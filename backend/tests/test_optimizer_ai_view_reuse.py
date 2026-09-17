"""
최적화 AI 뷰 재사용 흐름 — `portfolio_optimizer.run_ai_optimization`
(03b6a2a 재사용 · 988f80a 빠진 뷰를 채우지 않음). 최적화 역할이 요청한 검사다.

사용자 요구는 "다른 사용자가 이미 AI 분석한 종목은 다시 분석하지 말고, 하루 한 번
갱신" 이다. 틀리는 방향이 둘이다:

    덜 재사용한다   — 비용만 든다. 조용하다 (화면은 멀쩡하다).
    잘못 공유한다   — AI 가 답하지 않은 자리(대체 뷰 · 지어낸 0%)가 다음 초기화까지
                      **모든 사용자에게** 나간다. 역시 조용하다.

그래서 재는 것은 "AI·뉴스를 **몇 번, 어떤 종목으로** 불렀는가" 와 "무엇을 공유
저장소에 넘겼는가" 다. 화면 결과만 보면 둘 다 안 보인다.

## 가로채는 곳

바깥을 보는 네 곳 — `perplexity.search`(뉴스·AI 뷰가 같은 함수, `label` 로 구별),
`_fetch_prices` · `_gather_fundamentals` · `yfinance.download`(벤치마크). 한국이면
`markets.name_map_for` 도(뉴스 프롬프트의 회사명). 공용 캐시는 **실DB** 다 —
대문자 키·시장·정수 개월·만료가 전부 그 모듈에 있어서, 대역으로 바꾸면 그 규칙을
여기서 다시 적게 된다. `save_ai_views` 에는 인자를 기록하는 얇은 감시만 붙이고
실제 함수를 그대로 부른다.

Black-Litterman·프론티어 계산(`_run_pypfopt`)은 **대조군 하나에서만** 진짜로 돈다 —
"재사용한 뷰가 같은 결과를 낸다" 는 그 계산을 지나야 뜻이 있다. 나머지 흐름 검사는
그 자리에 **받은 뷰를 기록하는 대역**을 둔다: 재는 것이 "최적화에 어떤 뷰가
들어갔는가" 라 계산 결과가 필요 없고, 진짜 계산은 실행당 1초 가까이 들어 이 파일
하나가 게이트 시간을 두 배로 만들었다 (2026-09-17 실측: 전부 진짜 계산일 때 약
26초, 대조군만 진짜일 때 9.4초 — 그중 5.8초가 대조군).
"""
from __future__ import annotations

import json
import logging
import re

import numpy as np
import pandas as pd
import pytest

import backend.db as db
from backend.db import ai_view_cache as avc
from backend.services import portfolio_optimizer as po

_SUFFIXES = ("AAA", "BBB", "CCC", "DDD", "EEE")
_NEWS = "Optimizer news"
_VIEWS = "Optimizer AI views"
_OUTPUT_KEYS = ("optimizations", "posterior_returns", "posterior_source", "historical_returns",
                "effective_target_return", "frontier", "frontier_complete", "frontier_points",
                "correlation")


def _asked_sections(prompt: str) -> list[str]:
    """AI 뷰 프롬프트의 종목 섹션 머리(`■ 티커`)."""
    return re.findall(r"^■ (\S+)", prompt, flags=re.M)


def _asked_json_list(prompt: str) -> list[str]:
    """AI 뷰 프롬프트가 JSON 으로 답하라고 나열한 종목."""
    m = re.search(r"순수 JSON만 출력하라[^\n]*\n([^\n]+)\n", prompt)
    assert m, "the AI view prompt no longer lists tickers where this helper reads them"
    return re.findall(r'"([^"]+)"', m.group(1))


class _Pipeline:
    """`run_ai_optimization` 의 바깥을 전부 가로챈 판. 호출·저장·최적화 입력을 기록한다."""

    def __init__(self, tag: str, monkeypatch, real_optimizer: bool = False):
        self.tickers = [tag + s for s in _SUFFIXES]    # 길이가 같아 서로의 부분 문자열이 아니다
        self.calls: list[tuple[str, str]] = []
        self.saves: list[dict] = []
        self.optimized: list[dict] = []                 # 최적화가 받은 ai_views
        self.respond = self.complete

        idx = pd.bdate_range("2025-06-02", periods=320)
        rng = np.random.default_rng(7)
        self.frame = pd.DataFrame(
            {t: 100 * np.exp(np.cumsum(rng.normal(0.0004 * (i + 1), 0.012 + 0.002 * i, len(idx))))
             for i, t in enumerate(self.tickers)}, index=idx)
        bench = pd.DataFrame(
            {"Close": 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, len(idx))))}, index=idx)

        def fake_search(prompt, *, label="", **kw):
            self.calls.append((label, prompt))
            if label == _NEWS:
                return "뉴스 요약 — 가로챈 응답"
            if label == _VIEWS:
                return self.respond(_asked_sections(prompt))
            raise AssertionError(f"unexpected Perplexity call from the optimizer: {label!r}")

        real_save = avc.save_ai_views

        def watched_save(market, horizon_years, views):
            self.saves.append(dict(views))
            return real_save(market, horizon_years, views)

        monkeypatch.setattr("backend.services.perplexity.search", fake_search)
        monkeypatch.setattr(po, "_fetch_prices",
                            lambda tickers, period="1y": self.frame[[t for t in tickers if t in self.frame]])
        monkeypatch.setattr(po, "_gather_fundamentals", lambda tickers: {t: {} for t in tickers})
        monkeypatch.setattr("yfinance.download", lambda *a, **kw: bench)
        monkeypatch.setattr("backend.services.markets.name_map_for",
                            lambda tickers, market=None: {t: t for t in tickers})
        monkeypatch.setattr(avc, "save_ai_views", watched_save)

        real_run = po._run_pypfopt

        def recorded_run(prices, ai_views, *args, **kwargs):
            self.optimized.append({t: dict(v) for t, v in ai_views.items()})
            if real_optimizer:
                return real_run(prices, ai_views, *args, **kwargs)
            empty = {k: None for k in _OUTPUT_KEYS}
            return {**empty, "frontier_requested": None, "frontier_reason": None}

        monkeypatch.setattr(po, "_run_pypfopt", recorded_run)

    def view(self, t: str) -> dict:
        """종목마다 고정된 AI 뷰 — 어떤 바구니에서 물어도 같은 종목은 같은 답."""
        i = self.tickers.index(t)
        return {"expected_return": round(0.03 * (i + 1) - 0.05, 4), "confidence": 0.6,
                "sentiment": ("Bearish", "Neutral", "Bullish")[i % 3], "key_driver": f"근거 {t}"}

    def complete(self, asked: list[str]) -> str:
        return json.dumps({t: self.view(t) for t in asked}, ensure_ascii=False)

    def run(self, basket: list[str], **kw):
        c0, s0 = len(self.calls), len(self.saves)
        result = po.run_ai_optimization(basket, **kw)
        calls = self.calls[c0:]
        return result, calls, self.saves[s0:]


def _prompts(calls, label):
    return [p for l, p in calls if l == label]


@pytest.fixture
def pipeline(ai_view_tag, monkeypatch):
    return _Pipeline(ai_view_tag, monkeypatch)


@pytest.fixture
def pipeline_with_real_optimizer(ai_view_tag, monkeypatch):
    return _Pipeline(ai_view_tag, monkeypatch, real_optimizer=True)


def _without_cached_at(views: dict) -> dict:
    return {t: {k: v for k, v in view.items() if k != "cached_at"} for t, view in views.items()}


def _expire(tickers, market="US", months=12):
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE common_cache SET expires_at = NOW() - interval '1 second' "
                        "WHERE cache_type = ANY(%s)",
                        ([avc._cache_key(market, months, t) for t in tickers],))


# ── 같은 바구니 두 번 ─────────────────────────────────────────────────────────

def test_the_same_basket_again_calls_neither_news_nor_ai(pipeline):
    basket = pipeline.tickers[:3]

    first, calls1, _ = pipeline.run(basket)
    second, calls2, saves2 = pipeline.run(basket)

    # 전제 — 1회차는 정말로 물었다. 아니면 2회차의 0회가 아무것도 재지 않는다.
    assert len(_prompts(calls1, _VIEWS)) == 1 and len(_prompts(calls1, _NEWS)) == 1
    assert set(first["ai_view_source"].values()) == {"fresh"}

    assert calls2 == [], (
        f"the second run of the same basket called Perplexity {[l for l, _ in calls2]} -- "
        "every view was stored by the first run, so news and AI are both unused cost. "
        "(같은 바구니 2회차가 AI·뉴스를 다시 불렀다)"
    )
    assert second["ai_view_source"] == {t: "reused" for t in basket}
    assert _without_cached_at(pipeline.optimized[-1]) == pipeline.optimized[0], (
        "the optimizer did not receive the stored views on the second run"
    )
    assert all(not views for views in saves2), f"re-shared views that came from the cache: {saves2}"


def test_reused_views_give_the_same_optimization_as_fresh_ones(pipeline_with_real_optimizer):
    """대조군 — 저장소를 거친 뷰가 결과를 바꾸지 않는다. 그리고 이 비교가 **뷰에
    민감하다**는 것을 같이 보인다 — 다른 뷰는 다른 결과를 내야 한다. 안 그러면
    "같다" 는 뷰가 결과에 안 쓰여도 성립한다."""
    pipeline = pipeline_with_real_optimizer
    basket = pipeline.tickers[:3]
    fresh, _, _ = pipeline.run(basket)
    reused, calls, _ = pipeline.run(basket)
    assert calls == [] and set(reused["ai_view_source"].values()) == {"reused"}

    differ = [k for k in _OUTPUT_KEYS if fresh[k] != reused[k]]
    assert not differ, f"reusing stored views changed {differ}"
    assert _without_cached_at(reused["ai_views"]) == fresh["ai_views"]

    _expire(basket)
    pipeline.respond = lambda asked: json.dumps(
        {t: {**pipeline.view(t), "expected_return": -0.4, "sentiment": "Bearish"} for t in asked})
    other, calls, _ = pipeline.run(basket)
    assert len(_prompts(calls, _VIEWS)) == 1
    assert other["posterior_returns"] != fresh["posterior_returns"], (
        "different AI views produced the same posterior returns -- the equality above "
        "does not show that the views were used"
    )


# ── 겹치는 바구니 ─────────────────────────────────────────────────────────────

def test_an_overlapping_basket_asks_only_about_the_new_tickers(pipeline):
    a, b, c, d, e = pipeline.tickers
    pipeline.run([a, b, c])

    result, calls, saves = pipeline.run([b, c, d, e])

    news, views = _prompts(calls, _NEWS), _prompts(calls, _VIEWS)
    assert len(news) == 1 and len(views) == 1, [l for l, _ in calls]
    assert {t for t in pipeline.tickers if t in news[0]} == {d, e}, (
        "the news prompt carried tickers whose views were already stored -- they share "
        "the 12-ticker limit and the 3000-character news budget with the new ones"
    )
    assert set(_asked_sections(views[0])) == {d, e}
    assert set(_asked_json_list(views[0])) == {d, e}
    assert result["ai_view_source"] == {b: "reused", c: "reused", d: "fresh", e: "fresh"}
    assert [set(s) for s in saves] == [{d, e}]


@pytest.mark.parametrize("second", [{"market": "KR"}, {"holding_period_years": 0.5}],
                         ids=["다른-시장", "다른-기간"])
def test_views_are_not_reused_across_market_or_horizon(pipeline, second):
    basket = pipeline.tickers[:3]
    pipeline.run(basket)

    result, calls, _ = pipeline.run(basket, **second)

    views = _prompts(calls, _VIEWS)
    assert len(views) == 1 and set(_asked_sections(views[0])) == set(basket), (
        f"a {second} run reused views made for US/1y"
    )
    assert set(result["ai_view_source"].values()) == {"fresh"}
    market = second.get("market", "US")
    horizon = second.get("holding_period_years", 1.0)
    assert set(avc.get_ai_views(market, horizon, basket)) == set(basket), (
        "the views were not stored under their own market/horizon"
    )


# ── AI 가 답하지 않을 때 ──────────────────────────────────────────────────────

@pytest.mark.parametrize("raw", ["", "not json at all", "[]"], ids=["빈-응답", "JSON-아님", "배열"])
def test_a_total_ai_failure_uses_fallback_views_and_shares_nothing(pipeline, raw):
    basket = pipeline.tickers[:3]
    pipeline.respond = lambda asked: raw

    result, _, saves = pipeline.run(basket)

    assert result["ai_view_source"] == {t: "fallback" for t in basket}
    assert all("AI 분석 불가" in v["key_driver"] for v in pipeline.optimized[-1].values())
    assert all(not views for views in saves), (
        f"fallback views were handed to the shared cache: {saves} -- they would reach "
        "every user until the next reset, and a same-cycle save is never overwritten"
    )
    assert avc.get_ai_views("US", 1.0, basket) == {}

    pipeline.respond = pipeline.complete
    _, calls, _ = pipeline.run(basket)
    assert [set(_asked_sections(p)) for p in _prompts(calls, _VIEWS)] == [set(basket)], (
        "the next request did not ask the AI again -- the failure was remembered"
    )


@pytest.mark.parametrize("breakage", ["종목-누락", "값-N/A", "필드-누락"])
def test_a_partial_ai_answer_falls_back_only_for_the_broken_ticker(pipeline, breakage):
    a, b, c = pipeline.tickers[:3]

    def respond(asked):
        views = {t: pipeline.view(t) for t in asked}
        if b in views:
            if breakage == "종목-누락":
                del views[b]
            elif breakage == "값-N/A":
                views[b]["expected_return"] = "N/A"
            else:
                del views[b]["confidence"]
        return json.dumps(views, ensure_ascii=False)

    pipeline.respond = respond
    result, _, saves = pipeline.run([a, b, c])

    assert result["ai_view_source"] == {a: "fresh", b: "fallback", c: "fresh"}, (
        "one broken ticker must not cost the others their AI views"
    )
    given = pipeline.optimized[-1]
    for t in (a, c):
        assert given[t]["key_driver"] == f"근거 {t}", f"{t}: the AI view did not reach the optimizer"
    assert "AI 분석 불가" in given[b]["key_driver"]
    assert [set(s) for s in saves] == [{a, c}], f"saved {[sorted(s) for s in saves]}"
    assert set(avc.get_ai_views("US", 1.0, [a, b, c])) == {a, c}

    pipeline.respond = pipeline.complete
    _, calls, _ = pipeline.run([a, b, c])
    assert [set(_asked_sections(p)) for p in _prompts(calls, _VIEWS)] == [{b}]


# ── _generate_ai_views 는 빠진 것을 채우지 않는다 ─────────────────────────────

_FULL = {"expected_return": 0.12, "confidence": 0.6, "sentiment": "Bullish", "key_driver": "근거"}

_BROKEN_B = {
    "종목-없음": None,
    "null": "null",
    "expected_return-없음": {k: v for k, v in _FULL.items() if k != "expected_return"},
    "confidence-없음": {k: v for k, v in _FULL.items() if k != "confidence"},
    "sentiment-없음": {k: v for k, v in _FULL.items() if k != "sentiment"},
    "key_driver-없음": {k: v for k, v in _FULL.items() if k != "key_driver"},
    "N/A": {**_FULL, "expected_return": "N/A"},
    "불리언": {**_FULL, "confidence": True},
    "NaN": "NaN-literal",
    "sentiment-허용밖": {**_FULL, "sentiment": "Positive"},
    "key_driver-빈문자열": {**_FULL, "key_driver": "  "},
}


@pytest.mark.parametrize("case", sorted(_BROKEN_B))
def test_generate_ai_views_leaves_out_what_the_ai_did_not_answer(monkeypatch, caplog, case):
    """988f80a 이전에는 빠진 종목을 `0.0 · 0.5 · Neutral · ""` 로, 빠진 필드를 그
    필드의 기본값으로 채웠다 — "모름" 이 "보합 전망, 신뢰도 50%" 가 됐다 (§1.3 a)."""
    tickers = ["AAA", "BBB", "CCC"]
    body = {"AAA": _FULL, "CCC": {**_FULL, "expected_return": -0.9, "sentiment": "bearish"}}
    broken = _BROKEN_B[case]
    if isinstance(broken, dict):
        body["BBB"] = broken
    raw = json.dumps(body, ensure_ascii=False)
    if broken == "null":
        raw = raw[:-1] + ', "BBB": null}'
    elif broken == "NaN-literal":
        raw = raw[:-1] + ', "BBB": {"expected_return": NaN, "confidence": 0.6, ' \
                         '"sentiment": "Bullish", "key_driver": "x"}}'
    monkeypatch.setattr("backend.services.perplexity.search", lambda prompt, **kw: raw)
    caplog.set_level(logging.WARNING, logger=po.logger.name)

    views = po._generate_ai_views(tickers, {}, {}, "", 1.0, "US")

    assert "BBB" not in views, (
        f"{case}: BBB came back as {views.get('BBB')} -- a view the AI did not give was "
        "made up, and it would be shared with every user"
    )
    assert views["AAA"] == _FULL
    assert views["CCC"] == {**_FULL, "expected_return": -0.5, "sentiment": "Bearish"}, (
        "control: an out-of-range value is clipped and a lower-case sentiment accepted"
    )
    assert any("BBB" in r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING), (
        f"{case}: BBB was dropped without saying so"
    )

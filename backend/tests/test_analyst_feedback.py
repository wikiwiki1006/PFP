"""
포트폴리오 AI 피드백 — `POST /api/macro/analyst-feedback/auto` 와 그 프롬프트.

이 라우트를 부르는 테스트가 없었다. 그래서 f2b7802 가 `get_ai_analyst_feedback`
의 `market` 을 기본값 없는 인자로 바꿨을 때, 호출부에 `market=market` 이 빠진
상태로도 게이트가 초록이었다 — 실제로는 **두 시장 모두 500** 이었다 (통합이
68e6705 에서 고침). 여기서 두 시장이 200 이고 프롬프트가 그 시장의 변동성
지수를 그 이름으로 싣는지 고정한다:

    US  "- VIX 지수: 17.7 (정상)"      프레임의 ^VIX
    KR  "- VKOSPI 지수: 43.0 (위험)"   volatility_index("KR") — 프레임에 ^VIX 가 있어도

## "이번 호출이 만든 프롬프트" 만 본다

이 라우트는 **장이 닫혀 있으면 결과를 재사용한다** (`analysis_cache`). 캐시에서
나온 응답은 프롬프트를 새로 만들지 않으므로, 앞선 호출의 프롬프트를 이번 것으로
읽으면 틀린 결론이 나온다 — 통합이 측정 중 한 번 그렇게 한국에서 "VIX" 줄을 봤다.
그래서 가로챈 모델 호출을 **호출 단위로** 잘라 보고, 캐시 적중에서는 프롬프트가
**0개**라는 것을 따로 고정한다.

## 바깥만 가로챈다

라우터·지표 계산(`build_equity_curve` · `calculate_metrics`)·프롬프트 빌더는 진짜다.
가로채는 것은 보유·거래 로드, 가격 프레임, 섹터 등락, 한국 변동성 지수, 모델 호출,
분석 캐시, 시계와 장 개폐, KRX 관측 캘린더 — 전부 바깥을 보거나 실행 시각에 달린
것이다. 그래야 한국 `vix` 가 지표 계산을 거쳐 프롬프트에 닿는 길(ba60ceb → f2b7802)
전체가 이 검사 안에 든다.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers import macro
from backend.services import ai_analysis, market_calendar, market_data
from backend.services.auth import ai_feature_user

UID = "feedback-test-uid"
ET = ZoneInfo("America/New_York")
_NOW_ET = datetime(2026, 9, 15, 20, 0, tzinfo=ET)          # 미국 장 마감 뒤 = KST 09-16 09:00
_IDX = pd.bdate_range("2026-09-08", "2026-09-15")
_FRAME_VIX = 17.7
_VKOSPI = 43.02
_HOLDINGS = {
    "US": {"AAPL": {"q": 10, "avg": 150.0, "sector": "Technology"}, "CASH": {"q": 1000.0}},
    "KR": {"005930.KS": {"q": 10, "avg": 70000, "sector": "Technology"}, "CASH": {"q": 1_000_000}},
}
_FEEDBACK = "가로챈 모델 응답"


def _close_df(tickers, period="5d", ttl=60, include_market=True, fill=True):
    n = len(_IDX)
    columns = {}
    for t in tickers:
        base = 150.0 if t == "AAPL" else 70000.0
        columns[t] = [base * (1 + 0.004 * i) for i in range(n)]
    if include_market:
        columns["^GSPC"] = [6000.0 + 5 * i for i in range(n)]
        columns["^KS11"] = [3000.0 + 3 * i for i in range(n)]
        # 라우터의 프레임에는 시장과 무관하게 ^VIX 가 들어온다 — 한국에도.
        columns["^VIX"] = [20.0] * (n - 1) + [_FRAME_VIX]
    return pd.DataFrame(columns, index=_IDX)


class _World:
    def __init__(self, monkeypatch):
        self.prompts: list[str] = []
        self.cache: dict = {}
        self.cache_reads: list[str] = []
        self.vol_calls: list[str] = []
        self.loaded_for: list[tuple[str, str]] = []
        self.open = {"US": False, "KR": False}

        def load_holdings(uid, market="US"):
            self.loaded_for.append((uid, market))
            return {t: dict(h) for t, h in _HOLDINGS[market].items()}

        def volatility_index(market):
            self.vol_calls.append(market)
            return {"value": _VKOSPI, "prev_close": 44.40, "change_pct": -3.11,
                    "as_of": "2026-09-15", "label": "VKOSPI", "source": "investing"}

        def fake_model(prompt, *a, **kw):
            self.prompts.append(prompt)
            return _FEEDBACK

        def get_analysis(kind, key, user_id=None):
            self.cache_reads.append(key)
            return self.cache.get((kind, key, user_id))

        def save_analysis(kind, key, result, ttl_hours=24, user_id=None):
            self.cache[(kind, key, user_id)] = result

        start = _IDX[0].date() - timedelta(days=40)
        weekdays = frozenset(start + timedelta(days=i) for i in range(80)
                             if (start + timedelta(days=i)).weekday() < 5)

        monkeypatch.setattr(macro, "_load_holdings", load_holdings)
        monkeypatch.setattr(macro, "_load_trade_log", lambda uid, market="US": [])
        monkeypatch.setattr(macro, "get_close_df", _close_df)
        monkeypatch.setattr(macro, "get_sector_changes",
                            lambda market: {etf: 1.2 for _, etf in market_data.sector_etfs_for(market)})
        monkeypatch.setattr(market_data, "volatility_index", volatility_index)
        monkeypatch.setattr(ai_analysis, "ANTHROPIC_API_KEY", "test-key-never-sent")
        monkeypatch.setattr(ai_analysis, "call_claude", fake_model)
        monkeypatch.setattr("backend.db.reports_repo.get_analysis", get_analysis)
        monkeypatch.setattr("backend.db.reports_repo.save_analysis", save_analysis)
        monkeypatch.setattr(market_calendar, "now_et", lambda: _NOW_ET)
        monkeypatch.setattr(market_calendar, "is_us_market_open", lambda *a, **kw: self.open["US"])
        monkeypatch.setattr(market_calendar, "is_kr_market_open", lambda *a, **kw: self.open["KR"])
        monkeypatch.setattr(market_calendar, "_krx_trading_days", lambda: weekdays)

        app = FastAPI()
        app.include_router(macro.router)
        app.dependency_overrides[ai_feature_user] = lambda: {"uid": UID}
        self.client = TestClient(app, raise_server_exceptions=False)

    def call(self, market: str):
        """한 번 부르고, **이 호출이** 만든 프롬프트만 돌려준다."""
        before = len(self.prompts)
        r = self.client.post(f"/api/macro/analyst-feedback/auto?market={market}", json={})
        return r, self.prompts[before:]


@pytest.fixture
def world(monkeypatch):
    return _World(monkeypatch)


_VOL_LINE = re.compile(r"^- (\S+) 지수: ([^\n]+)$", re.M)


@pytest.mark.parametrize("market, line, vix", [
    ("US", "- VIX 지수: 17.7 (정상)", _FRAME_VIX),
    ("KR", "- VKOSPI 지수: 43.0 (위험)", _VKOSPI),
])
def test_each_market_answers_200_with_its_own_volatility_line(world, market, line, vix):
    r, prompts = world.call(market)

    assert r.status_code == 200, (
        f"{market}: {r.status_code} {r.text[:300]} -- this route had no test when a missing "
        "market= argument made it fail for both markets. (라우트가 200 이 아니다)"
    )
    body = r.json()
    assert body["from_cache"] is False and body["feedback"] == _FEEDBACK
    assert len(prompts) == 1, f"this call made {len(prompts)} prompts"
    assert [m.group(0) for m in _VOL_LINE.finditer(prompts[0])] == [line], (
        f"{market}: volatility line(s) {[m.group(0) for m in _VOL_LINE.finditer(prompts[0])]}, "
        f"expected [{line!r}] (the frame's ^VIX is {_FRAME_VIX} for both markets)"
    )
    assert body["metrics_snapshot"]["vix"] == vix
    assert world.vol_calls == (["KR"] if market == "KR" else [])
    assert world.loaded_for == [(UID, market)], "holdings were not loaded for the caller and market"


def test_a_cached_answer_makes_no_prompt(world):
    """장이 닫혀 있으면 두 번째 호출은 캐시에서 나온다 — 그때 프롬프트는 **없다.**
    측정할 때 앞 호출의 프롬프트를 이번 것으로 읽으면 안 되는 이유다."""
    first, made = world.call("KR")
    second, made_again = world.call("KR")

    assert len(made) == 1 and first.json()["from_cache"] is False
    assert second.status_code == 200 and second.json()["from_cache"] is True
    assert made_again == [], "a cached answer still produced a prompt"
    assert second.json()["feedback"] == first.json()["feedback"]


def test_one_markets_cached_answer_is_not_served_to_the_other(world):
    """캐시 키에 시장이 들어가야 한다 (§1.1). 안 들어가면 미국 피드백(VIX)이 한국
    화면에 나간다."""
    us, _ = world.call("US")
    kr, prompts = world.call("KR")

    assert us.json()["from_cache"] is False
    assert kr.json()["from_cache"] is False, "Korea was served the US cached feedback"
    assert len(prompts) == 1 and "- VKOSPI 지수: 43.0 (위험)" in prompts[0]


def test_while_the_market_is_open_every_call_makes_a_new_prompt(world):
    world.open["KR"] = True

    _, first = world.call("KR")
    second, again = world.call("KR")

    assert len(first) == 1 and len(again) == 1
    assert second.json()["from_cache"] is False
    assert world.cache_reads == [], "an open market read the closed-market cache"


# ── 프롬프트 — 등급은 적힌 숫자로 ──────────────────────────────────────────────

def _grade(prompt: str) -> tuple[str, str]:
    m = _VOL_LINE.search(prompt)
    assert m, "no volatility line in the prompt"
    shown = re.match(r"([\d.]+) \((\S+)\)$", m.group(2))
    assert shown, f"unexpected volatility line: {m.group(0)!r}"
    return shown.group(1), shown.group(2)


@pytest.mark.parametrize("market", ["US", "KR"])
def test_one_shown_number_has_one_grade(market):
    """원값으로 등급을 가르면 19.96 이 "20.0 (정상)", 20.0 이 "20.0 (주의)" 로 나가 같은
    숫자에 두 등급이 붙는다 (`_analyst_feedback_prompt` 주석). 경계마다 **같은 숫자로
    보이는 두 값**의 등급이 같은지 본다. 경계는 모듈에서 읽는다 — 값이 바뀌어도
    성질은 그대로다.

    대조군: 경계 조금 아래(다른 숫자로 보이는 값)는 등급이 달라야 한다. 안 그러면
    파싱이 늘 같은 등급을 읽어도 통과한다.
    """
    def prompt(v):
        return ai_analysis._analyst_feedback_prompt(v, 1.0, 0.5, "섹터", True, market)

    for floor, _ in ai_analysis._VOL_BANDS:
        just_below = round(floor - 0.04, 2)
        assert f"{just_below:.1f}" == f"{floor:.1f}", "test setup: both must display alike"
        assert _grade(prompt(just_below)) == _grade(prompt(floor)), (
            f"{market}: {just_below} and {floor} both show {floor:.1f} but got grades "
            f"{_grade(prompt(just_below))[1]!r} and {_grade(prompt(floor))[1]!r}"
        )
        assert _grade(prompt(round(floor - 0.2, 2)))[1] != _grade(prompt(floor))[1], (
            f"control: {floor - 0.2:.1f} and {floor:.1f} should fall in different bands"
        )

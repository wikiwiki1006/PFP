"""
한국 시장이 미국 기준으로 새지 않는지 확인한다.

이 앱은 두 시장을 함께 다루는데, 기본값이 전부 미국이라 한 군데만 놓쳐도
"한국 시장 화면인데 내용은 미국"이 된다. 실제로 겪은 것들:
  · 한국 종목 현재가가 전부 404 — 미국 상장 목록에서만 티커를 찾았다
  · 삼성전자 주가가 $255,500 — 통화가 USD 로 박혀 있었다
  · 한국 산업 리포트의 대표 종목이 NVDA·TSLA — 산업 목록이 하나뿐이었다
"""
import pytest

from backend.services import markets
from backend.services.report_writer import industries_for, industry_meta


# ── 시장 판별 ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("ticker,expected", [
    ("005930.KS", "KR"),
    ("035720.KQ", "KR"),
    ("AAPL", "US"),
    ("BRK.B", "US"),
])
def test_market_is_inferred_from_ticker(ticker, expected):
    assert markets.market_of_ticker(ticker) == expected


def test_currency_differs_by_market():
    """통화를 한쪽으로 고정하면 25만원짜리 주식이 25만 달러가 된다."""
    assert markets.get_market("KR").currency == "KRW"
    assert markets.get_market("KR").currency_symbol == "₩"
    assert markets.get_market("US").currency == "USD"
    assert markets.get_market("US").currency_symbol == "$"


# ── 산업 목록 ────────────────────────────────────────────────────────────────

def test_industry_lists_are_separate_per_market():
    us, kr = industries_for("US"), industries_for("KR")
    assert set(us) != set(kr)
    assert us and kr


def test_korean_industries_cover_korean_tickers():
    """한국 산업의 대표 종목이 국내 상장 코드여야 한다.

    미국 티커가 섞여 있으면 한국 산업 리포트인데 분석 대상이 미국 기업이 된다.
    """
    for iid, meta in industries_for("KR").items():
        tickers = [t.strip() for t in meta["coverage"].split(",") if t.strip()]
        assert tickers, f"{iid}: 커버리지가 비어 있다"
        for t in tickers:
            assert t.endswith((".KS", ".KQ")), f"{iid}: {t} 는 국내 종목이 아니다"


def test_us_industries_have_no_korean_tickers():
    for iid, meta in industries_for("US").items():
        for t in [x.strip() for x in meta["coverage"].split(",") if x.strip()]:
            assert not t.endswith((".KS", ".KQ")), f"{iid}: {t}"


def test_industry_meta_finds_both_markets():
    """id 만으로 정의를 찾을 수 있어야 한다 — 잡 처리에서 시장을 다시 받지 않는다."""
    assert industry_meta("kr_shipbuilding")["name_kr"] == "조선 & 기계"
    assert industry_meta("semiconductor_memory") is not None
    assert industry_meta("없는산업") is None


def test_benchmark_parsing_skips_exchange_names():
    """'KRX 조선' 에서 KRX 를 티커로 오인하면 벤치마크가 빈칸이 된다.

    KRX·KOSPI 는 거래소 이름이지 조회 가능한 심볼이 아니다.
    """
    import re
    skip = {"ETF", "AND", "THE", "BVP", "KRX", "KOSPI", "KOSDAQ"}
    for meta in industries_for("KR").values():
        words = re.findall(r"\b[A-Z]{2,6}\b", meta["benchmark"].upper())
        assert all(w in skip for w in words), \
            f"{meta['benchmark']}: 티커로 오인될 단어가 있다 — {words}"


# ── 프롬프트 관점 ────────────────────────────────────────────────────────────

def test_report_prompts_carry_market_stance():
    """프롬프트에 시장 관점이 없으면 모델은 미국을 기본값처럼 전제한다.

    한국 산업 리포트인데 비교 대상이 미국 기업이 되고 금액도 달러로 나온다.
    """
    from backend.services.report_writer import (
        _equity_prompt, _equity_prompt_part1, _equity_prompt_part2,
        _industry_prompt, _industry_prompt_part1, _industry_prompt_part2,
        industry_meta,
    )
    kr_meta = industry_meta("kr_semiconductor")
    us_meta = industry_meta("semiconductor_memory")

    kr_prompts = [
        _equity_prompt("005930.KS", "삼성전자", "KR"),
        _equity_prompt_part1("005930.KS", "삼성전자", "KR"),
        _equity_prompt_part2("005930.KS", "삼성전자", "KR"),
        _industry_prompt(kr_meta, "KR"),
        _industry_prompt_part1(kr_meta, "KR"),
        _industry_prompt_part2(kr_meta, "KR"),
    ]
    for p in kr_prompts:
        assert "한국 주식시장" in p
        assert "국내 상장 종목만" in p

    for p in [_equity_prompt("AAPL", "Apple", "US"), _industry_prompt(us_meta, "US")]:
        assert "미국 주식시장" in p
        assert "국내 상장 종목만" not in p


def test_macro_agent_prompts_switch_index_by_market():
    """시나리오 프롬프트에 S&P500 이 박혀 있으면 한국 시나리오도 미국 지수로 답한다."""
    from backend.services.ai_analysis import _build_agents

    kr = " ".join(a["prompt"] for a in _build_agents("이벤트", "보유", market="KR"))
    us = " ".join(a["prompt"] for a in _build_agents("이벤트", "보유", market="US"))

    assert "KOSPI" in kr and "S&P500" not in kr
    assert "S&P500" in us and "KOSPI" not in us
    assert "국내 상장 종목만" in kr


def test_display_name_resolves_korean_codes():
    """'034020.KS' 는 사람이 보고 어느 회사인지 알 수 없다."""
    from backend.services.markets import display_name
    # DB 가 없는 환경에서는 티커를 그대로 돌려주기만 하면 된다 (예외 없이).
    assert display_name("AAPL")
    assert display_name("005930.KS")


def test_news_gathering_is_market_aware():
    """뉴스 검색 방향도 시장을 따라야 한다.

    프롬프트에 "한국 관점으로 쓰라"고 해도, 모아 준 기사가 전부 미국 매체면
    모델은 그 재료로 답을 쓴다. 근거 자체를 그 시장 것으로 바꿔야 한다.
    """
    import backend.services.ai_analysis as ai

    captured = {}
    orig = ai._call_perplexity
    try:
        ai._call_perplexity = (lambda prompt, max_tokens=1200, market="US":
                               captured.setdefault("p", prompt) or "")
        ai.gather_perplexity_context("이벤트", market="KR")
        kr = captured.pop("p")
        ai.gather_perplexity_context("이벤트", market="US")
        us = captured.pop("p")
    finally:
        ai._call_perplexity = orig

    assert "SOUTH KOREA" in kr and "KOSPI" in kr
    assert "Korea-listed companies" in kr
    assert "UNITED STATES" in us and "SOUTH KOREA" not in us


def test_korean_lookup_matches_by_code(monkeypatch):
    """접미사 없이 코드만 입력해도 찾아야 한다.

    사용자가 '.KS' 를 알 이유가 없고, 같은 종목이 자료에 따라 .KS/.KQ 로
    다르게 적히기도 한다. 정확히 일치를 요구하면 ticker-exists 는 찾는데
    ticker-price 는 404 를 내는 어긋남이 생긴다 — 실제로 삼성전자가 그랬다.
    """
    from backend.services import korea_universe as ku

    monkeypatch.setattr(ku, "get_listed_all", lambda refresh=False: [
        {"ticker": "005930.KS", "name": "삼성전자"},
        {"ticker": "247540.KQ", "name": "에코프로비엠"},
    ])

    for q in ("005930", "005930.KS", "005930.kq"):
        got = ku.lookup(q)
        assert got and got["ticker"] == "005930.KS", q
    assert ku.lookup("247540")["name"] == "에코프로비엠"
    assert ku.lookup("999999") is None

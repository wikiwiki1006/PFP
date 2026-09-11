"""종목 상세의 포트폴리오 맥락은 시장별로 캐싱된다 (§1.1).

`_build_optimizer_block` 의 결과는 `get_holdings(uid, market=market)` 에 의존한다.
그런데 메모리 캐시 키가 `opt_ctx_{sym}_{uid}` 였다 — 시장이 없다. 먼저 조회한
시장의 답이 TTL(300초) 동안 다른 시장에 그대로 나갔다.

실측으로 재현됐던 것:

    000660.KS  ?market=KR  먼저 → in_portfolio=True  weight=48.79   (맞음)
    000660.KS  ?market=US       → in_portfolio=True  weight=48.79   ← 미국엔 없다

`MarketSwitch` 의 전체 새로고침으로도 막히지 않는다. 서버 메모리 캐시라
브라우저를 새로 고쳐도 남는다.

## 왜 test_market_isolation 이 못 잡았나

그 테스트는 "새 함수가 market 을 받는지" 를 호출부에서 검사한다. 이 라우트는
market 을 **제대로 받고 있었고** `get_holdings` 에 **제대로 넘기고 있었다.**
빠진 것은 캐시 키뿐이라 통과했다. 인자를 받는 것과 그 인자가 캐시 정체성에
들어가는 것은 다른 문제다.

## 왜 양방향을 다 보나

한 방향(KR 먼저 → US)만 검사하면 "항상 다른 값을 준다" 는 구현으로도
통과한다. 순서를 뒤집은 대조군을 같이 둔다.
"""
from __future__ import annotations

import pytest

import backend.routers.ticker as ticker_router


UID = "u-cache-market"
SYM = "000660.KS"


@pytest.fixture
def stub_holdings(monkeypatch):
    """시장별로 다른 보유를 돌려주고, 그 밖의 계산은 전부 눌러 둔다.

    이 테스트가 재는 것은 캐시 정체성 하나다 — 공분산 계산이나 가격 조회가
    섞이면 무엇 때문에 통과/실패했는지 알 수 없다.
    """
    holdings_by_market = {
        "KR": {"000660.KS": {"q": 20, "avg": 180_000, "sector": "Tech"}},
        "US": {"AAPL": {"q": 10, "avg": 150.0, "sector": "Tech"}},
    }

    def fake_get_holdings(uid, market="US"):
        return holdings_by_market[market]

    # 실제 최적화 계산 대신 어느 시장에서 불렸는지만 남긴다.
    def fake_context(sym, holdings, close_df, closes):
        return {"in_portfolio": sym in holdings, "tickers": sorted(holdings)}

    import backend.services.quant_metrics as qm
    import backend.db.portfolio_repo as repo
    import backend.services.market_data as md

    monkeypatch.setattr(repo, "get_holdings", fake_get_holdings)
    monkeypatch.setattr(qm, "compute_optimizer_context", fake_context)
    monkeypatch.setattr(md, "get_close_df",
                        lambda *a, **kw: _frame(sorted(
                            set(holdings_by_market["KR"]) | set(holdings_by_market["US"])
                            | {SYM, "^GSPC"})))

    # 캐시는 프로세스 전역이라 테스트마다 비운다 — 안 비우면 앞 테스트의
    # 값이 남아 이 검사 자체가 무의미해진다.
    md._cache.clear() if hasattr(md, "_cache") else None
    yield
    md._cache.clear() if hasattr(md, "_cache") else None


def _frame(tickers):
    import pandas as pd
    idx = pd.date_range("2026-01-01", periods=40, freq="D")
    return pd.DataFrame({t: [100.0 + i for i in range(40)] for t in tickers}, index=idx)


def _tickers_seen(market: str) -> list[str]:
    out = ticker_router._build_optimizer_block(SYM, UID, None, market)
    return out.get("tickers", [])


def test_kr_first_does_not_leak_into_us(stub_holdings):
    """KR 을 먼저 조회해도 US 는 US 보유를 받아야 한다."""
    kr = _tickers_seen("KR")
    us = _tickers_seen("US")

    assert kr == ["000660.KS"], f"KR 이 자기 보유를 못 받았다: {kr}"
    assert us == ["AAPL"], (
        f"US 가 KR 결과를 받았다: {us} -- 캐시 키에 시장이 없으면 먼저 조회한 "
        "시장의 답이 TTL 동안 그대로 나간다 (§1.1)"
    )


def test_us_first_does_not_leak_into_kr(stub_holdings):
    """순서를 뒤집은 대조군.

    이게 없으면 "항상 다른 값을 준다" 는 구현으로도 위 테스트가 통과한다.
    """
    us = _tickers_seen("US")
    kr = _tickers_seen("KR")

    assert us == ["AAPL"], f"US 가 자기 보유를 못 받았다: {us}"
    assert kr == ["000660.KS"], (
        f"KR 이 US 결과를 받았다: {kr} -- 반대 방향도 같은 이유로 샌다"
    )


def test_same_market_still_hits_the_cache(stub_holdings):
    """시장을 키에 넣되 캐시 자체는 살아 있어야 한다.

    키를 매번 다르게 만들어 버리면 누출은 막히지만 캐시가 무력해진다.
    이 블록은 캐시가 없으면 응답이 2.8초대로 늘어난다(주석에 실측이 있다).
    """
    calls = {"n": 0}
    import backend.services.quant_metrics as qm
    real = qm.compute_optimizer_context

    def counting(*a, **kw):
        calls["n"] += 1
        return real(*a, **kw)

    qm.compute_optimizer_context = counting
    try:
        _tickers_seen("KR")
        _tickers_seen("KR")
    finally:
        qm.compute_optimizer_context = real

    assert calls["n"] == 1, (
        f"같은 시장을 두 번 불렀는데 계산이 {calls['n']}회 돌았다 -- 캐시가 "
        "동작하지 않는다"
    )

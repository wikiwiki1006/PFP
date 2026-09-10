"""페어 탐색은 같은 시장 안에서만 이뤄져야 한다.

후보를 항상 S&P500 에서 뽑고 있어, 삼성바이오로직스의 페어로 AMAT·CME 같은
미국 종목이 나왔다. 통화도 거래시간도 다른 종목 사이의 상관은 허수다 —
KRX 가 닫혀 있는 동안 미국이 움직이면 스프레드가 저절로 벌어진 것처럼 보인다.
"""
import inspect

from backend.routers import signals


def test_pairs_auto_takes_market():
    assert "market" in inspect.signature(signals.pairs_auto).parameters, (
        "pairs_auto 가 market 을 받지 않는다 — 한국 종목에 미국 후보가 붙는다"
    )


def test_pairs_auto_uses_market_universe():
    """후보 목록을 시장별 유니버스에서 가져와야 한다."""
    src = inspect.getsource(signals.pairs_auto)
    assert "_universe_for(market)" in src, "후보를 시장별 유니버스에서 뽑지 않는다"
    assert "get_sp500_universe()" not in src, "여전히 S&P500 을 직접 쓴다"


def test_pairs_cache_key_is_market_scoped():
    """캐시 키에 시장이 없으면 한국 요청이 미국 결과를 받는다."""
    src = inspect.getsource(signals.pairs_auto)
    assert "pairs_auto::{market}::" in src


def test_pairs_rejects_foreign_ticker():
    """다른 시장의 티커로 조회하면 막는다."""
    src = inspect.getsource(signals.pairs_auto)
    assert "belongs_to(ticker, market)" in src

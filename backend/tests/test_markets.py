"""
미국·한국 시장 분리의 기본 규칙.

두 시장을 한 포트폴리오에 섞으면 평가액이 `800,000원 + 2,300달러` 처럼 단위
없이 더해져 수익률·비중·최적화가 전부 무의미해진다. 그래서 조회는 항상
market 으로 나누고, 티커가 엉뚱한 시장에 저장되지 않는지도 확인한다.
"""
import pytest

from backend.services.markets import (
    MARKETS, belongs_to, get_market, market_of_ticker, normalize,
)


def test_known_markets_exist():
    assert set(MARKETS) == {"US", "KR"}
    assert get_market("KR").currency == "KRW"
    assert get_market("US").currency == "USD"


def test_unknown_code_falls_back_to_us():
    """모르는 값이 왔다고 예외를 내면 기존 미국 사용자가 막힌다."""
    assert normalize(None) == "US"
    assert normalize("") == "US"
    assert normalize("JP") == "US"
    assert normalize("kr") == "KR"


@pytest.mark.parametrize("ticker,expected", [
    ("AAPL", "US"), ("^GSPC", "US"), ("BRK-B", "US"),
    ("005930.KS", "KR"), ("247540.KQ", "KR"),
    # 지수·환율은 접미사가 없어 별도로 인식해야 한다.
    ("^KS11", "KR"), ("^KQ11", "KR"), ("USDKRW=X", "KR"),
])
def test_ticker_market_detection(ticker, expected):
    assert market_of_ticker(ticker) == expected


def test_ticker_cannot_cross_markets():
    """미국 종목을 한국 포트폴리오에 담으면 통화가 섞인다 — 막아야 한다."""
    assert belongs_to("AAPL", "US")
    assert belongs_to("005930.KS", "KR")
    assert not belongs_to("AAPL", "KR")
    assert not belongs_to("005930.KS", "US")


def test_korea_sector_etfs_are_korean_tickers():
    """한국 섹터 ETF 에 미국 티커가 섞이면 등락률이 다른 시장 값이 된다."""
    for _, etf in get_market("KR").sector_etfs:
        assert market_of_ticker(etf) == "KR", etf


def test_korea_macro_series_exclude_stale_cpi():
    """FRED 한국 CPI 는 최신치가 오래돼 쓰지 않는다.
    한국 물가는 ECOS 에서 받는다 (services/korea_macro.py)."""
    assert "cpi_index" not in get_market("KR").fred_series

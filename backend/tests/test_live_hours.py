"""실시간 재수집 시간대는 시장마다 달라야 한다.

예전에는 '미국이냐 아니냐'로만 갈라 .KS/.KQ 가 암호화폐와 같은 취급을 받았다.
한국 주식은 09:00~15:30 에만 거래되는데 밤에도 주말에도 60초마다 다시 받아
왔다 — 하루 스무 시간 넘게 의미 없는 yfinance 호출이다.
"""
from datetime import datetime
from unittest.mock import patch
import zoneinfo

import pytest

from backend.services import live_quotes

KST = zoneinfo.ZoneInfo("Asia/Seoul")
ET  = zoneinfo.ZoneInfo("America/New_York")


def _can_move_at(ticker: str, when: datetime) -> bool:
    with patch("backend.services.market_calendar.now_kst", return_value=when.astimezone(KST)), \
         patch("backend.services.market_calendar.now_et",  return_value=when.astimezone(ET)):
        return live_quotes._can_move(ticker)


# 2026-09-08 은 화요일이고 미국·한국 모두 정상 거래일이다.
KR_SESSION   = datetime(2026, 9, 8, 10, 0,  tzinfo=KST)   # 한국 정규장
KR_AFTER     = datetime(2026, 9, 8, 18, 30, tzinfo=KST)   # 한국 마감 후
US_SESSION   = datetime(2026, 9, 8, 23, 30, tzinfo=KST)   # 미국 정규장
WEEKEND      = datetime(2026, 9, 5, 12, 0,  tzinfo=KST)   # 토요일


@pytest.mark.parametrize("ticker", ["005930.KS", "196170.KQ", "^KS11"])
def test_korean_tickers_move_during_krx_hours(ticker):
    assert _can_move_at(ticker, KR_SESSION) is True


@pytest.mark.parametrize("ticker", ["005930.KS", "196170.KQ", "^KS11"])
def test_korean_tickers_rest_after_krx_close(ticker):
    """한국장이 닫힌 뒤에는 다시 받아오지 않는다."""
    assert _can_move_at(ticker, KR_AFTER) is False
    assert _can_move_at(ticker, US_SESSION) is False
    assert _can_move_at(ticker, WEEKEND) is False


def test_us_ticker_follows_us_hours():
    assert _can_move_at("AAPL", US_SESSION) is True
    assert _can_move_at("AAPL", KR_SESSION) is False
    assert _can_move_at("AAPL", WEEKEND) is False


@pytest.mark.parametrize("ticker", ["BTC-USD", "USDKRW=X", "CL=F"])
def test_round_the_clock_assets_always_move(ticker):
    """24시간 자산은 시간대와 무관하게 계속 움직인다."""
    for when in (KR_SESSION, KR_AFTER, US_SESSION, WEEKEND):
        assert _can_move_at(ticker, when) is True


def test_korean_and_us_windows_do_not_collide():
    """같은 시각에 두 시장이 동시에 열려 있다고 판단하면 안 된다."""
    for when in (KR_SESSION, US_SESSION):
        assert _can_move_at("005930.KS", when) != _can_move_at("AAPL", when)

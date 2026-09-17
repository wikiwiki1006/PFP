"""
`price_series._is_behind_own_market` — 캘린더가 실패하면 열리되, 열렸다고 남긴다 (c1c75d8).

이 판정은 "종목의 기준일이 자기 시장의 마지막 확정 세션보다 뒤처졌는가" 다.
`portfolio_daily_change` 와 데일리 브리프가 이걸로 뒤처진 종목을 걸러낸다. 캘린더를
못 읽으면 `False`(뒤처지지 않음)를 준다 — 없는 결손을 만드는 것보다 낫다는 판단이
주석에 있다. 그 판단은 두되, §1.3(c) 가 요구하는 **열렸다는 기록**이 없었다: 캘린더가
깨지면 그 필터가 꺼진 채로 돌아도 아무도 몰랐다.

    반환             캘린더 예외 → False
    기록             첫 실패에 경고 한 줄
    소음             곧바로 이어지는 실패(종목마다 불린다)에는 추가 경고 없음
    다시 알림        한참 뒤의 실패는 다시 경고 — 한 번 남기고 영원히 조용하지 않다
    대조군           캘린더가 멀쩡하면 뒤처진 종목은 True (필터가 원래 뭔가를 거른다)

간격(10분)은 상수로 적지 않는다 — "곧바로" 와 "하루 뒤" 로 양쪽을 잰다. 모듈 전역
`_BEHIND_CHECK_ERR_AT` 은 테스트마다 비운다.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from backend.services import market_calendar, price_series

KST = ZoneInfo("Asia/Seoul")
ET = ZoneInfo("America/New_York")
_NOW = {"005930.KS": datetime(2026, 9, 17, 17, 0, tzinfo=KST),
        "AAPL": datetime(2026, 9, 17, 17, 0, tzinfo=ET)}
_STALE = pd.Timestamp("2026-09-10")


@pytest.fixture
def clock(monkeypatch):
    """모듈의 시계와 기록 시각을 테스트가 쥔다."""
    state = SimpleNamespace(t=1_000_000.0)
    monkeypatch.setattr(price_series, "_BEHIND_CHECK_ERR_AT", 0.0)
    monkeypatch.setattr(price_series, "_time", SimpleNamespace(time=lambda: state.t))
    return state


@pytest.fixture
def broken_calendar(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("calendar broke")

    monkeypatch.setattr(market_calendar, "last_completed_kr_session", boom)
    monkeypatch.setattr(market_calendar, "last_completed_session", boom)


def _warnings(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records
            if r.name == price_series._logger.name and r.levelno >= logging.WARNING]


@pytest.mark.parametrize("ticker", ["005930.KS", "AAPL"])
def test_a_calendar_failure_answers_not_behind_and_says_so_once(clock, broken_calendar, caplog, ticker):
    caplog.set_level(logging.WARNING, logger=price_series._logger.name)

    answers = [price_series._is_behind_own_market(ticker, _STALE, _NOW[ticker]) for _ in range(3)]

    assert answers == [False, False, False]
    assert len(_warnings(caplog)) == 1, (
        f"{len(_warnings(caplog))} warnings for 3 consecutive failures -- expected exactly one: "
        "none leaves the disabled filter invisible, one per ticker floods the log"
    )


def test_a_failure_much_later_warns_again(clock, broken_calendar, caplog):
    caplog.set_level(logging.WARNING, logger=price_series._logger.name)

    price_series._is_behind_own_market("005930.KS", _STALE, _NOW["005930.KS"])
    clock.t += 24 * 3600
    price_series._is_behind_own_market("005930.KS", _STALE, _NOW["005930.KS"])

    assert len(_warnings(caplog)) == 2, "the failure was reported once and then never again"


@pytest.mark.parametrize("ticker", ["005930.KS", "AAPL"])
def test_control_a_working_calendar_flags_a_behind_ticker(clock, monkeypatch, caplog, ticker):
    start = date(2026, 6, 1)
    weekdays = frozenset(start + timedelta(days=i) for i in range(200)
                         if (start + timedelta(days=i)).weekday() < 5)
    monkeypatch.setattr(market_calendar, "_krx_trading_days", lambda: weekdays)
    caplog.set_level(logging.WARNING, logger=price_series._logger.name)

    assert price_series._is_behind_own_market(ticker, _STALE, _NOW[ticker]) is True
    assert price_series._is_behind_own_market(ticker, pd.Timestamp("2026-09-17"), _NOW[ticker]) is False
    assert _warnings(caplog) == []

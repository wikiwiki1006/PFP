"""
NYSE 휴장일 계산이 실패하면 **닫힌 방향으로, 소리 내며** 실패한다 (e229171).

예전 `_holidays_for_year` 는 pandas 휴장일 규칙의 예외를 받아 로그 없이
`frozenset()` 을 돌려줬다. 빈 집합은 "휴장일이 없다" 는 단정이다:

    is_us_trading_day(2026-11-26 추수감사절)   → True
    next_close_reset(US, 11-25 17:00)          → 휴장일 11-26 16:30
    save_prices_to_db                          → 휴장일의 ffill 가짜 종가를 영구 저장 (§1.6)

그리고 `lru_cache` 가 그 빈 집합을 붙들어, 원인이 사라져도 프로세스가 끝날
때까지 계속 틀렸다. 로그는 한 줄도 없었다. 테스트 역할이
`AbstractHolidayCalendar.holidays` 를 망가뜨려 재현했고 통합이 고쳤다 — pandas 를
올리면 실제로 생길 수 있다 (venv 는 다섯 창이 공유한다, §7.6).

여기서는 망가뜨리는 자리를 같게 둔다: pandas 의 규칙 계산 자체.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import date, datetime
from unittest import mock
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from pandas.tseries.holiday import AbstractHolidayCalendar

from backend.services import market_calendar as cal

ET = ZoneInfo("America/New_York")
_THANKSGIVING_2026 = date(2026, 11, 26)


@contextmanager
def _broken_holiday_rules():
    """pandas 휴장일 계산을 망가뜨린다. 들어갈 때 연도 캐시를 비워 이미 계산된 해가
    망가진 규칙을 가리지 않게 하고, 나올 때는 **비우지 않는다** — 캐시에 실패가
    남지 않는다는 것이 재려는 성질 중 하나다."""
    cal._holidays_for_year.cache_clear()
    with mock.patch.object(AbstractHolidayCalendar, "holidays",
                           side_effect=RuntimeError("pandas holiday rules broke")), \
         mock.patch.object(cal, "_HOLIDAY_ERROR_LOGGED", set()):
        yield


@pytest.fixture(autouse=True)
def _fresh_year_cache():
    cal._holidays_for_year.cache_clear()
    yield
    cal._holidays_for_year.cache_clear()


def test_a_failed_calculation_raises_instead_of_declaring_no_holidays():
    with _broken_holiday_rules():
        with pytest.raises(RuntimeError):
            cal.is_us_trading_day(_THANKSGIVING_2026)
        with pytest.raises(RuntimeError):
            cal.next_close_reset("US", datetime(2026, 11, 25, 17, 0, tzinfo=ET))


def test_the_failure_is_logged_once_per_year(caplog):
    """저장 가드는 행마다 부른다. 호출마다 오류를 쏟으면 그 로그는 안 읽힌다."""
    caplog.set_level(logging.ERROR, logger=cal.logger.name)
    with _broken_holiday_rules():
        for d in (date(2026, 11, 25), _THANKSGIVING_2026, date(2026, 11, 27)):
            with pytest.raises(RuntimeError):
                cal.is_us_trading_day(d)
        with pytest.raises(RuntimeError):
            cal.is_us_trading_day(date(2027, 1, 4))

    errors = [r for r in caplog.records if r.name == cal.logger.name and r.levelno >= logging.ERROR]
    assert len(errors) == 2, [r.getMessage() for r in errors]
    assert "2026" in errors[0].getMessage() and "2027" in errors[1].getMessage()


def test_recovery_needs_no_restart():
    """예외는 `lru_cache` 에 남지 않는다 — 원인이 고쳐진 다음 호출부터 맞다."""
    with _broken_holiday_rules():
        with pytest.raises(RuntimeError):
            cal.is_us_trading_day(_THANKSGIVING_2026)

    assert cal.is_us_trading_day(_THANKSGIVING_2026) is False
    assert cal.is_us_trading_day(date(2026, 11, 27)) is True


_WORKING = "__TEST_HOLIDAY_OK__"
_BROKEN = "__TEST_HOLIDAY_BROKEN__"
# 지난 추수감사절 주간. 실행 시각보다 과거라 '종가 미확정' 가드에는 안 걸린다.
_WEEK = [date(2025, 11, 25), date(2025, 11, 26), date(2025, 11, 27), date(2025, 11, 28)]


@pytest.fixture
def stored(live_db):
    """이 테스트의 두 티커만 읽고, 앞뒤로 지운다."""
    from backend.db import get_conn

    def purge():
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM market_prices WHERE ticker IN (%s, %s)", (_WORKING, _BROKEN))

    def read(ticker):
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT price_date FROM market_prices WHERE ticker = %s ORDER BY 1",
                            (ticker,))
                return [r[0] for r in cur.fetchall()]

    purge()
    yield read
    purge()


def _ffilled_week(ticker: str) -> pd.DataFrame:
    return pd.DataFrame({ticker: [100.0, 101.0, 101.0, 102.0]},        # 11-27 은 ffill 값
                        index=pd.to_datetime([d.isoformat() for d in _WEEK]))


def test_the_price_write_guard_closes_when_holidays_cannot_be_computed(stored, caplog):
    """§1.6 의 가드는 휴장일을 모르면 **아무것도 쓰지 않고** 말한다.

    대조군을 먼저 잰다 — 규칙이 멀쩡하면 휴장일만 빠진 3행이 들어간다. 없으면
    "아무것도 저장하지 않는" 구현이 닫힌 가드로 보인다.
    """
    from backend.db import market_cache

    assert cal.uses_us_session_calendar(_WORKING) and cal.uses_us_session_calendar(_BROKEN)

    written = market_cache.save_prices_to_db(_ffilled_week(_WORKING))
    assert written == 3 and stored(_WORKING) == [_WEEK[0], _WEEK[1], _WEEK[3]], (
        f"control: with working rules the guard keeps the 3 sessions, got {written} "
        f"{stored(_WORKING)}"
    )

    caplog.set_level(logging.WARNING, logger=market_cache.logger.name)
    with _broken_holiday_rules():
        written = market_cache.save_prices_to_db(_ffilled_week(_BROKEN))

    assert written == 0 and stored(_BROKEN) == [], (
        f"with the holiday rules broken the guard wrote {stored(_BROKEN)} -- including "
        "Thanksgiving's forward-filled close, which is stored permanently. "
        "(휴장일을 모르는 가드가 열렸다)"
    )
    said = [r.getMessage() for r in caplog.records
            if r.name == market_cache.logger.name and r.levelno >= logging.WARNING]
    assert said, "the guard refused to write without saying why"

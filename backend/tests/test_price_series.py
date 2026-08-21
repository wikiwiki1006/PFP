"""
backend/tests/test_price_series.py
──────────────────────────────────
일변동률 primitive 회귀 테스트. 네트워크·DB 불필요 (합성 프레임만 사용).

각 테스트는 "0% 변동률" 버그의 구체적 실패 모드 하나씩에 대응한다.
"""
from datetime import datetime

import pandas as pd
import pytest

from backend.services.market_calendar import (
    is_us_trading_day, last_completed_session, us_market_status, uses_us_session_calendar,
)
from backend.services.price_series import daily_change, last_price, portfolio_daily_change

ET_TZ = __import__("zoneinfo").ZoneInfo("America/New_York")


def _et(y, m, d, hh=0, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=ET_TZ)


def _frame(dates, values, col="NVDA"):
    return pd.DataFrame({col: values}, index=pd.to_datetime(dates))


# ── 캘린더 ────────────────────────────────────────────────────────────────────

def test_2026_nyse_holidays_are_not_trading_days():
    for d in ["2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
              "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25"]:
        assert not is_us_trading_day(pd.Timestamp(d).date()), d


def test_normal_weekdays_are_trading_days():
    for d in ["2026-08-03", "2026-08-04", "2026-07-31"]:
        assert is_us_trading_day(pd.Timestamp(d).date()), d


def test_holiday_reports_closed_not_open():
    # 예전 구현은 주말만 확인해 공휴일에 '개장'으로 오판했다
    assert us_market_status(_et(2026, 7, 3, 11, 0)) == "closed"


def test_market_status_transitions():
    assert us_market_status(_et(2026, 8, 4, 8, 0))  == "pre"
    assert us_market_status(_et(2026, 8, 4, 11, 0)) == "open"
    assert us_market_status(_et(2026, 8, 4, 17, 0)) == "post"
    assert us_market_status(_et(2026, 8, 8, 11, 0)) == "closed"   # 토요일


def test_last_completed_session_skips_weekend_and_holiday():
    # 월요일 장전 → 직전 금요일
    assert last_completed_session(_et(2026, 8, 3, 8, 0)) == pd.Timestamp("2026-07-31").date()
    # 7/3(금)이 휴장이므로 7/6(월) 장전 → 7/2(목)
    assert last_completed_session(_et(2026, 7, 6, 8, 0)) == pd.Timestamp("2026-07-02").date()


def test_ticker_calendar_classification():
    assert uses_us_session_calendar("NVDA")
    assert uses_us_session_calendar("^GSPC")
    assert not uses_us_session_calendar("BTC-USD")
    assert not uses_us_session_calendar("USDKRW=X")
    assert not uses_us_session_calendar("GC=F")
    assert not uses_us_session_calendar("^KS11")
    assert not uses_us_session_calendar("005930.KS")


# ── primitive 핵심 동작 ────────────────────────────────────────────────────────

def test_ffill_duplicates_do_not_produce_zero():
    """실제 버그: 금요일 종가가 월·화로 복제돼 변동률이 0%가 됐다."""
    df = _frame(
        ["2026-07-30", "2026-07-31", "2026-08-03", "2026-08-04"],
        [195.04, 200.75, 200.75, 200.75],
    )
    # 08-04 장전 → 08-04 행은 캘린더 컷오프로 잘린다
    dc = daily_change(df, "NVDA", now=_et(2026, 8, 4, 8, 0))
    assert dc is not None
    assert dc.as_of == pd.Timestamp("2026-08-03")
    # 08-03 은 실제 거래일이므로 그 값을 신뢰한다 (DB 복구가 필요한 케이스)
    assert dc.prev_as_of == pd.Timestamp("2026-07-31")


def test_genuinely_flat_session_returns_zero_not_skipped():
    """마지막 두 '서로 다른 값'이 아니라 '서로 다른 날짜'를 써야 한다.

    값 기준이면 진짜 보합 마감한 날을 건너뛰고 전날 변동률을 잘못 보고한다.
    """
    df = _frame(["2026-07-30", "2026-07-31", "2026-08-03"], [100.0, 110.0, 110.0])
    dc = daily_change(df, "NVDA", now=_et(2026, 8, 4, 8, 0))
    assert dc is not None
    assert dc.chg_pct == 0.0                       # 보합은 진짜 0%
    assert dc.as_of == pd.Timestamp("2026-08-03")


def test_non_session_rows_are_filtered_by_calendar():
    """주말·공휴일 유령 행은 DB에 남아 있어도 캘린더로 제거된다."""
    df = _frame(
        ["2026-07-30", "2026-07-31", "2026-08-01", "2026-08-02"],   # 08-01 토, 08-02 일
        [195.04, 200.75, 200.75, 200.75],
    )
    dc = daily_change(df, "NVDA", now=_et(2026, 8, 3, 8, 0))
    assert dc is not None
    assert dc.as_of == pd.Timestamp("2026-07-31")
    assert dc.prev_as_of == pd.Timestamp("2026-07-30")
    assert dc.chg_pct == pytest.approx(2.928, abs=0.01)


def test_premarket_today_row_is_truncated():
    """장전에 오늘 날짜 행이 있어도 아직 거래 전이므로 인정하지 않는다."""
    df = _frame(["2026-07-31", "2026-08-03", "2026-08-04"], [200.75, 205.00, 205.00])
    dc = daily_change(df, "NVDA", now=_et(2026, 8, 4, 8, 0))   # 08:00 ET = 장전
    assert dc.as_of == pd.Timestamp("2026-08-03")
    # 09:30 이후면 오늘 행이 유효해진다
    dc2 = daily_change(df, "NVDA", now=_et(2026, 8, 4, 11, 0))
    assert dc2.as_of == pd.Timestamp("2026-08-04")


def test_live_price_uses_previous_real_close():
    """장중: 실시간 가격 vs 오늘 이전의 마지막 실제 종가."""
    df = _frame(["2026-07-31", "2026-08-03"], [200.75, 210.00])
    dc = daily_change(df, "NVDA", live_price=214.20, now=_et(2026, 8, 4, 11, 0))
    assert dc.is_live
    assert dc.price == 214.20
    assert dc.prev_close == 210.00
    assert dc.prev_as_of == pd.Timestamp("2026-08-03")
    assert dc.chg_pct == pytest.approx(2.0, abs=0.01)


def test_crypto_weekend_rows_are_preserved():
    """24/7 자산은 주말에도 실제로 거래되므로 필터링하면 안 된다 (R4)."""
    df = _frame(
        ["2026-07-31", "2026-08-01", "2026-08-02"],
        [62813.7, 62763.3, 63482.0],
        col="BTC-USD",
    )
    dc = daily_change(df, "BTC-USD", now=_et(2026, 8, 2, 12, 0))
    assert dc is not None
    assert dc.as_of == pd.Timestamp("2026-08-02")
    assert dc.prev_as_of == pd.Timestamp("2026-08-01")
    assert dc.chg_pct > 0


def test_single_observation_returns_none_but_price_survives():
    """관측치가 1개면 변동률은 None. 단, 가격은 반드시 살아 있어야 한다.

    여기서 가격을 0으로 떨어뜨리면 시가총액·비중·손익이 전부 왜곡된다.
    """
    df = _frame(["2026-08-03"], [123.45])
    assert daily_change(df, "NVDA", now=_et(2026, 8, 4, 8, 0)) is None
    assert last_price(df, "NVDA", now=_et(2026, 8, 4, 8, 0)) == 123.45


def test_unknown_ticker_is_safe():
    df = _frame(["2026-08-03"], [1.0])
    assert daily_change(df, "MISSING") is None
    assert last_price(df, "MISSING") is None


# ── 포트폴리오 합산 ────────────────────────────────────────────────────────────

def test_portfolio_total_equals_sum_of_parts():
    """1Day 값은 화면에 보이는 종목별 행의 합과 정의상 일치해야 한다."""
    idx = ["2026-07-30", "2026-07-31"]
    df = pd.DataFrame(
        {"AAA": [100.0, 110.0], "BBB": [50.0, 49.0]},
        index=pd.to_datetime(idx),
    )
    holdings = {"AAA": {"q": 10}, "BBB": {"q": 20}, "CASH": {"q": 1000.0}}
    val, pct, as_of = portfolio_daily_change(holdings, df, now=_et(2026, 8, 3, 8, 0))

    expected_val = 10 * (110.0 - 100.0) + 20 * (49.0 - 50.0)   # +100 - 20 = +80
    assert val == pytest.approx(expected_val, abs=0.01)
    base = 10 * 100.0 + 20 * 50.0 + 1000.0                     # 1000 + 1000 + 1000
    assert pct == pytest.approx(expected_val / base * 100, abs=0.01)
    assert as_of == pd.Timestamp("2026-07-31")


def test_portfolio_ignores_uncomputable_ticker_but_counts_its_value():
    """변동률을 못 구하는 종목이 있어도 %가 과장되면 안 된다."""
    df = pd.DataFrame(
        {"AAA": [100.0, 110.0], "NEW": [None, 200.0]},
        index=pd.to_datetime(["2026-07-30", "2026-07-31"]),
    )
    holdings = {"AAA": {"q": 10}, "NEW": {"q": 5}, "CASH": {"q": 0}}
    val, pct, _ = portfolio_daily_change(holdings, df, now=_et(2026, 8, 3, 8, 0))
    assert val == pytest.approx(100.0, abs=0.01)          # AAA 만 기여
    # 분모에는 NEW 의 보유가치(5×200=1000)도 포함된다
    assert pct == pytest.approx(100.0 / (1000.0 + 1000.0) * 100, abs=0.01)


def test_portfolio_empty_returns_none_not_zero():
    df = pd.DataFrame({"AAA": [100.0]}, index=pd.to_datetime(["2026-07-31"]))
    val, pct, _ = portfolio_daily_change({"AAA": {"q": 10}}, df, now=_et(2026, 8, 3, 8, 0))
    assert val is None and pct is None


# ── 카우프만 효율성 비율(ER) 국면 판단 ────────────────────────────────────────

def test_er_perfect_uptrend_is_one():
    """매일 같은 폭으로 오르면 경로=직선거리 → ER=1, 상승 판정."""
    from backend.services.trading_signals import detect_regime_er, efficiency_ratio
    p = pd.Series(range(100, 160), index=pd.bdate_range("2026-01-01", periods=60), dtype=float)
    er = efficiency_ratio(p, window=20)
    assert er.iloc[-1] == pytest.approx(1.0, abs=1e-9)
    assert detect_regime_er(p, window=20)["current_regime"] == "Bull"


def test_er_perfect_downtrend_is_one_but_bear():
    """방향만 반대 — ER 은 절대값이라 1, 순변동 부호로 하락 판정."""
    from backend.services.trading_signals import detect_regime_er
    p = pd.Series(range(160, 100, -1), index=pd.bdate_range("2026-01-01", periods=60), dtype=float)
    r = detect_regime_er(p, window=20)
    assert r["current_er"] == pytest.approx(1.0, abs=1e-9)
    assert r["current_regime"] == "Bear"


def test_er_zigzag_is_sideways():
    """위아래로 요동치며 제자리 → 경로는 길고 직선거리는 0 → ER≈0, 횡보."""
    from backend.services.trading_signals import detect_regime_er
    vals = [100 + (5 if i % 2 else 0) for i in range(60)]
    p = pd.Series(vals, index=pd.bdate_range("2026-01-01", periods=60), dtype=float)
    r = detect_regime_er(p, window=20)
    assert r["current_er"] < 0.3
    assert r["current_regime"] == "Sideways"


def test_er_threshold_is_respected():
    """임계값을 낮추면 같은 시계열도 추세로 판정된다."""
    from backend.services.trading_signals import detect_regime_er
    # 완만한 상승 + 노이즈 → 중간 정도 ER
    base = [100 + i * 0.4 + (1.5 if i % 3 == 0 else -1.2) for i in range(60)]
    p = pd.Series(base, index=pd.bdate_range("2026-01-01", periods=60), dtype=float)
    er = detect_regime_er(p, window=20)["current_er"]
    strict = detect_regime_er(p, window=20, threshold=min(0.9, er + 0.2))
    loose  = detect_regime_er(p, window=20, threshold=max(0.05, er - 0.05))
    assert strict["current_regime"] == "Sideways"
    assert loose["current_regime"] in ("Bull", "Bear")


def test_er_flat_series_is_sideways_not_crash():
    """가격이 전혀 안 움직이면 분모가 0 — 0으로 나누지 않고 횡보로 처리."""
    from backend.services.trading_signals import detect_regime_er
    p = pd.Series([100.0] * 60, index=pd.bdate_range("2026-01-01", periods=60))
    r = detect_regime_er(p, window=20)
    assert r["current_er"] == 0.0
    assert r["current_regime"] == "Sideways"


def test_er_requires_enough_data():
    from backend.services.trading_signals import detect_regime_er
    p = pd.Series([100.0] * 10, index=pd.bdate_range("2026-01-01", periods=10))
    with pytest.raises(ValueError):
        detect_regime_er(p, window=20)

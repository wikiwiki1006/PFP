"""
데일리 브리프의 기준일·등락 — `daily_report._fetch_price_data` (e0545d3). 리포트 품질 역할 요청.

예전 구현은 세션과 등락을 스스로 정했다. yf.download 프레임을 **합집합 날짜로
ffill** 해 마지막 행을 세션으로 삼고, "장중이면 전날" 을 UTC 날짜로 골랐다.
그래서 두 가지가 조용히 틀렸다:

    휴장일에 전 종목 +0.00%    — 한 티커라도 휴장일에 행이 있으면(원/달러 · 24시간
                                 자산 · 야후 유령행) 그날이 세션이 되고 나머지는
                                 전일 값이 복제됐다
    한 세션 늦음                — KST 새벽·ET 저녁에 UTC 날짜가 그 시장 날짜와 갈렸다

지금은 세션을 `market_calendar` 에, 등락을 `price_series.daily_change`(티커별 마지막
두 실제 관측치)에 묻는다. 여기서 경우마다 고정한다. **날짜는 거래소 사실이라 구체
값으로**, 등락은 합성 프레임에서 기대값을 계산해 비교한다.

가로채는 곳: `market_data.get_close_df`(프레임) · `market_calendar._krx_trading_days`
(관측 캘린더 — 휴장일 제외, **미래를 모른다**) · `_volatility_entry`(VKOSPI 조회).
시각은 `now=` 로 준다.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from backend.services import daily_report as dr
from backend.services import market_calendar as mc

nan = np.nan
KR_H = {"005930.KS": {"q": 10, "avg": 70000, "sector": "Tech"}, "CASH": {"q": 1_000_000}}
US_H = {"AAPL": {"q": 10, "avg": 200.0, "sector": "Tech"}, "CASH": {"q": 5000.0}}


def _krx(until: date, holidays=()) -> frozenset:
    start = date(2026, 6, 1)
    return frozenset(start + timedelta(days=i) for i in range((until - start).days + 1)
                     if (start + timedelta(days=i)).weekday() < 5
                     and (start + timedelta(days=i)) not in set(holidays))


class _Brief:
    def __init__(self, monkeypatch):
        self.monkeypatch = monkeypatch
        self.close_df_calls = 0

    def fetch(self, holdings, market, columns, dates, now_iso, krx_holidays=(), krx_until=None):
        frame = pd.DataFrame(columns, index=pd.DatetimeIndex(dates), dtype=float)
        now = datetime.fromisoformat(now_iso)

        def get_close_df(*args, **kwargs):
            self.close_df_calls += 1
            return frame.copy()

        mp = self.monkeypatch
        mp.setattr(mc, "_krx_trading_days", lambda: _krx(krx_until or now.date(), krx_holidays))
        mp.setattr("backend.services.market_data.get_close_df", get_close_df)
        mp.setattr(dr, "_volatility_entry", lambda market: None)
        return frame, dr._fetch_price_data(holdings, market, now=now)


@pytest.fixture
def brief(monkeypatch):
    return _Brief(monkeypatch)


def _chg(frame, ticker, day, prev) -> float:
    return round((frame.loc[day, ticker] / frame.loc[prev, ticker] - 1) * 100, 2)


def _session(out) -> str:
    return out["__date"][:13]          # "2026년 09월 23일" — 뒤의 요일 표기는 로캘을 탄다


# ── 휴장일이 세션이 되지 않는다 ──────────────────────────────────────────────

def test_after_chuseok_the_session_is_the_last_open_day_with_real_changes(brief):
    """추석 뒤 월요일 08:00 KST — 09-24·25 휴장, 원/달러만 매일 값이 있다.
    예전: 기준일 09-25(휴장일) · 005930·KOSPI +0.00%."""
    frame, out = brief.fetch(
        KR_H, "KR",
        {"005930.KS": [70000, 71000, 72000, nan, nan, nan],
         "^KS11": [7000, 7010, 7034, nan, nan, nan],
         "^KQ11": [780, 781, 782, nan, nan, nan],
         "USDKRW=X": [1380, 1381, 1382, 1383, 1384, 1385]},
        ["2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25", "2026-09-28"],
        "2026-09-28T08:00:00+09:00",
        krx_holidays=[date(2026, 9, 24), date(2026, 9, 25)], krx_until=date(2026, 9, 25))

    assert _session(out) == "2026년 09월 23일"
    assert out["005930.KS"]["chg_pct"] == _chg(frame, "005930.KS", "2026-09-23", "2026-09-22") != 0.0
    assert out["__KOSPI"]["chg_pct"] == _chg(frame, "^KS11", "2026-09-23", "2026-09-22") != 0.0
    assert out["__missing"] == {}


_TG_DATES = ["2026-11-20", "2026-11-23", "2026-11-24", "2026-11-25"]
_TG = {"AAPL": [228, 229, 230, 232], "SPY": [555, 556, 557, 561],
       "^VIX": [16, 17, 17.7, 15.1], "^TNX": [4.1, 4.15, 4.18, 4.22]}


@pytest.mark.parametrize("shape", ["휴장일-행-없음", "BTC-USD-보유", "SPY-유령행"])
def test_the_day_after_thanksgiving_reports_the_last_us_session(brief, shape):
    """추수감사절(11-26) 다음 날 08:00 ET. 24시간 자산이나 유령행 하나가 휴장일을
    세션으로 만들던 경우. 예전: 기준일 11-26 · AAPL·SPY 전부 +0.00%."""
    holdings, columns, dates = dict(US_H), dict(_TG), list(_TG_DATES)
    if shape == "BTC-USD-보유":
        columns = {**{k: v + [nan, nan] for k, v in _TG.items()},
                   "BTC-USD": [90000, 91000, 92000, 93000, 94000, 95000]}
        dates = _TG_DATES + ["2026-11-26", "2026-11-27"]
        holdings["BTC-USD"] = {"q": 0.1, "avg": 80000.0, "sector": "Crypto"}
    elif shape == "SPY-유령행":
        columns = {**{k: v + [nan] for k, v in _TG.items()}, "SPY": _TG["SPY"] + [561]}
        dates = _TG_DATES + ["2026-11-26"]

    frame, out = brief.fetch(holdings, "US", columns, dates, "2026-11-27T08:00:00-05:00")

    assert _session(out) == "2026년 11월 25일"
    assert out["AAPL"]["chg_pct"] == _chg(frame, "AAPL", "2026-11-25", "2026-11-24") != 0.0
    assert out["__SPY"]["chg_pct"] == _chg(frame, "SPY", "2026-11-25", "2026-11-24") != 0.0


# ── 한 세션 늦지 않는다 ───────────────────────────────────────────────────────

_KR_WEEK = {"005930.KS": [70000, 71000, 72000, 73000], "^KS11": [7000, 7010, 7020, 7034],
            "^KQ11": [780, 781, 782, 783], "USDKRW=X": [1380, 1381, 1382, 1383]}
_KR_DATES = ["2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17"]
_US_WEEK = {"AAPL": [228, 229, 230, 231], "SPY": [555, 556, 557, 560],
            "^VIX": [16, 17, 17.7, 15.66], "^TNX": [4.1, 4.15, 4.18, 4.2]}
_US_DATES = ["2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17"]


def _through(columns, dates, last):
    n = dates.index(last) + 1
    return {k: v[:n] for k, v in columns.items()}, dates[:n]


@pytest.mark.parametrize("case, market, now_iso, expected", [
    pytest.param(case, market, now_iso, expected, id=case)
    for case, market, now_iso, expected in (
        ("KR-평일-08시", "KR", "2026-09-18T08:00:00+09:00", "2026년 09월 17일"),
        ("KR-16시-당일-종가-있음", "KR", "2026-09-18T16:00:00+09:00", "2026년 09월 18일"),
        ("US-09시-장전", "US", "2026-09-17T09:00:00-04:00", "2026년 09월 16일"),
        ("US-09시-장전-유령행", "US", "2026-09-17T09:00:00-04:00", "2026년 09월 16일"),
        ("US-17시-마감-뒤", "US", "2026-09-17T17:00:00-04:00", "2026년 09월 17일"),
    )
])
def test_the_session_is_the_markets_last_completed_one(brief, case, market, now_iso, expected):
    """예전: KR 09-18 08:00 KST 에 09-16, US 09-17 17:00 ET 에 09-16 (UTC 날짜 기준)."""
    if market == "KR":
        columns, dates = dict(_KR_WEEK), list(_KR_DATES)
        if case == "KR-16시-당일-종가-있음":
            columns = {k: v + [v[-1] * 1.01] for k, v in columns.items()}
            dates = dates + ["2026-09-18"]
        holdings = KR_H
    else:
        holdings = US_H
        if case == "US-09시-장전":
            columns, dates = _through(_US_WEEK, _US_DATES, "2026-09-16")
        elif case == "US-09시-장전-유령행":
            base, dates = _through(_US_WEEK, _US_DATES, "2026-09-16")
            columns, dates = {k: v + [v[-1]] for k, v in base.items()}, dates + ["2026-09-17"]
        else:
            columns, dates = dict(_US_WEEK), list(_US_DATES)

    frame, out = brief.fetch(holdings, market, columns, dates, now_iso)

    assert _session(out) == expected, f"{case}: session {out['__date']}, expected {expected}"
    stock = "005930.KS" if market == "KR" else "AAPL"
    day = pd.Timestamp(datetime.strptime(expected, "%Y년 %m월 %d일"))
    prev = frame.index[frame.index.get_loc(day) - 1]
    assert out[stock]["chg_pct"] == _chg(frame, stock, day, prev)


# ── 기준일 행이 없는 종목 ─────────────────────────────────────────────────────

def test_a_ticker_without_a_close_on_the_session_is_named_not_copied(brief):
    """000660.KS 만 09-17 행이 비었다 (10:00 KST · 기준일 09-17). 예전: 000660 +0.00% ·
    `섹터 None` · 합계에 포함 — 전일 값이 복제돼 멀쩡한 보합으로 보였다."""
    holdings = {"005930.KS": {"q": 10, "avg": 70000, "sector": "Tech"},
                "000660.KS": {"q": 5, "avg": 180000, "sector": None}, "CASH": {"q": 0}}
    frame, out = brief.fetch(holdings, "KR",
                             {**_KR_WEEK, "000660.KS": [180000, 182000, 184000, nan]}, _KR_DATES,
                             "2026-09-18T10:00:00+09:00", krx_until=date(2026, 9, 17))

    assert "000660.KS" not in out, "a ticker with no close on the session got a change"
    assert set(out["__missing"]) == {"000660.KS"} and out["__missing"]["000660.KS"].strip()
    assert out["005930.KS"]["chg_pct"] == _chg(frame, "005930.KS", "2026-09-17", "2026-09-16")

    prompt = dr._build_prompt(holdings, out, {}, "KR")
    assert f"000660.KS: 데이터 없음 — {out['__missing']['000660.KS']}" in prompt
    assert "합산 불가" in prompt, "the totals still summed the tickers that do have a change"
    assert "없음 (전 종목 3% 미만 변동)" not in prompt, "claimed every ticker moved under 3%"


def test_a_holding_without_a_sector_says_so(brief):
    holdings = {"005930.KS": {"q": 10, "avg": 70000, "sector": None}, "CASH": {"q": 0}}
    _, out = brief.fetch(holdings, "KR", _KR_WEEK, _KR_DATES, "2026-09-18T08:00:00+09:00",
                         krx_until=date(2026, 9, 17))

    assert out["005930.KS"]["sector"] is None
    prompt = dr._build_prompt(holdings, out, {}, "KR")
    assert "섹터 정보 없음" in prompt and "섹터 None" not in prompt


def test_no_brief_is_generated_when_no_holding_has_a_change(monkeypatch):
    """KR 16:00 KST, 그날 종가가 아직 수집 전. 등락을 하나도 못 구하면 뉴스·LLM 을
    부르지 않고 사유로 멈춘다 — 전 종목 '데이터 없음' 브리프는 비용만 든다."""
    frame = pd.DataFrame(_KR_WEEK, index=pd.DatetimeIndex(_KR_DATES), dtype=float)
    calls: list[str] = []
    now = datetime.fromisoformat("2026-09-18T16:00:00+09:00")

    def forbidden(name):
        def _f(*a, **kw):
            calls.append(name)
            raise AssertionError(f"{name} must not run without any holding change")
        return _f

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-never-sent")
    monkeypatch.setattr(mc, "_krx_trading_days", lambda: _krx(date(2026, 9, 18)))
    monkeypatch.setattr(mc, "now_et", lambda: now.astimezone(mc.ET))
    monkeypatch.setattr("backend.services.market_data.get_close_df", lambda *a, **kw: frame.copy())
    monkeypatch.setattr(dr, "_volatility_entry", lambda market: None)
    for name in ("_collect_news", "_collect_korean_news", "_perplexity_search", "_generate_with_claude"):
        monkeypatch.setattr(dr, name, forbidden(name))

    with pytest.raises(RuntimeError) as exc:
        dr.generate_daily_report(KR_H, lambda m: None, "KR")

    assert calls == [], f"ran {calls} for a brief with no data"
    assert "005930.KS" in str(exc.value), f"the reason does not name the ticker: {exc.value}"


# ── 캘린더가 실패하면 멈춘다 ──────────────────────────────────────────────────

def test_a_calendar_failure_stops_before_any_price_lookup(brief, monkeypatch):
    """세션을 모르는 채로 만든 브리프는 숫자가 틀려도 티가 안 난다 (e229171 이후
    `_holidays_for_year` 는 예외를 올린다)."""
    def broken(year):
        raise RuntimeError(f"holiday rules broken ({year})")

    monkeypatch.setattr(mc, "_holidays_for_year", broken)
    with pytest.raises(RuntimeError):
        brief.fetch(US_H, "US", _US_WEEK, _US_DATES, "2026-09-17T09:00:00-04:00")
    assert brief.close_df_calls == 0, "prices were fetched for a session nobody could determine"

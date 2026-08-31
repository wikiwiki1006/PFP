"""
backend/tests/test_signal_scan.py
─────────────────────────────────
SMA 1차 필터 + MACD/RSI 스코어링 매매신호 스캔 회귀 테스트.
네트워크·DB 불필요 (합성 프레임만 사용).
"""
import numpy as np
import pandas as pd

from backend.services.trading_signals import _macd_hist, _rsi, sma_macd_rsi_scan

N = 260
IDX = pd.bdate_range("2024-01-01", periods=N)


def _wobble(i: np.ndarray) -> np.ndarray:
    return 2.0 * np.sin(i / 5.0)


def _make_frames() -> tuple[pd.DataFrame, pd.DataFrame]:
    i = np.arange(N, dtype=float)

    bull  = 100.0 * (1.0006 ** i) + _wobble(i)          # 꾸준한 상승, 정배열
    bear  = 160.0 * (0.9985 ** i) + _wobble(i)          # 꾸준한 하락, 역배열
    flat  = 120.0 + 3.0 * np.sin(i / 8.0)               # 횡보
    quiet = 100.0 * (1.0006 ** i) + _wobble(i)          # 상승이지만 당일 거래량 소외

    close = pd.DataFrame(
        {"BULL": bull, "BEAR": bear, "FLAT": flat, "QUIET": quiet}, index=IDX
    )
    # 거래일 부족 종목 — 마지막 60행만 값이 있고 나머지는 NaN
    close["SHORT_HIST"] = np.nan
    close.iloc[-60:, close.columns.get_loc("SHORT_HIST")] = bull[-60:]

    base = np.full(N, 1_000_000.0)
    vol = pd.DataFrame(
        {
            "BULL":  base.copy(),
            "BEAR":  base.copy(),
            "FLAT":  base.copy(),
            "QUIET": base.copy(),
            "SHORT_HIST": base.copy(),
        },
        index=IDX,
    )
    # 당일 거래량: BULL·BEAR 는 급증(신호 확인), QUIET 는 소외, FLAT 는 평균과 동일
    vol.iloc[-1, vol.columns.get_loc("BULL")] = 2_600_000.0
    vol.iloc[-1, vol.columns.get_loc("BEAR")] = 2_600_000.0
    vol.iloc[-1, vol.columns.get_loc("QUIET")] = 500_000.0
    return close, vol


def test_uptrend_with_volume_surge_is_a_buy_pick():
    close, vol = _make_frames()
    res = sma_macd_rsi_scan(close, vol, top_n=10)

    longs = {p["ticker"] for p in res["long_picks"]}
    shorts = {p["ticker"] for p in res["short_picks"]}

    assert "BULL" in longs
    assert "BULL" not in shorts


def test_downtrend_with_volume_surge_is_a_sell_pick():
    close, vol = _make_frames()
    res = sma_macd_rsi_scan(close, vol, top_n=10)

    shorts = {p["ticker"] for p in res["short_picks"]}
    longs = {p["ticker"] for p in res["long_picks"]}

    assert "BEAR" in shorts
    assert "BEAR" not in longs


def test_step1_excludes_low_volume_and_sideways():
    close, vol = _make_frames()
    res = sma_macd_rsi_scan(close, vol, top_n=10)

    picked = {p["ticker"] for p in res["long_picks"] + res["short_picks"]}
    # 상승이지만 당일 거래량이 20일 평균 미만 → 1차 필터 탈락
    assert "QUIET" not in picked
    # 횡보 (정/역배열 아님, 거래량 급증 없음) → 탈락
    assert "FLAT" not in picked


def test_insufficient_history_is_silently_skipped():
    close, vol = _make_frames()
    res = sma_macd_rsi_scan(close, vol, top_n=10)

    picked = {p["ticker"] for p in res["long_picks"] + res["short_picks"]}
    assert "SHORT_HIST" not in picked
    # 260행짜리 4개만 스캔 대상 (SHORT_HIST 는 60행이라 제외)
    assert res["scanned"] == 4


def test_scores_and_shape_are_well_formed():
    close, vol = _make_frames()
    res = sma_macd_rsi_scan(close, vol, top_n=10)

    assert res["as_of"] == IDX[-1].strftime("%Y-%m-%d")
    for p in res["long_picks"] + res["short_picks"]:
        assert 0 <= p["score"] <= 100
        assert p["volume_ratio"] > 1.0          # 1차 필터를 통과했으므로
        assert set(p["components"]) == {"volume", "momentum", "trend"}
        assert 0.0 <= p["components"]["volume"] <= 40.0
        assert -10.0 <= p["components"]["trend"] <= 30.0
        assert isinstance(p["reason"], str) and p["reason"]


def test_empty_or_volumeless_input_returns_empty_result():
    close, _ = _make_frames()
    assert sma_macd_rsi_scan(pd.DataFrame(), None)["long_picks"] == []
    # 거래량이 아예 없으면 '수급' 조건을 판정할 수 없어 후보가 나오지 않는다
    res = sma_macd_rsi_scan(close, None, top_n=10)
    assert res["long_picks"] == [] and res["short_picks"] == []
    assert res["scanned"] == 4


def test_rsi_and_macd_helpers_basic_sanity():
    up = pd.Series(np.linspace(100, 200, 120))
    down = pd.Series(np.linspace(200, 100, 120))
    assert _rsi(up).iloc[-1] > 70
    assert _rsi(down).iloc[-1] < 30
    # 상승 series 의 MACD 히스토그램 부호는 EMA 차이를 따른다 (마지막 값 유한)
    assert np.isfinite(_macd_hist(up).iloc[-1])

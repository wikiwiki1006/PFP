"""
backend/tests/test_signal_scan.py
─────────────────────────────────
SMA 1차 필터 + MACD/RSI 스코어링 매매신호 스캔 회귀 테스트.
네트워크·DB 불필요 (합성 프레임만 사용).
"""
import numpy as np
import pandas as pd

from backend.services.trading_signals import (
    _macd_hist, _rsi, sma_macd_rsi_scan, score_ticker_both_sides,
)

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


# 아래 기본 테스트들은 top_n=1 을 쓴다 — 유니버스가 4~5종목뿐이라 top_n=10 을 주면
# 완화 사다리가 항상 끝까지 내려가 버려(유니버스 크기 자체가 10 미만) '엄격한 기준'을
# 검증할 수 없다. 완화 사다리 자체는 별도 테스트에서 검증한다.

def test_uptrend_with_volume_surge_is_a_buy_pick():
    close, vol = _make_frames()
    res = sma_macd_rsi_scan(close, vol, top_n=1)

    longs = {p["ticker"] for p in res["long_picks"]}
    shorts = {p["ticker"] for p in res["short_picks"]}

    assert "BULL" in longs
    assert "BULL" not in shorts
    assert res["long_filter_level"] == 0   # 완화 없이 원래 기준으로 통과


def test_downtrend_with_volume_surge_is_a_sell_pick():
    close, vol = _make_frames()
    res = sma_macd_rsi_scan(close, vol, top_n=1)

    shorts = {p["ticker"] for p in res["short_picks"]}
    longs = {p["ticker"] for p in res["long_picks"]}

    assert "BEAR" in shorts
    assert "BEAR" not in longs
    assert res["short_filter_level"] == 0


def test_step1_excludes_low_volume_and_sideways():
    close, vol = _make_frames()
    res = sma_macd_rsi_scan(close, vol, top_n=1)

    picked = {p["ticker"] for p in res["long_picks"] + res["short_picks"]}
    # 상승이지만 당일 거래량이 20일 평균 미만 → 1차 필터 탈락 (완화 없이도 top_n=1 충족)
    assert "QUIET" not in picked
    # 횡보 (정/역배열 아님, 거래량 급증 없음) → 탈락
    assert "FLAT" not in picked


def test_insufficient_history_is_silently_skipped():
    close, vol = _make_frames()
    res = sma_macd_rsi_scan(close, vol, top_n=10)

    picked = {p["ticker"] for p in res["long_picks"] + res["short_picks"]}
    assert "SHORT_HIST" not in picked
    # 260행짜리 4개만 스캔 대상 (SHORT_HIST 는 60행이라 제외) — top_n·완화와 무관하게 성립
    assert res["scanned"] == 4


def test_scores_and_shape_are_well_formed():
    close, vol = _make_frames()
    res = sma_macd_rsi_scan(close, vol, top_n=10)

    assert res["as_of"] == IDX[-1].strftime("%Y-%m-%d")
    for p in res["long_picks"] + res["short_picks"]:
        assert 0 <= p["score"] <= 100
        # 'trend' 는 과도기 키다. 값이 RSI 점수인데 프론트가 "추세" 막대로
        # 그리고 있어서, develop 이 components.rsi 로 옮기기 전까지만 함께 나간다.
        assert set(p["components"]) == {"volume", "momentum", "rsi", "trend"}
        # 중복이 **의도된 것**임을 여기서 못 박는다. 둘이 갈라지는 순간
        # 화면의 "추세" 막대와 실제 RSI 점수가 다른 값을 그리게 되는데,
        # 키 집합만 보는 단언으로는 그 순간이 안 보인다.
        assert p["components"]["trend"] == p["components"]["rsi"], (
            f"trend={p['components']['trend']} but rsi={p['components']['rsi']} -- "
            "'trend' exists only as an alias of the RSI score until the "
            "frontend moves to components.rsi. Once they differ, the bar "
            "labelled 추세 is showing something else again."
        )
        assert 0.0 <= p["components"]["volume"] <= 40.0
        assert -10.0 <= p["components"]["trend"] <= 30.0
        assert isinstance(p["reason"], str) and p["reason"]


def test_empty_or_volumeless_input_returns_empty_result():
    close, _ = _make_frames()
    assert sma_macd_rsi_scan(pd.DataFrame(), None)["long_picks"] == []
    # 거래량이 아예 없으면 '수급' 조건을 판정할 수 없어 후보가 나오지 않는다.
    # (1차 필터는 완화되면 거래량 없이도 후보를 만들 수 있지만, Step2 스코어링
    #  자체가 거래량을 요구하므로 결국 스코어를 못 매겨 빠진다.)
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


# ══════════════════════════════════════════════════════════════════════════════
# 1차 필터 완화 사다리 — 실사례 재현
#
# 운영 중 실제로 겪은 상황: 일별 수집이 마감 1시간 뒤라 그날 체결분이 다 반영되기
# 전이면, 거의 모든 종목의 '당일 거래량'이 20일 평균보다 낮게 잡혀 1차 필터를
# 통과하는 종목이 하나도 없었다(단순 코드 버그가 아니라 데이터 수집 타이밍 때문).
# 이 테스트는 그 상황을 합성 데이터로 재현해, 완화 사다리가 top_n 을 채워주는지 검증한다.
# ══════════════════════════════════════════════════════════════════════════════

def _make_all_uptrend_universe(n_tickers: int = 15) -> tuple[pd.DataFrame, pd.DataFrame]:
    i = np.arange(N, dtype=float)
    close, vol = {}, {}
    for k in range(n_tickers):
        drift = 0.0004 + 0.00002 * k
        close[f"T{k}"] = 100.0 * (1.0 + drift) ** i + _wobble(i) * (1 + 0.05 * k)
        vol[f"T{k}"] = np.full(N, 1_000_000.0 + k * 10_000.0)
    close_df = pd.DataFrame(close, index=IDX)
    vol_df   = pd.DataFrame(vol, index=IDX)
    # 실사례 재현: 당일(마지막 행) 거래량을 모든 종목에서 최근 20일 평균의 60% 로 —
    # 원래 기준이면 단 하나도 '수급 폭발' 조건을 통과하지 못한다.
    vol_df.iloc[-1] = vol_df.iloc[-21:-1].mean() * 0.6
    return close_df, vol_df


def test_relaxation_fills_long_picks_when_every_ticker_misses_volume_surge():
    close_df, vol_df = _make_all_uptrend_universe()
    res = sma_macd_rsi_scan(close_df, vol_df, top_n=10)

    # 전 종목 상승세라 추세·가격 조건은 쉽게 만족 — 거래량 조건만 완화되면 채워진다.
    assert res["long_filter_level"] >= 1
    assert len(res["long_picks"]) == 10
    assert res["long_filter_note"] != "기준 그대로"


def test_relaxation_falls_back_to_full_universe_when_no_side_matches_at_all():
    close_df, vol_df = _make_all_uptrend_universe()
    res = sma_macd_rsi_scan(close_df, vol_df, top_n=10)

    # 전부 상승 종목이라 매도(하락) 쪽엔 추세·가격 조건을 만족하는 종목이 아예 없다
    # → 최후 단계(전 종목 스코어링)까지 완화돼야 top_n 이 채워진다.
    assert res["short_filter_level"] == 5
    assert len(res["short_picks"]) == 10


def test_relaxation_never_exceeds_universe_size():
    """유니버스가 top_n 보다 작으면(이 스위트의 기본 프레임 등) 완화해도 top_n 개를
    채울 수 없다 — 있는 만큼만 반환하고 무한 루프나 예외 없이 끝나야 한다."""
    close, vol = _make_frames()  # 유효 종목 4개뿐
    res = sma_macd_rsi_scan(close, vol, top_n=10)
    assert len(res["long_picks"]) <= 4
    assert len(res["short_picks"]) <= 4
    # 4개로는 top_n(10)을 절대 못 채우므로 완화 사다리가 마지막 단계까지 내려간다
    assert res["long_filter_level"] == 5
    assert res["short_filter_level"] == 5


# ══════════════════════════════════════════════════════════════════════════════
# 검색된 임의 종목의 참고 점수 (top_n 밖이어도 계산)
# ══════════════════════════════════════════════════════════════════════════════

def test_score_ticker_both_sides_scores_regardless_of_filter_pass():
    close, vol = _make_frames()
    # BEAR 는 하락 종목이라 '매수' 1차 필터는 통과하지 못하지만, 참고 점수는 계산돼야 한다.
    result = score_ticker_both_sides("BEAR", close["BEAR"], vol["BEAR"])

    assert result["insufficient_history"] is False
    assert result["long"] is not None            # 필터 미통과여도 점수는 나온다
    assert result["long_filter_pass"] is False    # 다만 오늘 실제로는 통과 못 했다는 사실도 알려준다
    assert result["short"] is not None
    assert result["short_filter_pass"] is True
    assert 0 <= result["long"]["score"] <= 100
    assert 0 <= result["short"]["score"] <= 100


def test_score_ticker_both_sides_without_volume_returns_none_scores():
    close, _ = _make_frames()
    result = score_ticker_both_sides("BULL", close["BULL"], None)
    assert result["long"] is None
    assert result["short"] is None
    # 필터 통과 여부 자체는 거래량과 무관하게 판단 가능하다
    assert result["long_filter_pass"] is True


def test_score_ticker_both_sides_insufficient_history():
    short_series = pd.Series(np.linspace(100, 110, 30))
    result = score_ticker_both_sides("NEW", short_series, None)
    assert result["insufficient_history"] is True
    assert result["long"] is None and result["short"] is None

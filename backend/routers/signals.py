"""
routers/signals.py
───────────────────
매매 타이밍 신호 API (전수 스캔 / 개별 전략)
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from backend.services.market_data import get_close_df
from backend.services.trading_signals import (
    SP500_NASDAQ_UNIVERSE,
    fetch_macro_doom_indicators,
    evaluate_doom_radar,
    scan_universe_with_targets,
    pairs_trading_signal,
    mean_reversion_signal,
    momentum_breakout_signal,
    detect_market_regime,
)

router = APIRouter(prefix="/api/signals", tags=["signals"])

_DATA_DIR = Path(__file__).parent.parent.parent / "pfp" / "data"
_DB_FILE  = _DATA_DIR / "holdings.json"

_scan_cache: dict = {}


def _load_holdings() -> dict:
    if not _DB_FILE.exists():
        return {}
    with open(_DB_FILE) as f:
        raw = json.load(f)
    return raw.get("my_holdings", raw)


_SEVERITY_MAP = {"위기": 5, "경고": 3, "정상": 1}


def _doom_radar_dict() -> dict:
    macro = fetch_macro_doom_indicators()
    doom  = evaluate_doom_radar(macro["rate_spread"], macro["hy_spread"])
    sev_str = doom.get("severity", "정상")
    sev_num = _SEVERITY_MAP.get(sev_str, 2 if doom["is_doom"] else 1)
    return {
        "is_doom":     doom["is_doom"],
        "severity":    sev_num,
        "comment":     doom.get("comment", ""),
        "rate_spread": round(macro["rate_spread"], 3),
        "hy_spread":   round(macro["hy_spread"], 3),
        "source":      macro["source"],
    }


# ══════════════════════════════════════════════════════════════════════════════
# 엔드포인트
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/doom-radar")
def doom_radar():
    """매크로 저승사자 레이더 (장단기 금리역전 + HY 스프레드)."""
    return _doom_radar_dict()


@router.post("/scan")
def scan_universe(
    top_n:             int  = Query(default=10, ge=1, le=30),
    include_portfolio: bool = Query(default=True),
):
    """
    S&P500 + 나스닥 전수 스캔 — 롱/숏 타점 반환.
    30~60초 소요. 결과는 인메모리 캐시.
    """
    import yfinance as yf

    extra    = []
    if include_portfolio:
        extra = [t for t in _load_holdings() if t != "CASH"]

    universe = sorted(set(SP500_NASDAQ_UNIVERSE + extra))

    try:
        data      = yf.download(universe, period="6mo", progress=False, auto_adjust=True)
        price_df  = data["Close"].ffill()
        volume_df = data["Volume"].ffill() if "Volume" in data.columns.get_level_values(0) else None

        raw = scan_universe_with_targets(price_df, volume_df, top_n=top_n)

        def _clean(picks: list[dict]) -> list[dict]:
            out = []
            for p in picks:
                out.append({
                    "ticker":   p.get("ticker", ""),
                    "method":   p.get("method", ""),
                    "score":    round(float(p.get("score", 0)), 2),
                    "entry":    round(float(p.get("entry", 0)), 2),
                    "target":   round(float(p.get("target", 0)), 2),
                    "stop":     round(float(p.get("stop", 0)), 2),
                    "upside":   round(float(p["upside"]), 2)   if p.get("upside")   is not None else None,
                    "downside": round(float(p["downside"]), 2) if p.get("downside") is not None else None,
                    "reason":   p.get("reason", ""),
                })
            return out

        result = {
            "long_picks":  _clean(raw.get("long_picks", [])),
            "short_picks": _clean(raw.get("short_picks", [])),
            "scanned":     raw.get("scanned", len(universe)),
            "doom":        _doom_radar_dict(),
        }
        _scan_cache["last"] = result
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"스캔 실패: {e}")


@router.get("/scan/cached")
def get_cached_scan():
    """마지막 스캔 결과 반환 (재실행 없이 빠르게 조회)."""
    if not _scan_cache.get("last"):
        raise HTTPException(status_code=404, detail="스캔 결과 없음. POST /api/signals/scan 먼저 실행")
    return _scan_cache["last"]


@router.get("/pairs")
def pairs_signal(
    ticker_a: str = Query(..., description="첫 번째 티커. 예: NVDA"),
    ticker_b: str = Query(..., description="두 번째 티커. 예: AMD"),
    lookback: int = Query(default=60, ge=20, le=252),
    period:   str = Query(default="1y"),
):
    """두 종목의 페어 트레이딩 Z-score 신호."""
    ticker_a = ticker_a.upper()
    ticker_b = ticker_b.upper()

    close_df = get_close_df([ticker_a, ticker_b], period=period, ttl=300)

    if ticker_a not in close_df.columns or ticker_b not in close_df.columns:
        raise HTTPException(status_code=400, detail=f"{ticker_a} 또는 {ticker_b} 데이터 없음")

    result = pairs_trading_signal(
        close_df[ticker_a].dropna(),
        close_df[ticker_b].dropna(),
        lookback=lookback,
    )

    return {
        "current_z":      round(result["current_z"], 4),
        "current_signal": str(result["current_signal"]) if result["current_signal"] else None,
        "beta":           round(result["beta"], 4),
        "correlation":    round(result["correlation"], 4),
        "is_valid_pair":  result["is_valid_pair"],
        "lock_message":   result.get("lock_message"),
    }


@router.get("/mean-reversion")
def mean_reversion(
    ticker: str   = Query(..., description="티커. 예: TSLA"),
    window: int   = Query(default=20, ge=5, le=60),
    n_std:  float = Query(default=2.0, ge=1.0, le=3.0),
    period: str   = Query(default="6mo"),
):
    """볼린저 밴드 기반 평균 회귀 신호."""
    ticker   = ticker.upper()
    close_df = get_close_df([ticker], period=period, ttl=300)

    if ticker not in close_df.columns:
        raise HTTPException(status_code=400, detail=f"{ticker} 데이터 없음")

    result = mean_reversion_signal(close_df[ticker].dropna(), window=window, n_std=n_std)

    return {
        "current_signal": str(result["current_signal"]) if result["current_signal"] else None,
        "current_price":  round(float(result["current_price"]), 2),
        "upper_band":     round(float(result["upper_band"].iloc[-1]), 2),
        "lower_band":     round(float(result["lower_band"].iloc[-1]), 2),
        "mid_band":       round(float(result["mid_band"].iloc[-1]), 2),
        "pct_b":          round(float(result.get("pct_b", 0.5)), 4),
        "current_z":      round(float(result.get("current_z", 0.0)), 4),
    }


@router.get("/momentum")
def momentum_breakout(
    ticker:   str = Query(..., description="티커. 예: NVDA"),
    lookback: int = Query(default=20, ge=5, le=60),
    period:   str = Query(default="6mo"),
):
    """N일 고가 돌파 + 거래량 급증 모멘텀 신호."""
    ticker   = ticker.upper()
    close_df = get_close_df([ticker], period=period, ttl=300)

    if ticker not in close_df.columns:
        raise HTTPException(status_code=400, detail=f"{ticker} 데이터 없음")

    price  = close_df[ticker].dropna()
    result = momentum_breakout_signal(price, volume=None, lookback=lookback)

    resistance = result["resistance"].iloc[-1]

    return {
        "current_signal":    str(result["current_signal"]) if result["current_signal"] else None,
        "current_price":     round(float(result["current_price"]), 2),
        "resistance":        round(float(resistance), 2) if not pd.isna(resistance) else None,
        "is_breakout_today": bool(result["is_breakout_today"]),
        "volume_surge":      bool(result.get("volume_surge", False)),
        "volume_ratio":      round(float(result.get("volume_ratio", 1.0)), 2),
    }


@router.get("/multi")
def multi_signal(
    ticker: str = Query(..., description="분석할 티커"),
    period: str = Query(default="6mo"),
):
    """단일 종목에 대해 평균회귀 + 모멘텀 신호를 동시에 반환."""
    ticker   = ticker.upper()
    close_df = get_close_df([ticker], period=period, ttl=300)

    if ticker not in close_df.columns:
        raise HTTPException(status_code=400, detail=f"{ticker} 데이터 없음")

    price = close_df[ticker].dropna()
    mr    = mean_reversion_signal(price)
    mb    = momentum_breakout_signal(price, volume=None)

    mr_signal = mr["current_signal"]
    mb_signal = mb["current_signal"]
    agreement = (
        (mr_signal == "BUY"  and mb_signal == "BREAKOUT") or
        (mr_signal == "SELL" and mb_signal is None)
    )

    return {
        "ticker": ticker,
        "mean_reversion": {
            "current_signal": str(mr_signal) if mr_signal else None,
            "current_z":      round(float(mr["current_z"]), 4),
            "pct_b":          round(float(mr.get("pct_b", 0.5)), 4),
        },
        "momentum": {
            "current_signal":    str(mb_signal) if mb_signal else None,
            "is_breakout_today": bool(mb["is_breakout_today"]),
        },
        "signals_agree": agreement,
        "combined_view": (
            "STRONG BUY"  if mr_signal == "BUY"  and mb_signal == "BREAKOUT" else
            "STRONG SELL" if mr_signal == "SELL" and mb_signal is None else
            "MIXED"
        ),
    }


@router.get("/regime")
def market_regime(
    ticker: str = Query(default="^GSPC", description="분석 티커 (기본: S&P500)"),
    period: str = Query(default="2y", description="기간 (1y/2y/3y)"),
):
    """K-means 기반 시장 국면 감지 (Bull/Sideways/Bear)."""
    ticker   = ticker.upper()
    close_df = get_close_df([ticker], period=period, ttl=300)

    if ticker not in close_df.columns:
        raise HTTPException(status_code=400, detail=f"{ticker} 데이터 없음")

    price  = close_df[ticker].dropna()
    result = detect_market_regime(price)

    regime_counts = {}
    if "regime_series" in result:
        import pandas as pd
        rs = pd.Series(result["regime_series"])
        counts = rs.value_counts()
        total  = len(rs)
        for r_name in ["Bull", "Sideways", "Bear"]:
            regime_counts[r_name] = round(counts.get(r_name, 0) / total * 100, 1) if total > 0 else 0.0

    chart_data = []
    if "regime_series" in result and close_df is not None:
        price_series = close_df[ticker].dropna()
        for i, (date, regime) in enumerate(zip(price_series.index, result.get("regime_series", []))):
            chart_data.append({
                "date":   date.strftime("%Y-%m-%d"),
                "price":  round(float(price_series.iloc[i]), 2),
                "regime": regime,
            })

    return {
        "ticker":          ticker,
        "current_regime":  result.get("current_regime", "Unknown"),
        "regime_pct":      regime_counts,
        "n_regimes":       result.get("n_regimes", 3),
        "chart_data":      chart_data[-252:],
    }

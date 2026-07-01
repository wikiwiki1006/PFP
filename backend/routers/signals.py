"""
routers/signals.py
───────────────────
매매 타이밍 신호 API (전수 스캔 / 개별 전략)
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd
from fastapi import APIRouter, Header, HTTPException, Query

from backend.db.portfolio_repo import get_holdings as db_get_holdings
from backend.db.market_cache import get_common, save_common
from backend.services.market_data import get_close_df, _cached
from backend.services.trading_signals import (
    SP500_NASDAQ_UNIVERSE,
    scan_universe_with_targets,
    pairs_trading_signal,
    mean_reversion_signal,
    momentum_breakout_signal,
    detect_market_regime,
    get_sp500_universe,
    bollinger_scan_full_universe,
    compute_macro_spread_levels,
    technical_chart_detail,
    pairs_auto_detail,
)

router = APIRouter(prefix="/api/signals", tags=["signals"])

_scan_cache: dict = {}


def _uid(x: Optional[str]) -> str:
    return (x or "default").strip() or "default"


# ══════════════════════════════════════════════════════════════════════════════
# 엔드포인트
# ══════════════════════════════════════════════════════════════════════════════


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
    from backend.db.market_cache import _yf_lock

    extra    = []
    if include_portfolio:
        extra = [t for t in db_get_holdings("default") if t != "CASH"]

    universe = sorted(set(SP500_NASDAQ_UNIVERSE + extra))

    import logging as _logging
    _logger = _logging.getLogger(__name__)

    BATCH = 50
    try:
        _yf_log = _logging.getLogger("yfinance")
        _prev = _yf_log.level
        _yf_log.setLevel(_logging.CRITICAL)
        close_frames:  list[pd.DataFrame] = []
        volume_frames: list[pd.DataFrame] = []
        try:
            for i in range(0, len(universe), BATCH):
                batch = universe[i : i + BATCH]
                with _yf_lock:
                    data = yf.download(
                        batch, period="6mo", progress=False,
                        auto_adjust=True, threads=False
                    )
                if data is None or data.empty:
                    continue
                if isinstance(data.columns, pd.MultiIndex):
                    lvl0 = data.columns.get_level_values(0)
                    if "Close"  in lvl0: close_frames.append(data["Close"])
                    if "Volume" in lvl0: volume_frames.append(data["Volume"])
                else:
                    if "Close"  in data.columns: close_frames.append(data[["Close"]])
                    if "Volume" in data.columns: volume_frames.append(data[["Volume"]])
        finally:
            _yf_log.setLevel(_prev)

        if not close_frames:
            raise HTTPException(status_code=503, detail="시장 데이터를 가져올 수 없습니다. 잠시 후 다시 시도하세요.")

        close_raw  = pd.concat(close_frames,  axis=1)
        volume_raw = pd.concat(volume_frames, axis=1) if volume_frames else None

        if close_raw.empty:
            raise HTTPException(status_code=503, detail="종가 데이터를 가져올 수 없습니다.")

        price_df  = close_raw.ffill().dropna(axis=1, how="all")
        volume_df = volume_raw.ffill() if volume_raw is not None else None

        _logger.info(f"스캔 데이터 로드 완료: {price_df.shape[1]}개 티커 × {len(price_df)}일")

        raw = scan_universe_with_targets(price_df, volume_df, top_n=top_n)

        def _clean(picks: list[dict]) -> list[dict]:
            out = []
            for p in picks:
                def _f(v):
                    try:
                        r = float(v)
                        return r if (r == r and abs(r) != float('inf')) else None  # NaN/Inf → None
                    except Exception:
                        return None
                out.append({
                    "ticker":   p.get("ticker", ""),
                    "method":   p.get("method", ""),
                    "score":    round(_f(p.get("score", 0)) or 0, 2),
                    "entry":    round(_f(p.get("entry", 0)) or 0, 2),
                    "target":   round(_f(p.get("target", 0)) or 0, 2),
                    "stop":     round(_f(p.get("stop", 0)) or 0, 2),
                    "upside":   round(_f(p["upside"]), 2)   if p.get("upside")   is not None else None,
                    "downside": round(_f(p["downside"]), 2) if p.get("downside") is not None else None,
                    "reason":   p.get("reason", ""),
                })
            return out

        result = {
            "long_picks":  _clean(raw.get("long_picks", [])),
            "short_picks": _clean(raw.get("short_picks", [])),
            "scanned":     raw.get("scanned", len(universe)),
        }
        _scan_cache["last"] = result
        return result
    except HTTPException:
        raise
    except Exception as e:
        _logger.error(f"스캔 실패 상세: {type(e).__name__}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"스캔 실패: {type(e).__name__}: {e}")


@router.get("/scan/cached")
def get_cached_scan():
    """마지막 스캔 결과 반환. 스캔 전이면 빈 결과 반환."""
    return _scan_cache.get("last") or {"long_picks": [], "short_picks": [], "scanned": 0}


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
    years:  int = Query(default=1, ge=1, le=5, description="표시 기간 (1-5년)"),
):
    """K-means 기반 시장 국면 감지 (Bull/Sideways/Bear). years 범위 내 데이터만 반환."""
    ticker   = ticker.upper()
    close_df = get_close_df([ticker], period="5y", ttl=300)

    if ticker not in close_df.columns:
        raise HTTPException(status_code=400, detail=f"{ticker} 데이터 없음")

    price_all = close_df[ticker].dropna()
    result    = detect_market_regime(price_all)

    rl = result.get("regime_labels")
    if rl is None or len(rl) == 0:
        return {"ticker": ticker, "current_regime": "Unknown",
                "regime_pct": {}, "n_regimes": 3, "chart_data": []}

    rl_series    = pd.Series(rl)
    cutoff       = rl_series.index.max() - pd.Timedelta(days=365 * years)
    rl_window    = rl_series[rl_series.index >= cutoff]
    price_window = price_all[price_all.index >= cutoff]

    counts = rl_window.value_counts()
    total  = len(rl_window)
    regime_counts = {
        r: round(counts.get(r, 0) / total * 100, 1) if total > 0 else 0.0
        for r in ["Bull", "Sideways", "Bear"]
    }

    chart_data = [
        {
            "date":   date.strftime("%Y-%m-%d"),
            "price":  round(float(price_window.loc[date]), 2),
            "regime": regime,
        }
        for date, regime in rl_window.items()
        if date in price_window.index
    ]

    return {
        "ticker":         ticker,
        "current_regime": result.get("current_regime", "Unknown"),
        "regime_pct":     regime_counts,
        "n_regimes":      result.get("n_regimes", 3),
        "chart_data":     chart_data,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Timing Engine 신규 엔드포인트
# ══════════════════════════════════════════════════════════════════════════════

def _trim_years(df: pd.DataFrame, years: int = 3) -> pd.DataFrame:
    """get_close_df(period='5y') 결과를 최근 N년으로 트림 (yfinance는 '3y'를 지원하지 않음)."""
    if df.empty:
        return df
    cutoff = df.index.max() - pd.Timedelta(days=365 * years)
    return df[df.index >= cutoff]


@router.get("/market-situation")
def market_situation():
    """금리차(10Y-2Y) / 하이일드 스프레드의 과거 백분위 기반 Low/Normal/High 분류."""
    cached = get_common("market_situation")
    if cached:
        return cached

    result = compute_macro_spread_levels()
    save_common("market_situation", result, ttl_seconds=86400)
    return result


@router.get("/bb-scan-full")
def bb_scan_full(top_n: int = Query(default=10, ge=1, le=30)):
    """
    S&P500 전체 종목 대상 3년 볼린저 밴드 스캔 — 매수/매도 신호 상위 N개.
    common_cache에 6시간 TTL로 캐시(스케줄러가 주기적으로 갱신).
    """
    cached = get_common("bb_scan_sp500")
    if cached:
        long_picks  = cached.get("long_picks", [])[:top_n]
        short_picks = cached.get("short_picks", [])[:top_n]
        return {**cached, "long_picks": long_picks, "short_picks": short_picks}

    universe = get_sp500_universe()
    close_df = get_close_df(universe, period="5y", ttl=300)
    close_df = _trim_years(close_df, years=3)
    valid_cols = [c for c in universe if c in close_df.columns]
    result = bollinger_scan_full_universe(close_df[valid_cols], top_n=max(top_n, 10))
    save_common("bb_scan_sp500", result, ttl_seconds=21600)
    return {
        **result,
        "long_picks":  result["long_picks"][:top_n],
        "short_picks": result["short_picks"][:top_n],
    }


@router.get("/technical-chart")
def technical_chart(
    ticker: str = Query(..., description="티커. 예: AAPL"),
    period: str = Query(default="3y", description="조회 기간 (최대 3y)"),
):
    """가격 + 볼린저밴드 + 저항선 + 키포인트(주요 변곡점) 시계열. 매매신호/평균회귀 패널 공용."""
    ticker = ticker.upper()

    def _compute():
        close_df = get_close_df([ticker], period="5y", ttl=300)
        if ticker not in close_df.columns:
            return None
        trimmed = _trim_years(close_df, years=3)
        price = trimmed[ticker].dropna()
        if price.empty:
            return None
        return technical_chart_detail(price)

    result = _cached(f"technical_chart::{ticker}", 600, _compute)
    if result is None:
        raise HTTPException(status_code=400, detail=f"{ticker} 데이터 없음")

    return {"ticker": ticker, **result}


@router.get("/pairs-auto")
def pairs_auto(
    ticker:        str   = Query(..., description="기준 티커. 예: KO"),
    threshold_pct: float = Query(default=5.0, ge=0.1, le=100.0),
    top_n:         int   = Query(default=5, ge=1, le=20),
):
    """기준 종목과 가장 유사한 페어를 S&P500 유니버스에서 자동 탐색."""
    ticker = ticker.upper()

    def _compute():
        universe = get_sp500_universe()
        candidates = [t for t in universe if t != ticker][:200]
        close_df = get_close_df([ticker] + candidates, period="5y", ttl=300)
        return pairs_auto_detail(ticker, close_df, candidates, threshold_pct=threshold_pct, top_n=top_n)

    result = _cached(f"pairs_auto::{ticker}::{threshold_pct}::{top_n}", 600, _compute)
    if not result.get("best"):
        raise HTTPException(status_code=400, detail=f"{ticker}에 대한 유사 종목을 찾을 수 없습니다.")

    return {"ticker": ticker, **result}

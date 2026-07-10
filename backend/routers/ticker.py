"""
routers/ticker.py
──────────────────
종목 상세 분석 API (차트 데이터, 펀더멘털, 성과, 리스크, 기술적 지표)
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf
from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/ticker", tags=["ticker"])

# ── 헬퍼 ─────────────────────────────────────────────────────────────────────

def _safe(v, default=None):
    if v is None:
        return default
    try:
        f = float(v)
        return default if (math.isnan(f) or math.isinf(f)) else f
    except Exception:
        return default


def _calc_rsi(closes: pd.Series, period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    delta = closes.diff()
    gain = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    rs = gain.iloc[-1] / loss.iloc[-1] if loss.iloc[-1] != 0 else float("inf")
    return round(100 - (100 / (1 + rs)), 1)


def _calc_bb(closes: pd.Series, period: int = 20, n_std: float = 2.0) -> pd.DataFrame:
    mid = closes.rolling(period).mean()
    std = closes.rolling(period).std()
    return pd.DataFrame({"bb_upper": mid + n_std * std, "bb_mid": mid, "bb_lower": mid - n_std * std})


def _calc_stoch(hist: pd.DataFrame, k_period: int = 14, smooth_k: int = 3, smooth_d: int = 3) -> pd.DataFrame:
    low_min  = hist["Low"].rolling(k_period).min()
    high_max = hist["High"].rolling(k_period).max()
    raw_k = 100 * (hist["Close"] - low_min) / (high_max - low_min + 1e-9)
    k = raw_k.rolling(smooth_k).mean()
    d = k.rolling(smooth_d).mean()
    return pd.DataFrame({"stoch_k": k, "stoch_d": d})


_PERIOD_MAP = {
    "1m": "1mo", "3m": "3mo", "6m": "6mo",
    "1y": "1y", "2y": "2y", "5y": "5y",
}


# ── 엔드포인트 ────────────────────────────────────────────────────────────────

@router.get("/{ticker}/detail")
def get_ticker_detail(
    ticker: str,
    period: str = Query("1y", regex="^(1m|3m|6m|1y|2y|5y)$"),
):
    """종목 상세: OHLCV + 이평선/BB/스토케스틱 + 펀더멘털 + 성과 + 리스크 + VaR"""
    sym = ticker.upper()
    yf_period = _PERIOD_MAP.get(period, "1y")

    try:
        t = yf.Ticker(sym)
        hist = t.history(period=yf_period, auto_adjust=True)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"yfinance 오류: {e}")

    if hist.empty or len(hist) < 5:
        raise HTTPException(status_code=404, detail=f"{sym} 데이터 없음")

    # ── 종가 Series ──────────────────────────────────────────────────────────
    closes = hist["Close"]
    current = float(closes.iloc[-1])
    prev    = float(closes.iloc[-2]) if len(closes) > 1 else current

    # ── 이평선 ──────────────────────────────────────────────────────────────
    ma20  = closes.rolling(20).mean()
    ma50  = closes.rolling(50).mean()
    ma200 = closes.rolling(200).mean()

    # ── BB ──────────────────────────────────────────────────────────────────
    bb = _calc_bb(closes)

    # ── 스토케스틱 ──────────────────────────────────────────────────────────
    stoch = _calc_stoch(hist)

    # ── OHLCV + 지표 합산 ───────────────────────────────────────────────────
    def _fv(s: pd.Series, i: int) -> Optional[float]:
        v = s.iloc[i]
        return None if (pd.isna(v) or math.isinf(float(v))) else round(float(v), 4)

    ohlcv = []
    for i, (dt, row) in enumerate(hist.iterrows()):
        ohlcv.append({
            "date":     dt.strftime("%Y-%m-%d"),
            "open":     round(float(row["Open"]),   4),
            "high":     round(float(row["High"]),   4),
            "low":      round(float(row["Low"]),    4),
            "close":    round(float(row["Close"]),  4),
            "volume":   int(row["Volume"]),
            "ma20":     _fv(ma20,  i),
            "ma50":     _fv(ma50,  i),
            "ma200":    _fv(ma200, i),
            "bb_upper": _fv(bb["bb_upper"], i),
            "bb_mid":   _fv(bb["bb_mid"],   i),
            "bb_lower": _fv(bb["bb_lower"], i),
            "stoch_k":  _fv(stoch["stoch_k"], i),
            "stoch_d":  _fv(stoch["stoch_d"], i),
        })

    # ── 펀더멘털 ────────────────────────────────────────────────────────────
    info: dict = {}
    try:
        info = t.info or {}
    except Exception:
        pass

    def _fmt_cap(v) -> str:
        if not v:
            return "N/A"
        v = float(v)
        if v >= 1e12:
            return f"{v/1e12:.1f}T USD"
        if v >= 1e9:
            return f"{v/1e9:.1f}B USD"
        return f"{v/1e6:.0f}M USD"

    # ── 수익률 ──────────────────────────────────────────────────────────────
    def _perf(n: int) -> Optional[float]:
        if len(closes) <= n:
            return None
        return round((current / float(closes.iloc[-n - 1]) - 1) * 100, 2)

    # YTD: 올해 첫 거래일 대비
    this_year = hist.index[-1].year
    ytd_hist  = hist[hist.index.year == this_year]["Close"]
    ytd = round((current / float(ytd_hist.iloc[0]) - 1) * 100, 2) if len(ytd_hist) > 1 else None

    # 52주 고/저가 (1y 이상 데이터가 없으면 수집된 전체 범위)
    s52 = hist["Close"].tail(252) if len(hist) >= 252 else hist["Close"]

    # ── 리스크 지표 ─────────────────────────────────────────────────────────
    returns = closes.pct_change().dropna()
    vol_ann = round(float(returns.std() * math.sqrt(252) * 100), 1) if len(returns) >= 10 else None
    rsi14   = _calc_rsi(closes)
    beta    = _safe(info.get("beta"), 1.0)

    # ── VaR (Historical Simulation, 95%) ────────────────────────────────────
    ret_1y = returns.tail(252)
    var95: Optional[float] = None
    return_dist: list[dict] = []
    if len(ret_1y) >= 30:
        var95 = round(float(np.percentile(ret_1y.values, 5) * 100), 2)
        counts, bins = np.histogram(ret_1y.values * 100, bins=40)
        return_dist = [
            {"x": round(float(b), 3), "count": int(c)}
            for b, c in zip(bins[:-1], counts)
        ]

    return {
        "ticker":  sym,
        "period":  period,
        "ohlcv":   ohlcv,
        "info": {
            "name":       info.get("longName") or sym,
            "sector":     info.get("sector")   or "N/A",
            "industry":   info.get("industry") or "N/A",
            "market_cap": _fmt_cap(info.get("marketCap")),
            "pe":         _safe(info.get("trailingPE"),   None),
            "div_yield":  round(_safe(info.get("dividendYield"), 0.0) * 100, 2),
        },
        "performance": {
            "1w":        _perf(5),
            "1m":        _perf(21),
            "6m":        _perf(126),
            "ytd":       ytd,
            "1y":        _perf(252),
            "5y":        _perf(1260),
            "s52w_high": round(float(s52.max()), 2),
            "s52w_low":  round(float(s52.min()), 2),
        },
        "risk": {
            "beta":         beta,
            "volatility":   vol_ann,
            "avg_volume":   int(hist["Volume"].tail(20).mean()),
            "rsi14":        rsi14,
            "current_price": round(current, 2),
            "change_pct":   round((current / prev - 1) * 100, 2),
        },
        "var": {
            "var95":       var95,
            "return_dist": return_dist,
        },
        # ── 향후 개발 예정 (placeholder) ──────────────────────────────────
        "quant": {
            "score":        78,
            "score_label":  "BULLISH / HIGH MOMENTUM",
            "regime":       "Risk-On (Phase 2)",
            "optimizer": {
                "target_weight":     8.5,
                "risk_contribution": 4.2,
                "current_weight":    8.5,
                "correlation":       0.65,
                "correlation_label": "Moderate",
                "beta_exposure":     1.2,
            },
            "panic_score":  25,
            "panic_status": "Extreme Panic - Buy Opportunity",
        },
    }

"""
routers/optimizer.py
─────────────────────
포트폴리오 최적화 + 몬테카를로 시뮬레이션 API
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd
from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel

from backend.db.portfolio_repo import get_holdings as db_get_holdings
from backend.services.market_data import get_close_df
from backend.services.optimizer import (
    optimize_max_sharpe,
    optimize_black_litterman,
    build_regime_views,
    factor_analysis,
    generate_proxy_factors,
)
from backend.services.monte_carlo import (
    monte_carlo_portfolio_target,
    monte_carlo_stock_target_price,
    monte_carlo_macro_scenario,
    calculate_advanced_params,
    INDUSTRY_ETF_MAP,
)

router = APIRouter(prefix="/api/optimizer", tags=["optimizer"])


def _uid(x: Optional[str]) -> str:
    return (x or "default").strip() or "default"


def _fetch_returns(tickers: list[str], period: str = "1y") -> pd.DataFrame:
    close_df = get_close_df(tickers, period=period, ttl=600)
    if close_df.empty:
        raise HTTPException(status_code=502, detail="시세 데이터 조회 실패")
    daily_returns = close_df.pct_change().dropna()
    valid = [t for t in tickers if t in daily_returns.columns and daily_returns[t].notna().sum() >= 60]
    if len(valid) < 2:
        raise HTTPException(status_code=400, detail=f"유효한 티커가 2개 미만 (전달: {tickers})")
    return daily_returns[valid]


# ── Pydantic 요청 모델 ─────────────────────────────────────────────────────────

class MaxSharpeRequest(BaseModel):
    tickers:        Optional[list[str]] = None  # None이면 보유 종목 자동 사용
    period:         str = "1y"
    risk_free_rate: float = 0.04
    weight_bounds:  list[float] = [0.0, 1.0]


class BlackLittermanRequest(BaseModel):
    tickers:         Optional[list[str]] = None
    period:          str = "1y"
    regime:          str = "Sideways"   # "Bull" | "Bear" | "Sideways"
    views:           Optional[dict[str, float]] = None  # 직접 지정 시 regime 대신 사용
    view_confidence: float = 0.5
    risk_free_rate:  float = 0.04
    risk_aversion:   float = 2.5


class FactorAnalysisRequest(BaseModel):
    tickers: Optional[list[str]] = None
    weights: Optional[dict[str, float]] = None
    period:  str = "1y"


class PortfolioMCRequest(BaseModel):
    tickers:       Optional[list[str]] = None
    weights:       Optional[dict[str, float]] = None
    target_return: float = 0.15         # 연 15%
    period:        str = "1y"
    n_simulations: int = 10_000
    n_days:        int = 252


class StockMCRequest(BaseModel):
    ticker:        str
    target_price:  float
    period:        str = "1y"
    n_simulations: int = 10_000
    n_days:        int = 252


class MacroMCRequest(BaseModel):
    tickers:                 Optional[list[str]] = None
    weights:                 Optional[dict[str, float]] = None
    target_return:           float = 0.10
    rate_shock_pp:           float = 0.0
    fx_shock_pct:            float = 0.0
    sector_beta:             float = 1.0
    fundamental_drift_shim:  float = 0.0
    period:                  str = "1y"
    n_simulations:           int = 10_000
    n_days:                  int = 252


# ══════════════════════════════════════════════════════════════════════════════
# 최적화 엔드포인트
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/max-sharpe")
def max_sharpe(req: MaxSharpeRequest):
    """과거 데이터 기반 Max Sharpe Ratio 포트폴리오 최적화."""
    tickers = req.tickers or [t for t in db_get_holdings("default") if t != "CASH"]
    if not tickers:
        raise HTTPException(status_code=400, detail="보유 종목 없음")

    daily_returns = _fetch_returns(tickers, req.period)
    return optimize_max_sharpe(
        daily_returns,
        risk_free_rate=req.risk_free_rate,
        weight_bounds=tuple(req.weight_bounds),
    )


@router.post("/black-litterman")
def black_litterman(req: BlackLittermanRequest):
    """Black-Litterman + 시장 국면 시그널 결합 최적화."""
    holdings = db_get_holdings("default")
    tickers  = req.tickers or [t for t in holdings if t != "CASH"]

    if not tickers:
        raise HTTPException(status_code=400, detail="보유 종목 없음")

    daily_returns = _fetch_returns(tickers, req.period)

    # 섹터 맵 (holdings에서 추출)
    sector_map = {t: holdings.get(t, {}).get("sector", "") for t in tickers}

    # View 결정: 직접 입력 우선, 없으면 regime 기반 자동 생성
    views = req.views
    if not views:
        views = build_regime_views(
            list(daily_returns.columns), sector_map, req.regime
        )

    return optimize_black_litterman(
        daily_returns,
        views=views,
        view_confidence=req.view_confidence,
        risk_free_rate=req.risk_free_rate,
        risk_aversion=req.risk_aversion,
    )


@router.post("/factor-analysis")
def run_factor_analysis(req: FactorAnalysisRequest):
    """Fama-French 스타일 4팩터 분석 (대용 팩터 자동 생성)."""
    tickers = req.tickers or [t for t in db_get_holdings("default") if t != "CASH"]
    if not tickers:
        raise HTTPException(status_code=400, detail="보유 종목 없음")

    daily_returns = _fetch_returns(tickers, req.period)

    # 보유 비중 결정
    if req.weights:
        w = np.array([req.weights.get(t, 1.0 / len(daily_returns.columns))
                      for t in daily_returns.columns])
    else:
        w = np.full(len(daily_returns.columns), 1.0 / len(daily_returns.columns))
    w /= w.sum()

    port_returns  = (daily_returns.values @ w)
    market_ret    = daily_returns.mean(axis=1).values
    factor_df     = generate_proxy_factors(market_ret, len(port_returns))

    return factor_analysis(port_returns, factor_df)


# ══════════════════════════════════════════════════════════════════════════════
# 몬테카를로 엔드포인트
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/montecarlo/portfolio")
def mc_portfolio(req: PortfolioMCRequest):
    """포트폴리오 목표 수익률 달성 확률 (기본 GBM)."""
    tickers = req.tickers or [t for t in db_get_holdings("default") if t != "CASH"]
    if not tickers:
        raise HTTPException(status_code=400, detail="보유 종목 없음")

    daily_returns = _fetch_returns(tickers, req.period)
    cols = list(daily_returns.columns)

    if req.weights:
        w = np.array([req.weights.get(t, 1.0 / len(cols)) for t in cols])
    else:
        w = np.full(len(cols), 1.0 / len(cols))
    w /= w.sum()

    result = monte_carlo_portfolio_target(
        weights=w,
        daily_returns=daily_returns.values,
        target_return=req.target_return,
        n_simulations=req.n_simulations,
        n_days=req.n_days,
    )
    # final_returns는 크기가 크므로 요약 통계만 포함
    result.pop("final_returns", None)
    return result


@router.post("/montecarlo/stock")
def mc_stock(req: StockMCRequest):
    """개별 종목 목표 주가 달성 확률 (GBM)."""
    ticker   = req.ticker.upper()
    close_df = get_close_df([ticker], period=req.period, ttl=300)
    if close_df.empty or ticker not in close_df.columns:
        raise HTTPException(status_code=502, detail=f"{ticker} 시세 조회 실패")

    prices        = close_df[ticker].dropna()
    daily_returns = prices.pct_change().dropna().values
    current_price = float(prices.iloc[-1])

    result = monte_carlo_stock_target_price(
        current_price=current_price,
        daily_returns=daily_returns,
        target_price=req.target_price,
        n_simulations=req.n_simulations,
        n_days=req.n_days,
    )
    result.pop("final_prices", None)
    result.pop("price_paths_sample", None)
    result["current_price"] = round(current_price, 2)
    return result


@router.post("/montecarlo/macro")
def mc_macro(req: MacroMCRequest):
    """매크로 시나리오(금리·환율 충격) 반영 점프-확산 몬테카를로."""
    tickers = req.tickers or [t for t in db_get_holdings("default") if t != "CASH"]
    if not tickers:
        raise HTTPException(status_code=400, detail="보유 종목 없음")

    daily_returns = _fetch_returns(tickers, req.period)
    cols = list(daily_returns.columns)

    if req.weights:
        w = np.array([req.weights.get(t, 1.0 / len(cols)) for t in cols])
    else:
        w = np.full(len(cols), 1.0 / len(cols))
    w /= w.sum()

    port_daily   = (daily_returns.values @ w)
    close_df     = get_close_df(tickers, period=req.period, ttl=600)
    port_values  = (close_df[cols].ffill().iloc[-1].values * w).sum()

    result = monte_carlo_macro_scenario(
        current_value=float(port_values),
        daily_returns=port_daily,
        target_return=req.target_return,
        rate_shock_pp=req.rate_shock_pp,
        fx_shock_pct=req.fx_shock_pct,
        sector_beta=req.sector_beta,
        fundamental_drift_shim=req.fundamental_drift_shim,
        n_simulations=req.n_simulations,
        n_days=req.n_days,
    )
    result.pop("final_values", None)
    result.pop("final_returns", None)
    result.pop("value_paths_sample", None)
    return result


@router.get("/advanced-params")
def advanced_params(
    ticker:       str = Query(..., description="종목 티커. 예: NVDA"),
    industry_etf: str = Query(default="SPY", description="산업 ETF. 예: SOXX"),
    period:       str = Query(default="3y"),
):
    """섹터 베타 + 펀더멘탈 Drift 보정치 자동 산출."""
    return calculate_advanced_params(ticker.upper(), industry_etf, period)


@router.get("/industry-etf-map")
def industry_etf_map():
    """업종→ETF 매핑 테이블."""
    return INDUSTRY_ETF_MAP

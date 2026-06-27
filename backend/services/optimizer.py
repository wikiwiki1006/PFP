"""
backend/services/optimizer.py
──────────────────────────────
pfp/portfolio_optimizer.py의 계산 로직을 백엔드 서비스로 직접 이식.
plot_* 함수는 Streamlit용이므로 pfp/에 유지.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

try:
    from pypfopt import EfficientFrontier, expected_returns, risk_models
    from pypfopt.black_litterman import BlackLittermanModel
    _HAS_PYPFOPT = True
except ImportError:
    _HAS_PYPFOPT = False


# ══════════════════════════════════════════════════════════════════════════════
# 1. Max Sharpe Ratio 최적화
# ══════════════════════════════════════════════════════════════════════════════

def optimize_max_sharpe(
    daily_returns: pd.DataFrame,
    risk_free_rate: float = 0.04,
    weight_bounds: tuple[float, float] = (0.0, 1.0),
) -> dict:
    tickers   = list(daily_returns.columns)
    n         = len(tickers)
    mu_annual = daily_returns.mean().values * 252
    cov_annual = daily_returns.cov().values * 252

    if _HAS_PYPFOPT:
        method    = "PyPortfolioOpt"
        mu_s      = pd.Series(mu_annual, index=tickers)
        cov_df    = pd.DataFrame(cov_annual, index=tickers, columns=tickers)
        ef        = EfficientFrontier(mu_s, cov_df, weight_bounds=weight_bounds)
        ef.max_sharpe(risk_free_rate=risk_free_rate)
        cleaned   = ef.clean_weights()
        weights   = np.array([cleaned[t] for t in tickers])
        exp_ret, vol, sharpe = ef.portfolio_performance(risk_free_rate=risk_free_rate)
    else:
        method = "NumPy SLSQP 폴백"
        weights, exp_ret, vol, sharpe = _max_sharpe_numpy(
            mu_annual, cov_annual, risk_free_rate, weight_bounds
        )

    frontier = _compute_efficient_frontier(mu_annual, cov_annual, weight_bounds, n_points=40)

    eq_w      = np.full(n, 1.0 / n)
    eq_ret    = float(eq_w @ mu_annual)
    eq_vol    = float(np.sqrt(eq_w @ cov_annual @ eq_w))
    eq_sharpe = (eq_ret - risk_free_rate) / eq_vol if eq_vol > 0 else 0.0

    return {
        "weights":                 dict(zip(tickers, weights.tolist())),
        "expected_return":         exp_ret * 100,
        "volatility":              vol * 100,
        "sharpe_ratio":            sharpe,
        "method":                  method,
        "frontier":                frontier,
        "equal_weight_sharpe":     eq_sharpe,
        "equal_weight_return":     eq_ret * 100,
        "equal_weight_volatility": eq_vol * 100,
    }


def _max_sharpe_numpy(
    mu: np.ndarray,
    cov: np.ndarray,
    rf: float,
    bounds: tuple[float, float],
) -> tuple[np.ndarray, float, float, float]:
    n = len(mu)
    lo, hi = bounds

    try:
        from scipy.optimize import minimize

        def neg_sharpe(w):
            ret = w @ mu
            vol = np.sqrt(w @ cov @ w)
            return -(ret - rf) / vol if vol > 0 else 1e6

        constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]
        res = minimize(neg_sharpe, np.full(n, 1.0 / n), method="SLSQP",
                       bounds=[bounds] * n, constraints=constraints)
        w = np.clip(res.x, lo, hi)
        w /= w.sum()
    except ImportError:
        rng = np.random.default_rng(0)
        best_sharpe, best_w = -np.inf, np.full(n, 1.0 / n)
        for _ in range(20000):
            raw = rng.random(n)
            w = raw / raw.sum()
            if np.any(w < lo) or np.any(w > hi):
                continue
            v = np.sqrt(w @ cov @ w)
            s = (w @ mu - rf) / v if v > 0 else -np.inf
            if s > best_sharpe:
                best_sharpe, best_w = s, w
        w = best_w

    ret    = float(w @ mu)
    vol    = float(np.sqrt(w @ cov @ w))
    sharpe = (ret - rf) / vol if vol > 0 else 0.0
    return w, ret, vol, sharpe


def _compute_efficient_frontier(
    mu: np.ndarray,
    cov: np.ndarray,
    bounds: tuple[float, float],
    n_points: int = 40,
) -> list[dict]:
    n = len(mu)
    targets  = np.linspace(mu.min(), mu.max(), n_points)
    frontier = []

    try:
        from scipy.optimize import minimize

        for target in targets:
            def portfolio_vol(w):
                return np.sqrt(w @ cov @ w)

            constraints = [
                {"type": "eq", "fun": lambda w: np.sum(w) - 1.0},
                {"type": "eq", "fun": lambda w, t=target: w @ mu - t},
            ]
            res = minimize(portfolio_vol, np.full(n, 1.0 / n), method="SLSQP",
                           bounds=[bounds] * n, constraints=constraints)
            if res.success:
                vol = portfolio_vol(res.x)
                frontier.append({"return": float(target) * 100, "volatility": float(vol) * 100})
    except ImportError:
        rng = np.random.default_rng(1)
        lo, hi = bounds
        for _ in range(n_points * 10):
            raw = rng.random(n)
            w = raw / raw.sum()
            if np.any(w < lo) or np.any(w > hi):
                continue
            frontier.append({
                "return":     float(w @ mu) * 100,
                "volatility": float(np.sqrt(w @ cov @ w)) * 100,
            })

    return frontier


# ══════════════════════════════════════════════════════════════════════════════
# 2. Black-Litterman 최적화
# ══════════════════════════════════════════════════════════════════════════════

def build_regime_views(
    tickers: list[str],
    sector_map: dict[str, str],
    regime: str,
    aggressive_sectors: tuple[str, ...] = ("AI Infra", "Tech", "Industrial"),
    defensive_sectors: tuple[str, ...]  = ("Energy", "Materials", "Healthcare", "Cash"),
) -> dict[str, float]:
    if regime not in ("Bull", "Bear"):
        return {}
    views = {}
    for t in tickers:
        sector = sector_map.get(t, "")
        if sector in aggressive_sectors:
            views[t] = -0.15 if regime == "Bear" else 0.12
        elif sector in defensive_sectors:
            views[t] = 0.05 if regime == "Bear" else -0.03
    return views


def optimize_black_litterman(
    daily_returns: pd.DataFrame,
    market_weights: dict[str, float] | None = None,
    views: dict[str, float] | None = None,
    view_confidence: float = 0.5,
    risk_free_rate: float = 0.04,
    risk_aversion: float = 2.5,
    tau: float = 0.05,
    weight_bounds: tuple[float, float] = (0.0, 1.0),
) -> dict:
    tickers    = list(daily_returns.columns)
    n          = len(tickers)
    cov_annual = daily_returns.cov().values * 252

    if market_weights:
        w_mkt = np.array([market_weights.get(t, 0) for t in tickers], dtype=float)
        w_mkt = w_mkt / w_mkt.sum() if w_mkt.sum() > 0 else np.full(n, 1.0 / n)
    else:
        w_mkt = np.full(n, 1.0 / n)

    pi        = risk_aversion * (cov_annual @ w_mkt)
    views     = views or {}
    has_views = bool(views)

    if _HAS_PYPFOPT and has_views:
        method     = "PyPortfolioOpt Black-Litterman"
        cov_df     = pd.DataFrame(cov_annual, index=tickers, columns=tickers)
        pi_series  = pd.Series(pi, index=tickers)
        Q          = pd.Series({t: v for t, v in views.items()})
        bl         = BlackLittermanModel(
            cov_df, pi=pi_series, absolute_views=Q,
            omega="idzorek", view_confidences=[view_confidence] * len(Q),
        )
        posterior_returns = bl.bl_returns()
        posterior_cov     = bl.bl_cov()
        ef  = EfficientFrontier(posterior_returns, posterior_cov, weight_bounds=weight_bounds)
        ef.max_sharpe(risk_free_rate=risk_free_rate)
        cleaned   = ef.clean_weights()
        weights   = np.array([cleaned[t] for t in tickers])
        exp_ret, vol, sharpe = ef.portfolio_performance(risk_free_rate=risk_free_rate)
        posterior_arr = posterior_returns.values
    else:
        method = "NumPy Black-Litterman" + (" (View 없음→시장균형)" if not has_views else "")
        posterior_arr, posterior_cov_arr = _black_litterman_numpy(
            pi, cov_annual, tickers, views, view_confidence, tau
        )
        weights, exp_ret, vol, sharpe = _max_sharpe_numpy(
            posterior_arr, posterior_cov_arr, risk_free_rate, weight_bounds
        )

    eq_w      = np.full(n, 1.0 / n)
    eq_ret    = float(eq_w @ posterior_arr)
    eq_vol    = float(np.sqrt(eq_w @ cov_annual @ eq_w))
    eq_sharpe = (eq_ret - risk_free_rate) / eq_vol if eq_vol > 0 else 0.0
    frontier  = _compute_efficient_frontier(posterior_arr, cov_annual, weight_bounds, n_points=40)

    return {
        "weights":                 dict(zip(tickers, weights.tolist())),
        "expected_return":         exp_ret * 100,
        "volatility":              vol * 100,
        "sharpe_ratio":            sharpe,
        "method":                  method,
        "frontier":                frontier,
        "equal_weight_sharpe":     eq_sharpe,
        "equal_weight_return":     eq_ret * 100,
        "equal_weight_volatility": eq_vol * 100,
        "implied_returns":         dict(zip(tickers, (pi * 100).tolist())),
        "posterior_returns":       dict(zip(tickers, (posterior_arr * 100).tolist())),
        "views_applied":           {t: v * 100 for t, v in views.items()},
        "has_views":               has_views,
    }


def _black_litterman_numpy(
    pi: np.ndarray,
    cov: np.ndarray,
    tickers: list[str],
    views: dict[str, float],
    view_confidence: float,
    tau: float,
) -> tuple[np.ndarray, np.ndarray]:
    n = len(tickers)
    if not views:
        return pi, cov

    k = len(views)
    P = np.zeros((k, n))
    Q = np.zeros(k)
    for i, (t, v) in enumerate(views.items()):
        if t in tickers:
            P[i, tickers.index(t)] = 1.0
            Q[i] = v

    tau_cov     = tau * cov
    omega_diag  = np.diag(P @ tau_cov @ P.T)
    omega_diag  = omega_diag * (1.0 - view_confidence) / max(view_confidence, 0.05)
    omega_diag  = np.maximum(omega_diag, 1e-8)
    Omega       = np.diag(omega_diag)

    tau_cov_inv = np.linalg.inv(tau_cov + np.eye(n) * 1e-10)
    omega_inv   = np.linalg.inv(Omega)

    A = tau_cov_inv + P.T @ omega_inv @ P
    b = tau_cov_inv @ pi + P.T @ omega_inv @ Q
    posterior_returns = np.linalg.solve(A, b)

    return posterior_returns, cov


# ══════════════════════════════════════════════════════════════════════════════
# 3. 팩터 분석
# ══════════════════════════════════════════════════════════════════════════════

def generate_proxy_factors(
    market_returns: np.ndarray,
    n_days: int,
    seed: int = 0,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n   = min(n_days, len(market_returns))
    mkt = market_returns[-n:]
    smb = 0.15 * mkt + rng.normal(0, 0.006, n)
    hml = -0.10 * mkt + rng.normal(0, 0.006, n)
    mom = 0.05  * mkt + rng.normal(0, 0.007, n)
    return pd.DataFrame({"Market": mkt, "SMB": smb, "HML": hml, "MOM": mom})


def factor_analysis(
    portfolio_returns: np.ndarray,
    factor_returns: pd.DataFrame,
    risk_free_daily: float = 0.04 / 252,
) -> dict:
    n            = min(len(portfolio_returns), len(factor_returns))
    y            = np.asarray(portfolio_returns[-n:]) - risk_free_daily
    X            = factor_returns.iloc[-n:].values
    factor_names = list(factor_returns.columns)

    X_design = np.column_stack([np.ones(n), X])
    coeffs, *_ = np.linalg.lstsq(X_design, y, rcond=None)
    alpha_daily = coeffs[0]
    betas       = coeffs[1:]

    y_pred        = X_design @ coeffs
    ss_res        = np.sum((y - y_pred) ** 2)
    ss_tot        = np.sum((y - y.mean()) ** 2)
    r_squared     = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    residual_vol  = float(np.std(y - y_pred, ddof=X_design.shape[1]) * np.sqrt(252) * 100)

    factor_mean_annual = X.mean(axis=0) * 252
    factor_contribution = {
        name: float(beta * fm * 100)
        for name, beta, fm in zip(factor_names, betas, factor_mean_annual)
    }

    return {
        "alpha":                float(alpha_daily * 252 * 100),
        "betas":                dict(zip(factor_names, [float(b) for b in betas])),
        "r_squared":            float(r_squared),
        "factor_contribution":  factor_contribution,
        "residual_vol":         residual_vol,
    }

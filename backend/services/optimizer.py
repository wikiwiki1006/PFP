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


def _ratio_undefined_reason(ratio: float | None, vol: float) -> "str | None":
    """비율이 성립하지 **않는** 이유. 성립하면 None.

    판정을 여기 한 곳에 둔다 — 호출부가 각자 판정하면 세 곳이 다른 이유를
    붙일 수 있고, 그게 이 파일에서 하한이 셋(`> 0` · `> 1e-12` · 프론트
    `> 0`)으로 갈렸던 원인이다. 값은 `_ratio_or_none` 이, 이유는 이 함수가
    같은 판정에서 나온다.

    분모를 **먼저** 본다. 호출자가 이미 None 을 만들어 넘겼어도(폴백 경로가
    그 형태다) 이유는 분모에서 나오기 때문이다.
    """
    import math

    from backend.services.portfolio_calculator import MIN_VOL_FOR_RATIO

    if vol is None or not math.isfinite(float(vol)):
        return "no_volatility"
    if float(vol) <= MIN_VOL_FOR_RATIO:
        return "no_volatility"
    if ratio is None or not math.isfinite(float(ratio)):
        return "not_finite"
    return None


def _ratio_or_none(ratio: float | None, vol: float) -> "float | None":
    """분모(변동성)가 너무 작으면 비율을 내보내지 않는다.

    판정은 `_ratio_undefined_reason` 이 한다 — 이 함수는 그 판정을 값으로
    옮기기만 한다. 둘이 갈리면 "값은 있는데 이유도 있다" 같은 응답이 나온다.

    `vol > 0` 으로만 걸렀을 때 무엇이 나갔는지 실측했다. 매일 정확히 +0.05%
    오르는 결정론적 입력(연 +13.42%)의 동일비중 변동성은 **0 이 아니라
    1.803e-15** 다 — 부동소수 잡음이다. `> 0` 을 통과해서
    `equal_weight_sharpe = 47,693,150,800,467.95` 가 응답에 실렸다.

    즉 이 자리의 위장은 두 방향이었다: 분모가 **정확히** 0 이면 `0.0`
    (화면에서 '위험조정수익 없음' = 최악으로 읽힌다), 잡음만큼 양수면
    천문학적 숫자가 측정값인 얼굴로 나간다. 둘 다 하한 하나로 막힌다.

    하한은 `portfolio_calculator.MIN_VOL_FOR_RATIO` 한 곳에서 읽는다.
    """
    return None if _ratio_undefined_reason(ratio, vol) is not None else float(ratio)


# ══════════════════════════════════════════════════════════════════════════════
# 1. Max Sharpe Ratio 최적화
# ══════════════════════════════════════════════════════════════════════════════

def optimize_max_sharpe(
    daily_returns: pd.DataFrame,
    risk_free_rate: float = 0.04,
    weight_bounds: tuple[float, float] = (0.0, 1.0),
) -> dict:
    """과거 데이터 기반 Max Sharpe 최적화. `/api/optimizer/max-sharpe` 가 이걸 그대로 반환한다.

    ## 반환 계약 — 어떤 필드가 언제 null 인가

    **Pydantic 응답 모델이 없다** (`routers/optimizer.py` 에 `response_model`
    이 없다). 그래서 이 docstring 이 유일한 계약이다. 프론트 타입
    (`OptimizationResult`)은 호출부가 0곳이라 삭제됐으므로, 다음 소비자가
    읽을 곳은 여기뿐이다.

        sharpe_ratio          **nullable**. 분모(변동성)가
                              `MIN_VOL_FOR_RATIO` 이하면 null.
        sharpe_reason         null 이 아닐 때만 문자열:
                              `"no_volatility"` · `"not_finite"`
        equal_weight_sharpe   **nullable**. 위와 같은 규칙·같은 판정자.
        equal_weight_sharpe_reason  같은 코드 집합.

        weights · expected_return · volatility
        equal_weight_return · equal_weight_volatility · method · frontier
                              → **null 이 아니다.** 입력에 NaN/Inf 가 있으면
                              pypfopt 가 `ValueError` 를 던져 응답 자체가
                              만들어지지 않는다 (실측: 한 종목이 전부 NaN 인
                              프레임 → `Problem data contains NaN or Inf`).
                              즉 "값이 있으면 유한하다".

    실측으로 확인한 null 경로 (결정론적 +0.05%/일 입력, 변동성 1.8e-15):
        sharpe_ratio None · equal_weight_sharpe None · 나머지는 유한한 수

    `sharpe_ratio` 가 nullable 이 된 것은 그 전에 **4.8e13** 이 측정값 얼굴로
    나갔기 때문이다 — `_ratio_or_none` 의 docstring 에 그 실측이 있다.
    """
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
        sharpe_reason = _ratio_undefined_reason(sharpe, vol)
        sharpe = _ratio_or_none(sharpe, vol)
    else:
        method = "NumPy SLSQP 폴백"
        weights, exp_ret, vol, sharpe = _max_sharpe_numpy(
            mu_annual, cov_annual, risk_free_rate, weight_bounds
        )
        # 폴백도 같은 판정을 통과시킨다 — 이유 코드가 경로에 따라 달라지면
        # 소비자가 두 가지를 다뤄야 한다.
        sharpe_reason = _ratio_undefined_reason(sharpe, vol)
        sharpe = _ratio_or_none(sharpe, vol)

    frontier = _compute_efficient_frontier(mu_annual, cov_annual, weight_bounds, n_points=40)

    eq_w      = np.full(n, 1.0 / n)
    eq_ret    = float(eq_w @ mu_annual)
    eq_vol    = float(np.sqrt(eq_w @ cov_annual @ eq_w))
    # 동일비중 비교군의 샤프도 같은 규칙·같은 판정자를 쓴다.
    eq_raw = (eq_ret - risk_free_rate) / eq_vol if eq_vol else None
    eq_reason = _ratio_undefined_reason(eq_raw, eq_vol)
    eq_sharpe = _ratio_or_none(eq_raw, eq_vol)

    return {
        "weights":                 dict(zip(tickers, weights.tolist())),
        "expected_return":         exp_ret * 100,
        "volatility":              vol * 100,
        "sharpe_ratio":            sharpe,
        # 샤프가 null 인 이유. `no_volatility` = 분모가 하한 이하,
        # `not_finite` = 비율이 NaN/Inf. 값이 있으면 null.
        "sharpe_reason":           sharpe_reason,
        "method":                  method,
        "frontier":                frontier,
        "equal_weight_sharpe":     eq_sharpe,
        "equal_weight_sharpe_reason": eq_reason,
        "equal_weight_return":     eq_ret * 100,
        "equal_weight_volatility": eq_vol * 100,
    }


def _max_sharpe_numpy(
    mu: np.ndarray,
    cov: np.ndarray,
    rf: float,
    bounds: tuple[float, float],
) -> "tuple[np.ndarray, float, float, float | None]":
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
    # 변동성 0 이면 샤프는 정의되지 않는다 — `0.0` 은 화면에서 최악으로
    # 읽히는데 실제로는 위험이 없다는 뜻이다. 하한은 한 곳에서 읽는다.
    # (위 `neg_sharpe` 의 `1e6` 과 랜덤 탐색의 `-inf` 는 **최적화 목적함수의
    # 벌점**이라 그대로 둔다 — 보고되는 값이 아니다.)
    from backend.services.portfolio_calculator import MIN_VOL_FOR_RATIO
    sharpe = (ret - rf) / vol if vol > MIN_VOL_FOR_RATIO else None
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
    """Black-Litterman + 국면 뷰. `/api/optimizer/black-litterman` 이 그대로 반환한다.

    ## 반환 계약 — 어떤 필드가 언제 null 인가

    `optimize_max_sharpe` 와 **같다** (응답 모델 없음 · 이 docstring 이 계약):

        sharpe_ratio · sharpe_reason
        equal_weight_sharpe · equal_weight_sharpe_reason
                              → nullable. 분모가 `MIN_VOL_FOR_RATIO` 이하면
                                null 이고 이유 코드가 `"no_volatility"`.

        weights · expected_return · volatility · equal_weight_return
        equal_weight_volatility · implied_returns · posterior_returns
        views_applied · has_views · method · frontier
                              → null 이 아니다 (NaN 입력은 `ValueError` 로
                                응답 전에 끊긴다).

    `has_views` 가 False 면 뷰 없이 시장균형(implied)만으로 계산한 결과다 —
    `method` 문자열에도 `(View 없음→시장균형)` 이 붙는다. 그건 실패가 아니라
    다른 계산이므로 값이 비지 않는다.
    """
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
        sharpe_reason = _ratio_undefined_reason(sharpe, vol)
        sharpe = _ratio_or_none(sharpe, vol)
        posterior_arr = posterior_returns.values
    else:
        method = "NumPy Black-Litterman" + (" (View 없음→시장균형)" if not has_views else "")
        posterior_arr, posterior_cov_arr = _black_litterman_numpy(
            pi, cov_annual, tickers, views, view_confidence, tau
        )
        weights, exp_ret, vol, sharpe = _max_sharpe_numpy(
            posterior_arr, posterior_cov_arr, risk_free_rate, weight_bounds
        )
        sharpe_reason = _ratio_undefined_reason(sharpe, vol)
        sharpe = _ratio_or_none(sharpe, vol)

    eq_w      = np.full(n, 1.0 / n)
    eq_ret    = float(eq_w @ posterior_arr)
    eq_vol    = float(np.sqrt(eq_w @ cov_annual @ eq_w))
    # 동일비중 비교군의 샤프도 같은 규칙·같은 판정자를 쓴다.
    eq_raw = (eq_ret - risk_free_rate) / eq_vol if eq_vol else None
    eq_reason = _ratio_undefined_reason(eq_raw, eq_vol)
    eq_sharpe = _ratio_or_none(eq_raw, eq_vol)
    frontier  = _compute_efficient_frontier(posterior_arr, cov_annual, weight_bounds, n_points=40)

    return {
        "weights":                 dict(zip(tickers, weights.tolist())),
        "expected_return":         exp_ret * 100,
        "volatility":              vol * 100,
        "sharpe_ratio":            sharpe,
        # 샤프가 null 인 이유 (값이 있으면 null). HRP 카드
        # (`portfolio_optimizer`)와 같은 코드 집합을 쓴다.
        "sharpe_reason":           sharpe_reason,
        "method":                  method,
        "frontier":                frontier,
        "equal_weight_sharpe":     eq_sharpe,
        "equal_weight_sharpe_reason": eq_reason,
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

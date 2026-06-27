"""
backend/services/monte_carlo.py
────────────────────────────────
pfp/monte_carlo.py의 계산 로직을 백엔드 서비스로 직접 이식.
plot_* 함수는 Streamlit용이므로 pfp/에 유지.
numpy 배열을 JSON-직렬화 가능한 형태로 반환하기 위해
final_returns / price_paths_sample 등은 tolist() 처리.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# ── 역사적 매크로 충격 상수 ─────────────────────────────────────────────────────
RATE_DRIFT_IMPACT_PER_PP  = -0.10
RATE_VOL_IMPACT_PER_PP    = +0.15
FX_DRIFT_IMPACT_PER_10PCT = -0.06
FX_VOL_IMPACT_PER_10PCT   = +0.10

INDUSTRY_ETF_MAP: dict[str, str] = {
    "반도체":    "SOXX", "AI Infra":  "SOXX",
    "기술/IT":   "QQQ",  "Tech":      "QQQ",
    "성장주":    "SPY",
    "에너지":    "XLE",  "Energy":    "XLE",
    "유틸리티":  "XLU",
    "금융":      "XLF",
    "산업재":    "XLI",  "Industrial":"XLI",
    "소재":      "XLB",  "Materials": "XLB",
    "헬스케어":  "XLV",
    "필수소비재":"XLP",
    "임의소비재":"XLY",
    "리츠/부동산":"XLRE",
}


# ══════════════════════════════════════════════════════════════════════════════
# 0. 샘플 데이터 생성 (테스트용)
# ══════════════════════════════════════════════════════════════════════════════

def generate_sample_returns(
    n_assets: int = 2,
    n_days: int = 252 * 3,
    annual_mu: list[float] | None = None,
    annual_sigma: list[float] | None = None,
    corr: float = 0.3,
    seed: int = 42,
) -> np.ndarray:
    rng          = np.random.default_rng(seed)
    annual_mu    = np.array(annual_mu    or [0.10, 0.07][:n_assets])
    annual_sigma = np.array(annual_sigma or [0.25, 0.15][:n_assets])
    daily_mu     = annual_mu / 252
    daily_sigma  = annual_sigma / np.sqrt(252)
    corr_matrix  = np.full((n_assets, n_assets), corr)
    np.fill_diagonal(corr_matrix, 1.0)
    cov = np.outer(daily_sigma, daily_sigma) * corr_matrix
    return rng.multivariate_normal(daily_mu, cov, size=n_days)


# ══════════════════════════════════════════════════════════════════════════════
# 1. 포트폴리오 목표 수익률 달성 확률
# ══════════════════════════════════════════════════════════════════════════════

def monte_carlo_portfolio_target(
    weights: np.ndarray,
    daily_returns: np.ndarray,
    target_return: float,
    n_simulations: int = 10_000,
    n_days: int = 252,
    seed: int | None = None,
) -> dict:
    weights = np.asarray(weights, dtype=float)
    if not np.isclose(weights.sum(), 1.0):
        weights = weights / weights.sum()

    mu       = daily_returns.mean(axis=0)
    cov      = np.atleast_2d(np.cov(daily_returns, rowvar=False))
    n_assets = len(mu)

    rng = np.random.default_rng(seed)
    L   = np.linalg.cholesky(cov + np.eye(n_assets) * 1e-12)
    Z   = rng.standard_normal((n_simulations, n_days, n_assets))

    asset_daily = mu + Z @ L.T
    port_daily  = asset_daily @ weights
    growth      = np.prod(1.0 + port_daily, axis=1)
    final_returns = growth - 1.0

    probability = float(np.sum(final_returns > target_return) / n_simulations * 100)

    return {
        "probability":    probability,
        "final_returns":  final_returns.tolist(),
        "message":        f"목표 수익률 {target_return*100:.1f}%를 달성할 확률은 {probability:.2f}%입니다.",
        "mean_return":    float(final_returns.mean() * 100),
        "median_return":  float(np.median(final_returns) * 100),
        "var_95":         float(np.percentile(final_returns, 5) * 100),
    }


# ══════════════════════════════════════════════════════════════════════════════
# 2. 개별 종목 목표 주가 달성 확률 (GBM)
# ══════════════════════════════════════════════════════════════════════════════

def monte_carlo_stock_target_price(
    current_price: float,
    daily_returns: np.ndarray,
    target_price: float,
    n_simulations: int = 10_000,
    n_days: int = 252,
    seed: int | None = None,
) -> dict:
    daily_returns = np.asarray(daily_returns, dtype=float).ravel()
    mu    = daily_returns.mean()
    sigma = daily_returns.std(ddof=1)

    rng  = np.random.default_rng(seed)
    drift = mu - 0.5 * sigma ** 2
    Z     = rng.standard_normal((n_simulations, n_days))
    log_r = drift + sigma * Z
    cum_log = np.cumsum(log_r, axis=1)
    price_paths = current_price * np.exp(cum_log)
    final_prices = price_paths[:, -1]

    direction_up = target_price >= current_price
    if direction_up:
        prob_final = float(np.sum(final_prices >= target_price) / n_simulations * 100)
        prob_touch = float(np.any(price_paths >= target_price, axis=1).sum() / n_simulations * 100)
    else:
        prob_final = float(np.sum(final_prices <= target_price) / n_simulations * 100)
        prob_touch = float(np.any(price_paths <= target_price, axis=1).sum() / n_simulations * 100)

    direction_label = "이상 도달" if direction_up else "이하 하락"
    sample_n = min(200, n_simulations)
    sample_paths = np.column_stack([
        np.full(sample_n, current_price), price_paths[:sample_n, :]
    ])

    return {
        "probability_final":    prob_final,
        "probability_touch":    prob_touch,
        "final_prices":         final_prices.tolist(),
        "price_paths_sample":   sample_paths.tolist(),
        "message": (
            f"목표 주가 {target_price:,.2f}에 {direction_label}할 확률은 "
            f"(1년 후 종가 기준) {prob_final:.2f}%, "
            f"(1년 내 터치 기준) {prob_touch:.2f}%입니다."
        ),
    }


# ══════════════════════════════════════════════════════════════════════════════
# 3. 매크로 시나리오 (점프-확산 + GBM 보정)
# ══════════════════════════════════════════════════════════════════════════════

def macro_drift_vol_multipliers(
    rate_shock_pp: float,
    fx_shock_pct: float,
    sector_beta: float = 1.0,
) -> dict:
    mu_shift_rate = RATE_DRIFT_IMPACT_PER_PP * rate_shock_pp * sector_beta
    if rate_shock_pp >= 0:
        vol_shift_rate = RATE_VOL_IMPACT_PER_PP * rate_shock_pp * sector_beta
    else:
        vol_shift_rate = RATE_VOL_IMPACT_PER_PP * 0.3 * rate_shock_pp * sector_beta

    mu_shift_fx = FX_DRIFT_IMPACT_PER_10PCT * (fx_shock_pct / 10.0) * sector_beta
    if fx_shock_pct >= 0:
        vol_shift_fx = FX_VOL_IMPACT_PER_10PCT * (fx_shock_pct / 10.0) * sector_beta
    else:
        vol_shift_fx = FX_VOL_IMPACT_PER_10PCT * 0.3 * (fx_shock_pct / 10.0) * sector_beta

    mu_total  = mu_shift_rate + mu_shift_fx
    vol_total = vol_shift_rate + vol_shift_fx
    return {
        "k_mu":            1.0 + mu_total,
        "k_sigma":         max(1.0 + vol_total, 0.5),
        "mu_shift_pct":    mu_total * 100,
        "sigma_shift_pct": vol_total * 100,
    }


def _macro_to_jump_params(
    rate_shock_pp: float,
    fx_shock_pct: float,
    base_annual_vol: float,
) -> dict:
    rate_stress = max(rate_shock_pp, 0) / 2.0
    fx_stress   = max(fx_shock_pct, 0) / 10.0
    relief      = max(-rate_shock_pp, 0) / 2.0 + max(-fx_shock_pct, 0) / 10.0
    stress      = float(np.clip(
        np.sqrt(rate_stress**2 + fx_stress**2) / np.sqrt(2) - relief * 0.3, 0, 1.0
    ))
    return {
        "lambda_base":   2.0 / 252 * (1 + stress * 2.0),
        "self_excite":   min(0.04 + stress * 0.10, 0.20),
        "excite_decay":  0.85 + stress * 0.06,
        "jump_mean":     -0.02 - stress * 0.03,
        "jump_std":      0.015 + stress * 0.01,
        "stress_level":  stress,
    }


def monte_carlo_macro_scenario(
    current_value: float,
    daily_returns: np.ndarray,
    target_return: float,
    rate_shock_pp: float = 0.0,
    fx_shock_pct: float  = 0.0,
    sector_beta: float = 1.0,
    fundamental_drift_shim: float = 0.0,
    n_simulations: int = 10_000,
    n_days: int = 252,
    seed: int | None = None,
) -> dict:
    daily_returns = np.asarray(daily_returns, dtype=float).ravel()
    mu_base       = daily_returns.mean() + fundamental_drift_shim
    sigma_base    = daily_returns.std(ddof=1)
    annual_vol    = sigma_base * np.sqrt(252)

    mv          = macro_drift_vol_multipliers(rate_shock_pp, fx_shock_pct, sector_beta)
    mu_diff     = mu_base * mv["k_mu"]
    sigma_diff  = sigma_base * mv["k_sigma"]
    jp          = _macro_to_jump_params(rate_shock_pp, fx_shock_pct, annual_vol)

    rng   = np.random.default_rng(seed)
    drift = mu_diff - 0.5 * sigma_diff ** 2
    Z     = rng.standard_normal((n_simulations, n_days))
    diffusion = drift + sigma_diff * Z

    lambda_t      = np.full((n_simulations, n_days), jp["lambda_base"])
    jump_occurred = np.zeros((n_simulations, n_days), dtype=bool)
    jump_sizes    = np.zeros((n_simulations, n_days))
    excite_decay  = jp["excite_decay"]
    lambda_cap    = jp["lambda_base"] * 6.0

    for day in range(n_days):
        if day > 0:
            lambda_t[:, day] = (
                jp["lambda_base"]
                + excite_decay * (lambda_t[:, day - 1] - jp["lambda_base"])
                + jp["self_excite"] * jump_occurred[:, day - 1]
            )
            lambda_t[:, day] = np.minimum(lambda_t[:, day], lambda_cap)
        u        = rng.random(n_simulations)
        occurred = u < np.clip(lambda_t[:, day], 0, 0.35)
        jump_occurred[:, day] = occurred
        sizes                 = rng.normal(jp["jump_mean"], jp["jump_std"], n_simulations)
        jump_sizes[:, day]    = np.where(occurred, sizes, 0.0)

    total_log     = diffusion + jump_sizes
    cum_log       = np.cumsum(total_log, axis=1)
    value_paths   = current_value * np.exp(cum_log)
    final_values  = value_paths[:, -1]
    final_returns = final_values / current_value - 1.0

    target_value = current_value * (1 + target_return)
    direction_up = target_return >= 0
    if direction_up:
        prob_final = float(np.sum(final_values >= target_value) / n_simulations * 100)
        prob_touch = float(np.any(value_paths >= target_value, axis=1).sum() / n_simulations * 100)
    else:
        prob_final = float(np.sum(final_values <= target_value) / n_simulations * 100)
        prob_touch = float(np.any(value_paths <= target_value, axis=1).sum() / n_simulations * 100)

    var_95  = float(np.percentile(final_returns, 5) * 100)
    cvar_95 = float(final_returns[final_returns <= np.percentile(final_returns, 5)].mean() * 100)

    sample_n = min(150, n_simulations)
    sample_paths = np.column_stack([
        np.full(sample_n, current_value), value_paths[:sample_n, :]
    ])

    direction_label = "이상 달성" if direction_up else "이하 하락"
    shim_note = (
        f" · 펀더멘탈 보정 +{fundamental_drift_shim*252*100:.1f}%/년"
        if abs(fundamental_drift_shim) > 1e-9 else ""
    )
    message = (
        f"목표 수익률 {target_return*100:+.1f}%에 {direction_label}할 확률은 "
        f"(종가 기준) {prob_final:.2f}%, (터치 기준) {prob_touch:.2f}%입니다. "
        f"Drift {mv['mu_shift_pct']:+.1f}% · Vol {mv['sigma_shift_pct']:+.1f}% 보정{shim_note} · "
        f"패닉 강도: {jp['stress_level']:.2f}"
    )

    return {
        "probability_final":               prob_final,
        "probability_touch":               prob_touch,
        "final_values":                    final_values.tolist(),
        "final_returns":                   final_returns.tolist(),
        "value_paths_sample":              sample_paths.tolist(),
        "var_95":                          var_95,
        "cvar_95":                         cvar_95,
        "stress_level":                    jp["stress_level"],
        "jump_params":                     jp,
        "mu_sigma_multipliers":            mv,
        "fundamental_drift_shim_applied":  fundamental_drift_shim,
        "message":                         message,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 4. 섹터 베타 + 펀더멘탈 Drift Shim 자동 계산
# ══════════════════════════════════════════════════════════════════════════════

def calculate_advanced_params(
    ticker: str,
    industry_etf: str = "SPY",
    period: str = "3y",
) -> dict:
    result = {
        "sector_beta": 1.0, "fundamental_drift_shim": 0.0,
        "annual_drift_bonus_pct": 0.0, "forward_pe": None,
        "trailing_pe": None, "peg_ratio": None, "regime": "데이터 부족",
        "is_fallback": False, "industry_etf": industry_etf, "warnings": [],
    }
    try:
        import yfinance as yf
    except ImportError:
        result["is_fallback"] = True
        result["warnings"].append("yfinance 모듈을 찾을 수 없습니다.")
        return result

    try:
        data = yf.download([ticker, industry_etf], period=period,
                           progress=False, auto_adjust=True)["Close"].dropna()
        if ticker not in data.columns or industry_etf not in data.columns or len(data) < 30:
            raise ValueError("가격 데이터 부족")
        stock_ret = data[ticker].pct_change().dropna()
        etf_ret   = data[industry_etf].pct_change().dropna()
        common    = stock_ret.index.intersection(etf_ret.index)
        cov_m     = np.cov(stock_ret.loc[common].values, etf_ret.loc[common].values)
        etf_var   = cov_m[1, 1]
        if etf_var <= 1e-12:
            raise ValueError("ETF 변동성 0")
        result["sector_beta"] = float(np.clip(cov_m[0, 1] / etf_var, -3.0, 5.0))
    except Exception as e:
        result["is_fallback"] = True
        result["warnings"].append(f"Sector Beta 계산 실패 → 기본값 1.0 ({type(e).__name__})")

    try:
        import yfinance as yf
        info        = yf.Ticker(ticker).info
        forward_pe  = info.get("forwardPE")
        trailing_pe = info.get("trailingPE")
        peg_ratio   = info.get("pegRatio") or info.get("trailingPegRatio")
        result["forward_pe"]  = round(forward_pe, 2)  if forward_pe  else None
        result["trailing_pe"] = round(trailing_pe, 2) if trailing_pe else None
        result["peg_ratio"]   = round(peg_ratio, 2)   if peg_ratio   else None

        annual_bonus = 0.0
        regime_tags  = []
        if peg_ratio and peg_ratio > 0:
            if peg_ratio < 1.0:
                annual_bonus += 0.03; regime_tags.append("저평가/고성장(PEG<1)")
            elif peg_ratio < 1.5:
                annual_bonus += 0.015; regime_tags.append("적정평가/성장(PEG<1.5)")
        if forward_pe and trailing_pe and forward_pe > 0 and trailing_pe > 0:
            if forward_pe < trailing_pe:
                ratio = (trailing_pe - forward_pe) / trailing_pe
                annual_bonus += min(ratio * 0.05, 0.015)
                regime_tags.append("이익개선 기대(Fwd PE<Trailing PE)")

        annual_bonus = min(annual_bonus, 0.03)
        result["annual_drift_bonus_pct"]   = annual_bonus * 100
        result["fundamental_drift_shim"]   = annual_bonus / 252
        result["regime"] = " · ".join(regime_tags) if regime_tags else "중립"
    except Exception as e:
        result["is_fallback"] = True
        result["warnings"].append(f"펀더멘탈 지표 조회 실패 → Shim=0.0 ({type(e).__name__})")

    return result

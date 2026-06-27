"""
services/portfolio_calculator.py
──────────────────────────────────
포트폴리오 계산 로직. alpha_terminal.py에서 추출.
Streamlit / yfinance 의존 없음 — 순수 NumPy/Pandas.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# ── 에쿼티 커브 ────────────────────────────────────────────────────────────────

def build_equity_curve(
    holdings: dict,
    trade_log: list,
    close_df: pd.DataFrame,
) -> pd.Series:
    """
    실제 매매 이력(trade_log)을 재생해 날짜별 포트폴리오 가치를 산출.
    trade_log가 없으면 현재 보유수량 고정 방식으로 폴백.
    """
    stock_tickers = [t for t in holdings if t != "CASH" and t in close_df.columns]
    cash_val = holdings.get("CASH", {}).get("q", 0)

    if not trade_log:
        vals = close_df[stock_tickers].multiply([holdings[t]["q"] for t in stock_tickers])
        return vals.sum(axis=1) + cash_val

    try:
        log_df = pd.DataFrame(trade_log)
        log_df["date"] = pd.to_datetime(log_df["date"])
        log_df = log_df.sort_values("date")
        logged_tickers = set(log_df["ticker"].unique())

        holdings_matrix = pd.DataFrame(0.0, index=close_df.index, columns=stock_tickers)
        for t in stock_tickers:
            t_log = log_df[log_df["ticker"] == t].sort_values("date")
            if t_log.empty:
                continue
            qty_series = pd.Series(0.0, index=close_df.index)
            running = 0.0
            for _, row in t_log.iterrows():
                q = float(row.get("q", 0))
                if row["type"] == "ADD":
                    running += q
                elif row["type"] == "SOLD":
                    running -= q
                elif row["type"] == "UPDATE":
                    running = q
                qty_series.loc[qty_series.index >= row["date"]] = running
            holdings_matrix[t] = qty_series

        for t in stock_tickers:
            if t not in logged_tickers and holdings[t]["q"] > 0:
                holdings_matrix[t] = holdings[t]["q"]

        equity = (close_df[stock_tickers] * holdings_matrix).sum(axis=1) + cash_val

        if equity.iloc[-1] <= 0 or equity.replace(0, np.nan).dropna().empty:
            raise ValueError("비정상 에쿼티 커브")
        return equity

    except Exception:
        vals = close_df[stock_tickers].multiply([holdings[t]["q"] for t in stock_tickers])
        return vals.sum(axis=1) + cash_val


def equity_curve_to_records(
    curve: pd.Series,
    benchmark_df: pd.DataFrame | None = None,
) -> list[dict]:
    """에쿼티 커브를 API 응답용 레코드 리스트로 변환."""
    perf = (curve / curve.iloc[0] - 1) * 100

    sp500_perf = None
    if benchmark_df is not None and "^GSPC" in benchmark_df.columns:
        b = benchmark_df["^GSPC"].reindex(curve.index).ffill()
        sp500_perf = (b / b.iloc[0] - 1) * 100

    records = []
    for date, p_val in perf.items():
        rec = {
            "date":      date.strftime("%Y-%m-%d"),
            "portfolio": round(float(p_val), 4),
            "sp500":     round(float(sp500_perf.loc[date]), 4) if sp500_perf is not None and date in sp500_perf.index else None,
        }
        records.append(rec)
    return records


# ── 포트폴리오 베타 ────────────────────────────────────────────────────────────

def calculate_portfolio_beta(
    holdings: dict,
    close_df: pd.DataFrame,
    benchmark: str = "^GSPC",
) -> float:
    try:
        if benchmark not in close_df.columns:
            return 1.0
        mkt_ret = close_df[benchmark].pct_change().dropna()
        mkt_var = mkt_ret.var()
        if mkt_var <= 1e-12:
            return 1.0

        stock_tickers = [t for t in holdings if t != "CASH" and t in close_df.columns]
        if not stock_tickers:
            return 1.0

        latest = close_df.iloc[-1]
        values, betas = [], []
        for t in stock_tickers:
            s_ret = close_df[t].pct_change().dropna()
            common = s_ret.index.intersection(mkt_ret.index)
            if len(common) < 30:
                beta_t = 1.0
            else:
                cov = np.cov(s_ret.loc[common], mkt_ret.loc[common])[0, 1]
                beta_t = cov / mkt_var if mkt_var > 0 else 1.0
            v = float(latest.get(t, 0)) * holdings[t]["q"]
            values.append(v)
            betas.append(beta_t)

        cash_val = holdings.get("CASH", {}).get("q", 0)
        total_val = sum(values) + cash_val
        if total_val <= 0:
            return 1.0

        weighted = sum(v * b for v, b in zip(values, betas)) / total_val
        return float(np.clip(weighted, -2.0, 3.0))
    except Exception:
        return 1.0


# ── 핵심 지표 계산 ─────────────────────────────────────────────────────────────

def calculate_metrics(
    holdings: dict,
    close_df: pd.DataFrame,
    equity_curve: pd.Series,
) -> dict:
    if close_df.empty or len(close_df) < 2:
        return {}

    curr = close_df.iloc[-1]
    prev = close_df.iloc[-2]

    def _price(t):
        return float(curr.get(t, 0)) if t != "CASH" else 1.0

    def _prev_price(t):
        return float(prev.get(t, _price(t))) if t != "CASH" and t in prev.index else _price(t)

    stock_tickers = [t for t in holdings if t != "CASH" and t in close_df.columns]
    cash_val = holdings.get("CASH", {}).get("q", 0)

    total_equity  = sum(_price(t)      * holdings[t]["q"] for t in stock_tickers) + cash_val
    prev_equity   = sum(_prev_price(t) * holdings[t]["q"] for t in stock_tickers) + cash_val
    total_cost    = sum(holdings[t]["avg"] * holdings[t]["q"] for t in stock_tickers) + cash_val
    today_chg_val = total_equity - prev_equity
    today_chg_pct = (today_chg_val / prev_equity * 100) if prev_equity else 0.0
    total_rtn     = (total_equity / total_cost - 1) * 100 if total_cost else 0.0

    def _perf(days):
        if len(equity_curve) >= days + 1:
            base = float(equity_curve.iloc[-(days + 1)])
            return (total_equity / base - 1) * 100 if base else 0.0
        return 0.0

    beta = calculate_portfolio_beta(holdings, close_df)
    vix  = float(curr.get("^VIX", 18.0))

    alpha = None
    if "^GSPC" in close_df.columns:
        p_perf = (equity_curve / equity_curve.iloc[0] - 1) * 100
        b_sp   = close_df["^GSPC"].reindex(equity_curve.index).ffill()
        b_perf = (b_sp / b_sp.iloc[0] - 1) * 100
        try:
            alpha = round(float(p_perf.iloc[-1]) - float(b_perf.iloc[-1]), 4)
        except Exception:
            pass

    return {
        "total_equity":      round(total_equity, 2),
        "total_cost":        round(total_cost, 2),
        "total_return_pct":  round(total_rtn, 4),
        "today_change_val":  round(today_chg_val, 2),
        "today_change_pct":  round(today_chg_pct, 4),
        "portfolio_beta":    round(beta, 4),
        "vix":               round(vix, 2),
        "perf_1w":           round(_perf(5), 4),
        "perf_1m":           round(_perf(21), 4),
        "alpha_vs_sp500":    round(alpha, 4) if alpha is not None else None,
    }


# ── 보유 종목 상세 ─────────────────────────────────────────────────────────────

def get_holdings_detail(holdings: dict, close_df: pd.DataFrame) -> list[dict]:
    if close_df.empty or len(close_df) < 2:
        return []

    curr = close_df.iloc[-1]
    prev = close_df.iloc[-2]

    total_equity = sum(
        float(curr.get(t, 0)) * info["q"]
        for t, info in holdings.items() if t != "CASH" and t in close_df.columns
    ) + holdings.get("CASH", {}).get("q", 0)

    rows = []
    for t, info in holdings.items():
        if t == "CASH":
            price = 1.0
            chg_pct = 0.0
            pnl_pct = 0.0
        else:
            price = float(curr.get(t, 0)) if t in close_df.columns else 0.0
            p_price = float(prev.get(t, price)) if t in prev.index else price
            chg_pct = (price / p_price - 1) * 100 if p_price else 0.0
            pnl_pct = (price / info["avg"] - 1) * 100 if info["avg"] > 0 else 0.0

        value = price * info["q"]
        weight = (value / total_equity * 100) if total_equity else 0.0

        rows.append({
            "ticker":     t,
            "avg":        round(float(info["avg"]), 2),
            "shares":     float(info["q"]),
            "price":      round(price, 2),
            "chg_pct":    round(chg_pct, 4),
            "pnl_pct":    round(pnl_pct, 4),
            "value":      round(value, 2),
            "weight_pct": round(weight, 2),
        })
    return rows


# ── 팩터 분석 (Fama-French 스타일) ────────────────────────────────────────────

def factor_analysis(portfolio_returns: pd.Series, close_df: pd.DataFrame) -> dict:
    """
    포트폴리오 수익률을 시장/모멘텀/가치 팩터에 회귀해 노출도 산출.
    데이터 부족 시 빈 dict 반환.
    """
    try:
        mkt = close_df.get("^GSPC") or close_df.get("SPY")
        if mkt is None:
            return {}

        mkt_ret = mkt.pct_change().dropna()
        common = portfolio_returns.index.intersection(mkt_ret.index)
        if len(common) < 60:
            return {}

        p = portfolio_returns.loc[common]
        m = mkt_ret.loc[common]

        # OLS 회귀: portfolio = alpha + beta * market
        X = np.column_stack([np.ones(len(m)), m.values])
        y = p.values
        try:
            coef, *_ = np.linalg.lstsq(X, y, rcond=None)
            alpha_ann = coef[0] * 252
            market_beta = coef[1]
            y_hat = X @ coef
            ss_res = np.sum((y - y_hat) ** 2)
            ss_tot = np.sum((y - y.mean()) ** 2)
            r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
        except Exception:
            return {}

        return {
            "alpha_annualized": round(float(alpha_ann), 4),
            "market_beta":      round(float(market_beta), 4),
            "r_squared":        round(float(r2), 4),
        }
    except Exception:
        return {}

"""
services/portfolio_calculator.py
──────────────────────────────────
포트폴리오 계산 로직. alpha_terminal.py에서 추출.
Streamlit / yfinance 의존 없음 — 순수 NumPy/Pandas.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def _safe(v, default: float = 0.0) -> float:
    """NaN/Inf/None → default. JSON-safe 숫자 보장."""
    try:
        f = float(v)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


# ── 에쿼티 커브 ────────────────────────────────────────────────────────────────

def _build_cash_series(
    current_cash: float,
    trade_log: list,
    index: "pd.DatetimeIndex",
) -> "pd.Series":
    """
    현재 현금에서 역산해 최초 현금을 구한 뒤 날짜별로 조정하는 시계열을 반환.
    • 주식 BUY → 현금 감소, SELL → 현금 증가
    • CASH DEPOSIT → 현금 직접 증가, WITHDRAW → 직접 감소
    """
    stock_trades = [
        t for t in (trade_log or [])
        if str(t.get("ticker", "")).upper() not in ("CASH", "")
        and t.get("type", "") in ("ADD", "BUY", "SOLD", "SELL")
        and float(t.get("price") or 0) > 0
    ]
    cash_deposits = [
        t for t in (trade_log or [])
        if str(t.get("ticker", "")).upper() == "CASH"
        and t.get("type", "") in ("DEPOSIT", "WITHDRAW")
        and float(t.get("q") or 0) > 0
    ]
    if not stock_trades and not cash_deposits:
        return pd.Series(current_cash, index=index, dtype=float)

    # 역산: 현재 현금 → 모든 이벤트 이전 초기 현금
    initial_cash = current_cash
    for tr in stock_trades:
        q, price = float(tr.get("q", 0)), float(tr.get("price") or 0)
        if tr["type"] in ("ADD", "BUY"):
            initial_cash += q * price
        elif tr["type"] in ("SOLD", "SELL"):
            initial_cash -= q * price
    for tr in cash_deposits:
        q = float(tr.get("q", 0))
        if tr["type"] == "DEPOSIT":
            initial_cash -= q   # 입금 이전에는 현금이 적었음
        elif tr["type"] == "WITHDRAW":
            initial_cash += q   # 출금 이전에는 현금이 많았음
    initial_cash = round(max(0.0, initial_cash), 2)

    # 순방향: 날짜 순으로 모든 이벤트를 반영
    cash_series = pd.Series(initial_cash, index=index, dtype=float)
    all_events = sorted(stock_trades + cash_deposits,
                        key=lambda t: (t.get("date", ""), t.get("id", 0)))
    for tr in all_events:
        try:
            td = pd.Timestamp(tr["date"])
        except Exception:
            continue
        q    = float(tr.get("q", 0))
        mask = cash_series.index >= td
        if str(tr.get("ticker", "")).upper() == "CASH":
            if tr["type"] == "DEPOSIT":
                cash_series[mask] += q
            elif tr["type"] == "WITHDRAW":
                cash_series[mask] -= q
        else:
            price = float(tr.get("price") or 0)
            if tr["type"] in ("ADD", "BUY"):
                cash_series[mask] -= q * price
            elif tr["type"] in ("SOLD", "SELL"):
                cash_series[mask] += q * price

    return cash_series.clip(lower=0)


def build_equity_curve(
    holdings: dict,
    trade_log: list,
    close_df: pd.DataFrame,
) -> pd.Series:
    """
    매매 이력(trade_log)을 처음부터 순방향으로 재생해 날짜별 포트폴리오 가치 산출.
    현금 0 / 보유 0에서 시작 → DEPOSIT·WITHDRAW·BUY·SELL 이벤트만 반영, 역산 없음.
    매도·입금 여부와 관계없이 과거 포인트가 변하지 않는다.
    비거래일(주말·공휴일) 이벤트는 다음 거래일에 자동 적용.
    """
    if close_df.empty:
        return pd.Series(dtype=float)

    # 오늘 날짜가 인덱스에 없으면(주말·공휴일) 마지막 가격을 ffill로 연장
    today = pd.Timestamp.today().normalize()
    if close_df.index[-1] < today:
        extended_idx = pd.DatetimeIndex(list(close_df.index) + [today])
        close_df = close_df.reindex(extended_idx).ffill()

    prices = close_df.ffill()
    idx    = close_df.index

    def _fallback() -> pd.Series:
        tickers = [t for t in holdings if t != "CASH" and t in prices.columns]
        cash    = float(holdings.get("CASH", {}).get("q", 0))
        sv = prices[tickers].multiply(
            [holdings[t]["q"] for t in tickers]
        ).sum(axis=1) if tickers else pd.Series(0.0, index=idx)
        return sv + cash

    if not trade_log:
        return _fallback()

    try:
        log_df = pd.DataFrame(trade_log)
        log_df["date"] = pd.to_datetime(log_df["date"]).dt.normalize()
        sort_keys = ["date", "id"] if "id" in log_df.columns else ["date"]
        log_df = log_df.sort_values(sort_keys).reset_index(drop=True)

        # 현재 보유 + 과거 매매 이력에 등장한 모든 종목을 추적
        traded_tickers = {
            str(r["ticker"]).upper()
            for _, r in log_df.iterrows()
            if str(r.get("ticker", "")).upper() not in ("CASH", "")
        }
        current_tickers = {t for t in holdings if t != "CASH"}
        all_tickers     = sorted((traded_tickers | current_tickers) & set(prices.columns))

        # 거래 이력 없는 보유 종목은 최초 시점부터 현재 수량으로 고정 (레거시 대응)
        logged_tickers = {str(r["ticker"]).upper() for _, r in log_df.iterrows()}
        static_qty: dict[str, float] = {
            t: float(holdings[t]["q"])
            for t in all_tickers
            if t not in logged_tickers and holdings.get(t, {}).get("q", 0) > 0
        }

        # DEPOSIT 날짜 보정: 주식 BUY보다 늦게 기록된 DEPOSIT은 첫 BUY 날짜로 당겨서 처리
        # (사용자가 현금을 오늘 입력했으나 과거에 매수한 경우 자산 왜곡 방지)
        stock_rows_mask = ~log_df["ticker"].str.upper().isin(["CASH", ""])
        if stock_rows_mask.any():
            first_trade_date = log_df.loc[stock_rows_mask, "date"].min()
            for i, row in log_df.iterrows():
                if str(row.get("ticker", "")).upper() == "CASH" \
                        and str(row.get("type", "")).upper() == "DEPOSIT" \
                        and row["date"] > first_trade_date:
                    log_df.at[i, "date"] = first_trade_date

        # 날짜 보정 후 재정렬
        log_df = log_df.sort_values(sort_keys).reset_index(drop=True)

        # 비거래일 이벤트 → 다음 거래일 포지션에 매핑 (정수 인덱스 키)
        events_by_pos: dict[int, list] = {}
        for _, row in log_df.iterrows():
            pos = int(idx.searchsorted(row["date"], side="left"))
            if pos < len(idx):
                events_by_pos.setdefault(pos, []).append(row)

        # 순방향 워크: 현금 0, 보유 0에서 시작
        running_cash: float = 0.0
        running_qty: dict[str, float] = {t: static_qty.get(t, 0.0) for t in all_tickers}

        equity_vals = np.zeros(len(idx), dtype=float)

        for i in range(len(idx)):
            for row in events_by_pos.get(i, []):
                ticker     = str(row.get("ticker", "")).upper()
                trade_type = str(row.get("type",   "")).upper()
                q          = float(row.get("q",     0))
                pr         = float(row.get("price") or 0)

                if ticker == "CASH":
                    if trade_type == "DEPOSIT":
                        running_cash += q
                    elif trade_type == "WITHDRAW":
                        running_cash -= q
                elif ticker in running_qty:
                    if trade_type in ("ADD", "BUY"):
                        running_cash -= q * pr
                        running_qty[ticker] += q
                    elif trade_type in ("SOLD", "SELL"):
                        running_cash += q * pr
                        running_qty[ticker] = max(0.0, running_qty[ticker] - q)
                    elif trade_type == "UPDATE":
                        running_qty[ticker] = max(0.0, q)

            row_prices = prices.iloc[i]
            stock_val  = sum(_safe(row_prices[t]) * running_qty[t] for t in all_tickers)
            equity_vals[i] = max(0.0, running_cash) + stock_val

        equity = pd.Series(equity_vals, index=idx, dtype=float)
        if equity.iloc[-1] <= 0 or equity.replace(0, np.nan).dropna().empty:
            raise ValueError("비정상 에쿼티 커브")
        return equity

    except Exception:
        return _fallback()


def equity_curve_to_records(
    curve: pd.Series,
    benchmark_df: pd.DataFrame | None = None,
    cash_event_amounts: dict | None = None,
    trade_markers: list | None = None,
) -> list[dict]:
    """
    에쿼티 커브를 API 응답용 레코드 리스트로 변환.
    cash_event_amounts: {date_str: amount}  DEPOSIT(양수) / WITHDRAW(음수)
    trade_markers:      [{ticker,type,q,price,date}, ...]  주식 매매 이력
    """
    peak = float(curve.max()) if not curve.empty else 0.0
    threshold = peak * 0.001
    meaningful = curve[curve > threshold]
    if not meaningful.empty:
        curve = curve.loc[meaningful.index[0]:]

    sp500_indexed = None
    if benchmark_df is not None and "^GSPC" in benchmark_df.columns:
        b = benchmark_df["^GSPC"].reindex(curve.index).ffill().bfill()
        b_clean = b.dropna()
        if len(b_clean) > 0:
            sp500_indexed = b / float(b_clean.iloc[0]) * float(curve.iloc[0])

    def _bv(date):
        if sp500_indexed is None or date not in sp500_indexed.index:
            return None
        v = sp500_indexed.loc[date]
        return None if pd.isna(v) else round(float(v), 2)

    cash_evt: dict = cash_event_amounts or {}

    # 날짜별 주식 거래 목록 (포인트 마커용)
    trade_by_date: dict = {}
    for tr in (trade_markers or []):
        d = tr.get("date", "")
        trade_by_date.setdefault(d, []).append({
            "ticker": tr.get("ticker", ""),
            "type":   tr.get("type", ""),
            "q":      tr.get("q", 0),
            "price":  tr.get("price", 0),
        })

    records = []
    for date, val in curve.items():
        if pd.isna(val):
            continue
        date_str = date.strftime("%Y-%m-%d")
        records.append({
            "date":               date_str,
            "value":              round(float(val), 2),
            "benchmark_value":    _bv(date),
            "cash_event":         date_str in cash_evt,
            "cash_event_amount":  cash_evt.get(date_str, 0),
            "trades":             trade_by_date.get(date_str, []),
        })
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

    # 비거래일(주말·공휴일)에 NaN이 생기지 않도록 ffill 적용
    price_df = close_df.ffill()
    curr = price_df.iloc[-1]
    prev = price_df.iloc[-2]

    def _price(t):
        v = curr.get(t, 0)
        return _safe(v) if t != "CASH" else 1.0

    def _prev_price(t):
        v = prev.get(t, curr.get(t, 0))
        return _safe(v) if t != "CASH" else 1.0

    stock_tickers = [t for t in holdings if t != "CASH" and t in close_df.columns]
    cash_val = holdings.get("CASH", {}).get("q", 0)

    total_equity  = _safe(sum(_price(t) * holdings[t]["q"] for t in stock_tickers) + cash_val)
    total_cost    = _safe(sum(_safe(holdings[t]["avg"]) * _safe(holdings[t]["q"]) for t in stock_tickers) + cash_val)
    total_rtn     = _safe((total_equity / total_cost - 1) * 100 if total_cost else 0.0)

    # 1D 변화: 현재 수량 × 전일 가격 방식은 당일 거래 시 오류 → 에쿼티 커브를 직접 사용
    if len(equity_curve) >= 2:
        _cur_eq = float(equity_curve.iloc[-1])
        _pre_eq = float(equity_curve.iloc[-2])
        today_chg_val = _safe(_cur_eq - _pre_eq)
        today_chg_pct = _safe((_cur_eq / _pre_eq - 1) * 100 if _pre_eq else 0.0)
    else:
        today_chg_val = 0.0
        today_chg_pct = 0.0

    def _perf(days):
        if len(equity_curve) >= days + 1:
            base = float(equity_curve.iloc[-(days + 1)])
            return (total_equity / base - 1) * 100 if base else 0.0
        return 0.0

    beta = calculate_portfolio_beta(holdings, close_df)
    vix  = float(curr.get("^VIX", 18.0))

    alpha = 0.0
    if "^GSPC" in close_df.columns:
        try:
            p_perf = (equity_curve / equity_curve.iloc[0] - 1) * 100
            b_sp   = close_df["^GSPC"].reindex(equity_curve.index).ffill().bfill()
            b_first = b_sp.dropna().iloc[0] if len(b_sp.dropna()) > 0 else None
            if b_first is not None and float(b_first) != 0:
                b_perf = (b_sp / float(b_first) - 1) * 100
                a_val = float(p_perf.iloc[-1]) - float(b_perf.iloc[-1])
                if a_val == a_val:  # not NaN
                    alpha = round(a_val, 4)
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
        _safe(curr.get(t, 0)) * _safe(info["q"])
        for t, info in holdings.items() if t != "CASH" and t in close_df.columns
    ) + _safe(holdings.get("CASH", {}).get("q", 0))
    total_equity = _safe(total_equity)

    rows = []
    for t, info in holdings.items():
        if t == "CASH":
            price   = 1.0
            chg_pct = 0.0
            pnl_pct = 0.0
        else:
            price   = _safe(curr.get(t, 0)) if t in close_df.columns else 0.0
            p_price = _safe(prev.get(t, price)) if t in prev.index else price
            chg_pct = _safe((price / p_price - 1) * 100) if p_price else 0.0
            avg     = _safe(info.get("avg", 0))
            pnl_pct = _safe((price / avg - 1) * 100) if avg > 0 else 0.0

        avg_cost = _safe(info.get("avg", 0))
        qty      = _safe(info.get("q", 0))
        value    = _safe(price * qty)
        pnl      = _safe((price - avg_cost) * qty)

        rows.append({
            "ticker":        t,
            "sector":        info.get("sector", "Other"),
            "qty":           round(qty, 4),
            "avg_cost":      round(avg_cost, 2),
            "current_price": round(price, 2),
            "chg_pct":       round(chg_pct, 4),
            "pnl_pct":       round(pnl_pct, 4),
            "pnl":           round(pnl, 2),
            "market_value":  round(value, 2),
            "weight":        round(_safe(value / total_equity), 4) if total_equity else 0.0,
        })
    return rows


# ── 수익률(%) 커브 ─────────────────────────────────────────────────────────────

def build_return_pct_curve(
    holdings: dict,
    trade_log: list,
    close_df: pd.DataFrame,
) -> "tuple[pd.Series, dict[str, list[dict]]]":
    """
    주식 포트폴리오의 날짜별 누적 수익률(%) 시계열 반환.
    (총 평가액 / 총 매입원가 - 1) × 100, 현금(CASH) 제외.
    """
    if close_df.empty:
        return pd.Series(dtype=float), {}

    today = pd.Timestamp.today().normalize()
    if close_df.index[-1] < today:
        extended_idx = pd.DatetimeIndex(list(close_df.index) + [today])
        close_df = close_df.reindex(extended_idx).ffill()

    prices = close_df.ffill()
    idx = close_df.index

    current_tickers = {t for t in holdings if t != "CASH"}

    if trade_log:
        log_df = pd.DataFrame(trade_log)
        log_df["date"] = pd.to_datetime(log_df["date"]).dt.normalize()
        sort_keys = ["date", "id"] if "id" in log_df.columns else ["date"]
        log_df = log_df.sort_values(sort_keys).reset_index(drop=True)

        stock_log = log_df[~log_df["ticker"].str.upper().isin(["CASH", ""])]
        traded_tickers = set(stock_log["ticker"].str.upper().tolist())
        all_tickers = sorted((traded_tickers | current_tickers) & set(prices.columns))

        logged_tickers = set(stock_log["ticker"].str.upper().tolist())
        static_qty: dict[str, float] = {
            t: float(holdings[t]["q"])
            for t in all_tickers
            if t not in logged_tickers and holdings.get(t, {}).get("q", 0) > 0
        }
        static_avg: dict[str, float] = {
            t: _safe(holdings[t].get("avg", 0))
            for t in static_qty
        }

        events_by_pos: dict[int, list] = {}
        for _, row in stock_log.iterrows():
            pos = int(idx.searchsorted(row["date"], side="left"))
            if pos < len(idx):
                events_by_pos.setdefault(pos, []).append(row)
    else:
        all_tickers = sorted(current_tickers & set(prices.columns))
        static_qty = {t: float(holdings[t]["q"]) for t in all_tickers}
        static_avg = {t: _safe(holdings[t].get("avg", 0)) for t in all_tickers}
        events_by_pos = {}

    if not all_tickers:
        return pd.Series(dtype=float), {}

    running_qty: dict[str, float] = {t: static_qty.get(t, 0.0) for t in all_tickers}
    running_avg: dict[str, float] = {
        t: static_avg.get(t, _safe(holdings.get(t, {}).get("avg", 0)))
        for t in all_tickers
    }

    return_pct_arr = np.zeros(len(idx), dtype=float)
    holdings_by_date: dict[str, list[dict]] = {}

    for i in range(len(idx)):
        for row in events_by_pos.get(i, []):
            ticker = str(row.get("ticker", "")).upper()
            trade_type = str(row.get("type", "")).upper()
            q = float(row.get("q", 0))
            pr = float(row.get("price") or 0)

            if ticker in running_qty:
                if trade_type in ("ADD", "BUY"):
                    prev_qty = running_qty[ticker]
                    prev_avg = running_avg[ticker]
                    new_qty = prev_qty + q
                    running_avg[ticker] = (
                        (prev_qty * prev_avg + q * pr) / new_qty if new_qty > 0 else 0.0
                    )
                    running_qty[ticker] = new_qty
                elif trade_type in ("SOLD", "SELL"):
                    running_qty[ticker] = max(0.0, running_qty[ticker] - q)
                    if running_qty[ticker] == 0:
                        running_avg[ticker] = 0.0
                elif trade_type == "UPDATE":
                    running_qty[ticker] = max(0.0, q)

        row_prices = prices.iloc[i]
        total_cost = sum(running_qty[t] * running_avg[t] for t in all_tickers)
        total_val  = sum(running_qty[t] * _safe(row_prices.get(t, 0)) for t in all_tickers)

        return_pct_arr[i] = _safe(
            (total_val / total_cost - 1) * 100 if total_cost > 0 else 0.0
        )

        date_str = idx[i].strftime("%Y-%m-%d")
        holdings_by_date[date_str] = [
            {
                "ticker": t,
                "return_pct": round(_safe(
                    (_safe(row_prices.get(t, 0)) / running_avg[t] - 1) * 100
                    if running_avg[t] > 0 else 0.0
                ), 2),
                "price": round(_safe(row_prices.get(t, 0)), 2),
            }
            for t in all_tickers
            if running_qty[t] > 0
        ]

    return pd.Series(return_pct_arr, index=idx, dtype=float), holdings_by_date


def return_pct_to_records(
    return_pct: pd.Series,
    holdings_by_date: dict,
    close_df: "pd.DataFrame | None" = None,
    trade_markers: "list | None" = None,
) -> list[dict]:
    """
    수익률(%) 시계열을 API 응답용 레코드 리스트로 변환.
    S&P 500도 같은 시작일 기준 % 수익률로 변환해 포함.
    """
    if return_pct.empty:
        return []

    # 첫 보유 종목이 있는 날부터 표시
    first_date = None
    for date_str in sorted(holdings_by_date.keys()):
        if holdings_by_date[date_str]:
            first_date = pd.Timestamp(date_str)
            break
    if first_date is None:
        return []

    curve = return_pct.loc[return_pct.index >= first_date]
    if curve.empty:
        return []

    # S&P 500: 같은 시작일 기준 수익률(%)
    sp_return = None
    if close_df is not None and "^GSPC" in close_df.columns:
        b = close_df["^GSPC"].reindex(curve.index).ffill().bfill()
        b_clean = b.dropna()
        if len(b_clean) > 0:
            b_first = float(b_clean.iloc[0])
            if b_first > 0:
                sp_return = (b / b_first - 1) * 100

    trade_by_date: dict = {}
    for tr in (trade_markers or []):
        d = tr.get("date", "")
        trade_by_date.setdefault(d, []).append({
            "ticker": tr.get("ticker", ""),
            "type":   tr.get("type", ""),
            "q":      tr.get("q", 0),
            "price":  tr.get("price", 0),
        })

    records = []
    for date, pct in curve.items():
        if pd.isna(pct):
            continue
        date_str = date.strftime("%Y-%m-%d")
        sp_val = None
        if sp_return is not None and date in sp_return.index:
            v = sp_return.loc[date]
            sp_val = None if pd.isna(v) else round(float(v), 2)
        records.append({
            "date":     date_str,
            "port":     round(float(pct), 2),
            "sp":       sp_val,
            "trades":   trade_by_date.get(date_str, []),
            "holdings": holdings_by_date.get(date_str, []),
        })
    return records


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

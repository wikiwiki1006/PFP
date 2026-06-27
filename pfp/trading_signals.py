"""
trading_signals.py  (Streamlit 페이지 전용)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
계산 로직은 backend/services/trading_signals.py 에 있고,
여기서는 re-export + Plotly 시각화 함수만 유지한다.

페이지 파일(trading_signals_page.py, alpha_terminal.py)은
변경 없이 계속 `from trading_signals import ...` 로 사용 가능.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# ── 백엔드 서비스 re-export ────────────────────────────────────────────────────
_ROOT = Path(__file__).parent.parent   # pfp/ (프로젝트 루트)
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.services.trading_signals import (   # noqa: E402
    DOOM_RATE_SPREAD_THRESHOLD,
    DOOM_HY_SPREAD_THRESHOLD,
    SP500_NASDAQ_UNIVERSE,
    fetch_macro_doom_indicators,
    evaluate_doom_radar,
    pairs_trading_signal,
    mean_reversion_signal,
    momentum_breakout_signal,
    detect_market_regime,
    _regime_fallback,
    find_best_pair,
    scan_universe_with_targets,
)

__all__ = [
    "DOOM_RATE_SPREAD_THRESHOLD", "DOOM_HY_SPREAD_THRESHOLD", "SP500_NASDAQ_UNIVERSE",
    "fetch_macro_doom_indicators", "evaluate_doom_radar", "apply_doom_filter",
    "plot_doom_radar",
    "pairs_trading_signal", "plot_pairs_trading",
    "mean_reversion_signal", "plot_mean_reversion",
    "momentum_breakout_signal", "plot_momentum_breakout",
    "detect_market_regime", "_regime_fallback",
    "plot_regime_chart", "plot_regime_scatter",
    "find_best_pair",
    "scan_top_signals", "scan_top_pairs",
    "scan_universe_with_targets",
    "multi_factor_scan",
]


# ══════════════════════════════════════════════════════════════════════════════
# 저승사자 레이더 — 필터 + 차트
# ══════════════════════════════════════════════════════════════════════════════

def apply_doom_filter(signal: str | None, doom: dict) -> str | None:
    """경보 중 매수성 신호("BUY", "BREAKOUT", "LONG_A_SHORT_B")를 차단."""
    if not doom["is_doom"] or signal is None:
        return signal
    BUY_LIKE = {"BUY", "BREAKOUT", "LONG_A_SHORT_B"}
    if signal in BUY_LIKE:
        return "🚫 매수 금지 (대재앙 경보)"
    return signal


def plot_doom_radar(macro: dict, doom: dict) -> go.Figure:
    """10Y-2Y 금리차 + HY 스프레드 시계열 2단 차트, 경보 구간 음영."""
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.08,
        subplot_titles=("10Y-2Y 장단기 금리차 (%p)", "하이일드 스프레드 OAS (%)"),
    )
    rs = macro.get("rate_spread_series")
    hs = macro.get("hy_spread_series")

    if rs is not None and len(rs) > 0:
        fig.add_trace(go.Scatter(x=rs.index, y=rs, name="10Y-2Y",
                                 line=dict(color="#3b82f6")), row=1, col=1)
        fig.add_hline(y=0, line=dict(color="#ef4444", dash="dash", width=1.5), row=1, col=1)
    if hs is not None and len(hs) > 0:
        fig.add_trace(go.Scatter(x=hs.index, y=hs, name="HY OAS",
                                 line=dict(color="#f59e0b")), row=2, col=1)
        fig.add_hline(y=DOOM_HY_SPREAD_THRESHOLD,
                      line=dict(color="#ef4444", dash="dash", width=1.5), row=2, col=1)

    fig.update_layout(height=460, showlegend=False)
    return fig


# ══════════════════════════════════════════════════════════════════════════════
# 페어 트레이딩 차트
# ══════════════════════════════════════════════════════════════════════════════

def plot_pairs_trading(
    price_a: pd.Series, price_b: pd.Series, result: dict,
    ticker_a: str, ticker_b: str,
) -> go.Figure:
    """정규화 가격 비교(상단) + Z-score & 신호 마커(하단)."""
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.08,
        row_heights=[0.5, 0.5],
        subplot_titles=(f"{ticker_a} vs {ticker_b} (정규화 가격)", "스프레드 Z-score"),
    )
    norm_a = price_a / price_a.iloc[0] * 100
    norm_b = price_b / price_b.iloc[0] * 100
    fig.add_trace(go.Scatter(x=norm_a.index, y=norm_a, name=ticker_a,
                             line=dict(color="#3b82f6")), row=1, col=1)
    fig.add_trace(go.Scatter(x=norm_b.index, y=norm_b, name=ticker_b,
                             line=dict(color="#f59e0b")), row=1, col=1)

    zscore = result["zscore"]
    fig.add_trace(go.Scatter(x=zscore.index, y=zscore, name="Z-score",
                             line=dict(color="#9b59b6")), row=2, col=1)
    fig.add_hline(y=2,  line=dict(color="#ef4444", dash="dash", width=1), row=2, col=1)
    fig.add_hline(y=-2, line=dict(color="#22c55e", dash="dash", width=1), row=2, col=1)
    fig.add_hline(y=0,  line=dict(color="#8b949e", width=1), row=2, col=1)

    sig = result["signals"]
    long_a_entries = sig[sig == "LONG_A_SHORT_B"]
    long_b_entries = sig[sig == "LONG_B_SHORT_A"]
    if len(long_a_entries) > 0:
        fig.add_trace(go.Scatter(
            x=long_a_entries.index, y=zscore.loc[long_a_entries.index],
            mode="markers", marker=dict(color="#22c55e", size=6, symbol="triangle-up"),
            name=f"롱 {ticker_a}/숏 {ticker_b}",
        ), row=2, col=1)
    if len(long_b_entries) > 0:
        fig.add_trace(go.Scatter(
            x=long_b_entries.index, y=zscore.loc[long_b_entries.index],
            mode="markers", marker=dict(color="#ef4444", size=6, symbol="triangle-down"),
            name=f"롱 {ticker_b}/숏 {ticker_a}",
        ), row=2, col=1)

    fig.update_layout(height=560, showlegend=True)
    return fig


# ══════════════════════════════════════════════════════════════════════════════
# 평균 회귀 차트
# ══════════════════════════════════════════════════════════════════════════════

def plot_mean_reversion(price: pd.Series, result: dict, ticker: str) -> go.Figure:
    """가격 + 볼린저 밴드 + 매수/매도 마커."""
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=price.index, y=result["upper_band"], name="상단밴드",
                             line=dict(color="#ef4444", width=1, dash="dot")))
    fig.add_trace(go.Scatter(x=price.index, y=result["mid_band"], name="중앙선(이동평균)",
                             line=dict(color="#8b949e", width=1)))
    fig.add_trace(go.Scatter(x=price.index, y=result["lower_band"], name="하단밴드",
                             line=dict(color="#22c55e", width=1, dash="dot"),
                             fill="tonexty", fillcolor="rgba(139,148,158,0.05)"))
    fig.add_trace(go.Scatter(x=price.index, y=price, name=ticker,
                             line=dict(color="#3b82f6", width=2)))

    buys  = result["signals"][result["signals"] == "BUY"]
    sells = result["signals"][result["signals"] == "SELL"]
    if len(buys) > 0:
        fig.add_trace(go.Scatter(x=buys.index, y=price.loc[buys.index], mode="markers",
                                 marker=dict(color="#22c55e", size=8, symbol="triangle-up"),
                                 name="매수 신호"))
    if len(sells) > 0:
        fig.add_trace(go.Scatter(x=sells.index, y=price.loc[sells.index], mode="markers",
                                 marker=dict(color="#ef4444", size=8, symbol="triangle-down"),
                                 name="매도 신호"))
    fig.update_layout(
        title=dict(text=f"{ticker} 볼린저 밴드 평균회귀", font=dict(size=14)),
        yaxis_title="가격", height=440,
    )
    return fig


# ══════════════════════════════════════════════════════════════════════════════
# 모멘텀 돌파 차트
# ══════════════════════════════════════════════════════════════════════════════

def plot_momentum_breakout(
    price: pd.Series, volume: pd.Series, result: dict, ticker: str,
) -> go.Figure:
    """가격+저항선(상단) / 거래량(하단) 2단 차트, 돌파 지점 마커."""
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.06,
        row_heights=[0.65, 0.35],
        subplot_titles=(f"{ticker} 가격 & 저항선 돌파", "거래량"),
    )
    fig.add_trace(go.Scatter(x=price.index, y=price, name=ticker,
                             line=dict(color="#3b82f6", width=2)), row=1, col=1)
    fig.add_trace(go.Scatter(x=price.index, y=result["resistance"], name="저항선(N일 고점)",
                             line=dict(color="#f59e0b", width=1, dash="dash")), row=1, col=1)

    breakouts = result["signals"][result["signals"] == "BREAKOUT"]
    if len(breakouts) > 0:
        fig.add_trace(go.Scatter(
            x=breakouts.index, y=price.loc[breakouts.index], mode="markers",
            marker=dict(color="#22c55e", size=10, symbol="triangle-up"),
            name="돌파 매수 신호",
        ), row=1, col=1)

    vol_colors = ["#22c55e" if v > a else "#30363d"
                  for v, a in zip(volume, result["volume_avg"].fillna(0))]
    fig.add_trace(go.Bar(x=volume.index, y=volume, marker_color=vol_colors,
                         name="거래량"), row=2, col=1)
    fig.add_trace(go.Scatter(x=volume.index, y=result["volume_avg"], name="평균거래량",
                             line=dict(color="#8b949e", width=1)), row=2, col=1)

    fig.update_layout(height=560, showlegend=True)
    return fig


# ══════════════════════════════════════════════════════════════════════════════
# 시장 국면 차트
# ══════════════════════════════════════════════════════════════════════════════

def plot_regime_chart(price: pd.Series, result: dict, ticker: str) -> go.Figure:
    """가격 배경을 국면(Bull/Bear/Sideways)별 색상으로 구분."""
    labels       = result["regime_labels"]
    aligned_price = price.reindex(labels.index)
    color_map    = {"Bull": "rgba(34,197,94,0.12)", "Bear": "rgba(239,68,68,0.12)",
                    "Sideways": "rgba(139,148,158,0.10)"}

    fig = go.Figure()
    if labels is not None and len(labels) > 0:
        current_label = labels.iloc[0]
        start_idx     = labels.index[0]
        for i in range(1, len(labels)):
            if labels.iloc[i] != current_label or i == len(labels) - 1:
                end_idx = labels.index[i]
                fig.add_vrect(
                    x0=start_idx, x1=end_idx,
                    fillcolor=color_map.get(current_label, "rgba(139,148,158,0.1)"),
                    line_width=0, layer="below",
                )
                current_label = labels.iloc[i]
                start_idx     = labels.index[i]

    fig.add_trace(go.Scatter(x=aligned_price.index, y=aligned_price, name=ticker,
                             line=dict(color="#e0e0e0", width=2)))
    fig.update_layout(
        title=dict(text=f"{ticker} 시장 국면 (K-means 자동 분류)", font=dict(size=14)),
        yaxis_title="가격", height=440,
    )
    return fig


def plot_regime_scatter(result: dict) -> go.Figure:
    """(수익률, 변동성) 산점도 — 극단값 95% 분위로 축 범위 고정."""
    features = result["features"]
    labels   = result["regime_labels"]
    if features is None:
        return go.Figure()

    color_map = {"Bull": "#22c55e", "Bear": "#ef4444", "Sideways": "#8b949e"}
    vol_pct   = features["volatility"] * 100
    ret_pct   = features["return"] * 100
    x_lo, x_hi = np.percentile(vol_pct, [2.5, 97.5])
    y_lo, y_hi = np.percentile(ret_pct, [2.5, 97.5])
    x_pad = (x_hi - x_lo) * 0.1 or 1.0
    y_pad = (y_hi - y_lo) * 0.1 or 1.0

    fig = go.Figure()
    for regime in labels.unique():
        mask = labels == regime
        fig.add_trace(go.Scatter(
            x=vol_pct[mask], y=ret_pct[mask], mode="markers", name=regime,
            marker=dict(color=color_map.get(regime, "#3b82f6"), size=5, opacity=0.6),
        ))
    fig.update_layout(
        title=dict(text="국면별 (수익률, 변동성) 분포 (상하위 2.5% 극단값 제외)",
                   font=dict(size=13)),
        xaxis=dict(title="연변동성 (%)", range=[x_lo - x_pad, x_hi + x_pad]),
        yaxis=dict(title="연수익률 (%)", range=[y_lo - y_pad, y_hi + y_pad]),
        height=380,
    )
    return fig


# ══════════════════════════════════════════════════════════════════════════════
# 전수 스캔 (워치리스트 — 매수가/목표가/손절가 없는 빠른 버전)
# ══════════════════════════════════════════════════════════════════════════════

def scan_top_signals(price_df, volume_df, top_n: int = 5) -> dict:
    """워치리스트 전 종목에 평균회귀·모멘텀을 적용, LONG/SHORT 상위 top_n 반환."""
    long_candidates:  list[dict] = []
    short_candidates: list[dict] = []
    scanned = 0

    for ticker in price_df.columns:
        price = price_df[ticker].dropna()
        if len(price) < 60:
            continue
        scanned += 1

        try:
            mr = mean_reversion_signal(price)
            if mr["current_signal"] == "BUY":
                long_candidates.append({"ticker": ticker, "method": "평균회귀",
                                        "reason": f"볼린저 하단 이탈 · Z={mr['current_z']:.2f}",
                                        "score": abs(mr["current_z"])})
            elif mr["current_signal"] == "SELL":
                short_candidates.append({"ticker": ticker, "method": "평균회귀",
                                         "reason": f"볼린저 상단 이탈 · Z={mr['current_z']:.2f}",
                                         "score": abs(mr["current_z"])})
        except Exception:
            pass

        if volume_df is not None and ticker in volume_df.columns:
            try:
                vol    = volume_df[ticker].dropna()
                common = price.index.intersection(vol.index)
                if len(common) >= 60:
                    mb = momentum_breakout_signal(price.loc[common], vol.loc[common])
                    if mb["is_breakout_today"]:
                        long_candidates.append({"ticker": ticker, "method": "모멘텀 돌파",
                                                "reason": "저항선 돌파 + 거래량 급증",
                                                "score": 3.5})
            except Exception:
                pass

    def _dedup_top(lst: list[dict]) -> list[dict]:
        seen: dict = {}
        for c in sorted(lst, key=lambda x: x["score"], reverse=True):
            if c["ticker"] not in seen:
                seen[c["ticker"]] = c
        return list(seen.values())[:top_n]

    return {"long_candidates": _dedup_top(long_candidates),
            "short_candidates": _dedup_top(short_candidates),
            "scanned_count": scanned}


def scan_top_pairs(price_df, top_n: int = 2, min_correlation: float = 0.0) -> list[dict]:
    """전체 종목 조합 중 Z-score 격차가 큰 페어 top_n 반환."""
    tickers = [t for t in price_df.columns if price_df[t].dropna().shape[0] >= 60]
    if len(tickers) < 2:
        return []

    returns_df  = price_df[tickers].pct_change().dropna()
    corr_matrix = returns_df.corr()
    pairs:  list[dict] = []

    for i, ta in enumerate(tickers):
        for tb in tickers[i + 1:]:
            try:
                corr = float(corr_matrix.loc[ta, tb])
                if abs(corr) < min_correlation:
                    continue
                pa = price_df[ta].dropna()
                pb = price_df[tb].dropna()
                common = pa.index.intersection(pb.index)
                if len(common) < 60:
                    continue
                res = pairs_trading_signal(pa.loc[common], pb.loc[common], min_correlation=0.0)
                pairs.append({"pair": f"{ta}/{tb}", "ticker_a": ta, "ticker_b": tb,
                               "correlation": round(corr, 3), "current_z": res["current_z"],
                               "reason": f"상관계수 {corr:.2f} · Z-score {res['current_z']:+.2f}"})
            except Exception:
                continue

    return sorted(pairs, key=lambda x: abs(x["current_z"]), reverse=True)[:top_n]


# ══════════════════════════════════════════════════════════════════════════════
# 멀티팩터 스캔 (RSI + MACD + BB + 거래량)
# ══════════════════════════════════════════════════════════════════════════════

def _calc_rsi(price: pd.Series, window: int = 14) -> float:
    delta = price.diff()
    gain  = delta.clip(lower=0).rolling(window).mean()
    loss  = (-delta.clip(upper=0)).rolling(window).mean()
    rs    = gain / loss.replace(0, 1e-9)
    rsi   = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50.0


def _calc_macd(price: pd.Series):
    ema12  = price.ewm(span=12, adjust=False).mean()
    ema26  = price.ewm(span=26, adjust=False).mean()
    macd   = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    return float(macd.iloc[-1]), float(signal.iloc[-1])


def multi_factor_scan(price_df, volume_df=None, top_n: int = 10, stop_pct: float = 0.04) -> dict:
    """RSI + MACD + BB + 거래량 4개 지표 합산 점수로 LONG/SHORT top_n 선별."""
    long_picks:  list[dict] = []
    short_picks: list[dict] = []
    scanned = 0

    for ticker in price_df.columns:
        price = price_df[ticker].dropna()
        if len(price) < 60:
            continue
        scanned += 1
        cur        = float(price.iloc[-1])
        buy_score  = 0.0
        sell_score = 0.0
        buy_signals:  list[str] = []
        sell_signals: list[str] = []

        try:
            rsi = _calc_rsi(price)
            if rsi < 35:
                buy_score += 1; buy_signals.append(f"RSI {rsi:.0f} 과매도")
            elif rsi > 65:
                sell_score += 1; sell_signals.append(f"RSI {rsi:.0f} 과매수")
        except Exception:
            pass

        try:
            macd_val, macd_sig = _calc_macd(price)
            prev_macd, prev_sig = _calc_macd(price.iloc[:-1])
            if macd_val > macd_sig and prev_macd <= prev_sig:
                buy_score += 1; buy_signals.append("MACD 골든크로스")
            elif macd_val < macd_sig and prev_macd >= prev_sig:
                sell_score += 1; sell_signals.append("MACD 데드크로스")
            elif macd_val > macd_sig:
                buy_score += 0.5
            elif macd_val < macd_sig:
                sell_score += 0.5
        except Exception:
            pass

        try:
            mid   = float(price.rolling(20).mean().iloc[-1])
            std   = float(price.rolling(20).std().iloc[-1])
            upper = mid + 2 * std
            lower = mid - 2 * std
            if cur <= lower:
                buy_score += 1; buy_signals.append(f"BB 하단이탈 (중앙선 ${mid:.2f})")
            elif cur >= upper:
                sell_score += 1; sell_signals.append(f"BB 상단돌파 (중앙선 ${mid:.2f})")
        except Exception:
            mid = cur

        if volume_df is not None and ticker in volume_df.columns:
            try:
                vol   = volume_df[ticker].dropna()
                vol5  = float(vol.rolling(5).mean().iloc[-1])
                vol20 = float(vol.rolling(20).mean().iloc[-1])
                if vol5 > vol20 * 1.2:
                    if buy_score > sell_score:
                        buy_score += 0.5; buy_signals.append("거래량 급증 확인")
                    else:
                        sell_score += 0.5; sell_signals.append("거래량 급증 확인")
            except Exception:
                pass

        firm_buy  = sum(1 for s in buy_signals  if "거래량" not in s)
        firm_sell = sum(1 for s in sell_signals if "거래량" not in s)

        if buy_score >= 2.0 and firm_buy >= 2 and sell_score == 0:
            target = round(mid if 'mid' in dir() else cur * 1.05, 2)
            long_picks.append({"ticker": ticker, "method": "멀티팩터", "direction": "LONG",
                                "entry": round(cur, 2), "target": target,
                                "stop": round(cur * (1 - stop_pct), 2),
                                "upside": round((target - cur) / cur * 100, 1),
                                "score": buy_score, "strength": min(buy_score / 4.0, 1.0),
                                "reason": " · ".join(buy_signals)})
        elif sell_score >= 2.0 and firm_sell >= 2 and buy_score == 0:
            target = round(mid if 'mid' in dir() else cur * 0.95, 2)
            short_picks.append({"ticker": ticker, "method": "멀티팩터", "direction": "SHORT",
                                 "entry": round(cur, 2), "target": target,
                                 "stop": round(cur * (1 + stop_pct), 2),
                                 "downside": round((cur - target) / cur * 100, 1),
                                 "score": sell_score, "strength": min(sell_score / 4.0, 1.0),
                                 "reason": " · ".join(sell_signals)})

    def _dedup(lst):
        seen: dict = {}
        for c in sorted(lst, key=lambda x: x["score"], reverse=True):
            if c["ticker"] not in seen:
                seen[c["ticker"]] = c
        return list(seen.values())[:top_n]

    return {"long_picks": _dedup(long_picks), "short_picks": _dedup(short_picks),
            "scanned": scanned}

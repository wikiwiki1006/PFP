"""
monte_carlo.py  (Streamlit 페이지 전용)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
계산 로직은 backend/services/monte_carlo.py 에 있고,
여기서는 re-export + Plotly 시각화 함수만 유지한다.

페이지 파일(monte_carlo_page.py)은
변경 없이 계속 `from monte_carlo import ...` 로 사용 가능.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import plotly.graph_objects as go

# ── 백엔드 서비스 re-export ────────────────────────────────────────────────────
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.services.monte_carlo import (   # noqa: E402
    generate_sample_returns,
    monte_carlo_portfolio_target,
    monte_carlo_stock_target_price,
    macro_drift_vol_multipliers,
    _macro_to_jump_params,
    monte_carlo_macro_scenario,
    calculate_advanced_params,
    INDUSTRY_ETF_MAP,
)

__all__ = [
    "generate_sample_returns",
    "monte_carlo_portfolio_target", "plot_portfolio_distribution",
    "monte_carlo_stock_target_price", "plot_stock_distribution",
    "macro_drift_vol_multipliers", "_macro_to_jump_params",
    "monte_carlo_macro_scenario", "plot_macro_distribution", "plot_macro_paths",
    "calculate_advanced_params", "INDUSTRY_ETF_MAP",
]


# ══════════════════════════════════════════════════════════════════════════════
# 포트폴리오 목표 수익률 분포 차트
# ══════════════════════════════════════════════════════════════════════════════

def plot_portfolio_distribution(
    result: dict,
    target_return: float,
    initial_value: float = 100_000_000,
) -> go.Figure:
    """1년 후 포트폴리오 가치 분포 히스토그램."""
    final_returns = np.asarray(result["final_returns"])
    final_values  = initial_value * (1 + final_returns)
    target_value  = initial_value * (1 + target_return)

    fig = go.Figure()
    fig.add_trace(go.Histogram(
        x=final_values, nbinsx=60,
        marker=dict(color="#3b82f6", line=dict(color="white", width=0.5)),
        opacity=0.85, name="시뮬레이션 결과",
    ))
    fig.add_vline(x=target_value, line=dict(color="red", dash="dash", width=2),
                  annotation_text=f"목표 {target_return*100:.1f}% (₩{target_value:,.0f})",
                  annotation_position="top", annotation_font_color="red")
    fig.add_vline(x=initial_value, line=dict(color="gray", dash="dot", width=1.5),
                  annotation_text=f"현재 (₩{initial_value:,.0f})",
                  annotation_position="bottom", annotation_font_color="gray")
    fig.update_layout(
        title=dict(
            text=(f"1년 후 포트폴리오 가치 분포 (n={len(final_values):,})  "
                  f"·  목표 달성 확률: {result['probability']:.2f}%"),
            font=dict(size=14),
        ),
        xaxis_title="1년 후 자산 가치", yaxis_title="시뮬레이션 빈도",
        bargap=0.02, height=420,
    )
    fig.update_xaxes(tickformat=",.0f", ticksuffix="원")
    return fig


# ══════════════════════════════════════════════════════════════════════════════
# 개별 종목 목표 주가 분포 차트
# ══════════════════════════════════════════════════════════════════════════════

def plot_stock_distribution(
    result: dict,
    current_price: float,
    target_price: float,
) -> tuple[go.Figure, go.Figure]:
    """1년 후 가격 분포 히스토그램 + 샘플 경로. (fig_hist, fig_path) 반환."""
    final_prices = np.asarray(result["final_prices"])

    # ── 히스토그램 ────────────────────────────────────────────────────────────
    fig_hist = go.Figure()
    fig_hist.add_trace(go.Histogram(
        x=final_prices, nbinsx=60,
        marker=dict(color="#10b981", line=dict(color="white", width=0.5)),
        opacity=0.85, name="1년 후 가격",
    ))
    fig_hist.add_vline(x=target_price, line=dict(color="red", dash="dash", width=2),
                       annotation_text=f"목표 {target_price:,.2f}",
                       annotation_position="top", annotation_font_color="red")
    fig_hist.add_vline(x=current_price, line=dict(color="gray", dash="dot", width=1.5),
                       annotation_text=f"현재 {current_price:,.2f}",
                       annotation_position="bottom", annotation_font_color="gray")
    fig_hist.update_layout(
        title=dict(
            text=(f"1년 후 가격 분포  ·  종가기준 {result['probability_final']:.2f}%  "
                  f"·  터치기준 {result['probability_touch']:.2f}%"),
            font=dict(size=13),
        ),
        xaxis_title="1년 후 가격", yaxis_title="시뮬레이션 빈도",
        bargap=0.02, height=400,
    )

    # ── 경로 차트 ─────────────────────────────────────────────────────────────
    paths    = np.asarray(result["price_paths_sample"])
    fig_path = go.Figure()
    for p in paths:
        fig_path.add_trace(go.Scatter(
            y=p, mode="lines", line=dict(color="#3b82f6", width=0.8),
            opacity=0.08, showlegend=False, hoverinfo="skip",
        ))
    fig_path.add_hline(y=target_price, line=dict(color="red", dash="dash", width=2),
                       annotation_text=f"목표 {target_price:,.2f}",
                       annotation_font_color="red")
    fig_path.add_hline(y=current_price, line=dict(color="gray", dash="dot", width=1.5),
                       annotation_text=f"현재 {current_price:,.2f}",
                       annotation_font_color="gray")
    fig_path.update_layout(
        title=dict(text=f"시뮬레이션 가격 경로 (샘플 {len(paths)}개)", font=dict(size=13)),
        xaxis_title="거래일", yaxis_title="가격", height=400,
    )
    return fig_hist, fig_path


# ══════════════════════════════════════════════════════════════════════════════
# 매크로 시나리오 차트
# ══════════════════════════════════════════════════════════════════════════════

def plot_macro_distribution(
    result: dict,
    current_value: float,
    target_return: float,
    value_label: str = "자산 가치",
) -> go.Figure:
    """매크로 시나리오 결과 히스토그램 + VaR/CVaR 라인."""
    final_values = np.asarray(result["final_values"])
    target_value = current_value * (1 + target_return)
    var_value    = current_value * (1 + result["var_95"]  / 100)
    cvar_value   = current_value * (1 + result["cvar_95"] / 100)

    fig = go.Figure()
    fig.add_trace(go.Histogram(
        x=final_values, nbinsx=70,
        marker=dict(color="#3b82f6", line=dict(color="white", width=0.3)),
        opacity=0.85, name="시뮬레이션 결과",
    ))
    fig.add_vline(x=target_value, line=dict(color="#22c55e", dash="dash", width=2),
                  annotation_text=f"목표 {target_return*100:+.1f}%",
                  annotation_position="top", annotation_font_color="#22c55e")
    fig.add_vline(x=current_value, line=dict(color="gray", dash="dot", width=1.5),
                  annotation_text="현재", annotation_position="bottom",
                  annotation_font_color="gray")
    fig.add_vline(x=var_value, line=dict(color="#f59e0b", dash="dashdot", width=1.5),
                  annotation_text=f"VaR95 {result['var_95']:+.1f}%",
                  annotation_position="top", annotation_font_color="#f59e0b")
    fig.add_vline(x=cvar_value, line=dict(color="#ef4444", dash="dashdot", width=1.5),
                  annotation_text=f"CVaR95 {result['cvar_95']:+.1f}%",
                  annotation_position="bottom", annotation_font_color="#ef4444")
    fig.update_layout(
        title=dict(
            text=(f"1년 후 {value_label} 분포 (n={len(final_values):,})  ·  "
                  f"종가달성 {result['probability_final']:.1f}%  ·  "
                  f"터치달성 {result['probability_touch']:.1f}%"),
            font=dict(size=13),
        ),
        xaxis_title=f"1년 후 {value_label}", yaxis_title="시뮬레이션 빈도",
        bargap=0.02, height=440,
    )
    return fig


def plot_macro_paths(
    result: dict,
    current_value: float,
    target_return: float,
    value_label: str = "자산 가치",
) -> go.Figure:
    """샘플 가치 경로 + 점프 발생 지점 빨간 점."""
    paths        = np.asarray(result["value_paths_sample"])
    jumps        = np.asarray(result["jump_events_sample"]) if result.get("jump_events_sample") else None
    target_value = current_value * (1 + target_return)

    fig = go.Figure()
    for i, p in enumerate(paths):
        fig.add_trace(go.Scatter(
            y=p, mode="lines", line=dict(color="#3b82f6", width=0.7),
            opacity=0.12, showlegend=False, hoverinfo="skip",
        ))
        if jumps is not None and i < 40:
            jump_idx = np.where(jumps[i])[0]
            if len(jump_idx) > 0:
                fig.add_trace(go.Scatter(
                    x=jump_idx + 1, y=p[jump_idx + 1], mode="markers",
                    marker=dict(color="#ef4444", size=4, opacity=0.5),
                    showlegend=False, hoverinfo="skip",
                ))

    fig.add_hline(y=target_value, line=dict(color="#22c55e", dash="dash", width=2),
                  annotation_text=f"목표 {target_return*100:+.1f}%",
                  annotation_font_color="#22c55e")
    fig.add_hline(y=current_value, line=dict(color="gray", dash="dot", width=1.5),
                  annotation_text="현재", annotation_font_color="gray")
    fig.update_layout(
        title=dict(
            text=f"시뮬레이션 경로 (샘플 {len(paths)}개) · 빨간 점 = 점프(패닉) 발생",
            font=dict(size=13),
        ),
        xaxis_title="거래일", yaxis_title=value_label, height=440,
    )
    return fig

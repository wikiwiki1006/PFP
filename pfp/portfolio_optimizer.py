"""
portfolio_optimizer.py  (Streamlit 페이지 전용)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
계산 로직은 backend/services/optimizer.py 에 있고,
여기서는 re-export + Plotly 시각화 함수만 유지한다.

페이지 파일(portfolio_optimizer_page.py)은
변경 없이 계속 `from portfolio_optimizer import ...` 로 사용 가능.
"""
from __future__ import annotations

import sys
from pathlib import Path

import plotly.graph_objects as go

# ── 백엔드 서비스 re-export ────────────────────────────────────────────────────
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.services.optimizer import (   # noqa: E402
    optimize_max_sharpe,
    optimize_black_litterman,
    build_regime_views,
    generate_proxy_factors,
    factor_analysis,
    _HAS_PYPFOPT,
)

__all__ = [
    "_HAS_PYPFOPT",
    "optimize_max_sharpe", "optimize_black_litterman", "build_regime_views",
    "generate_proxy_factors", "factor_analysis",
    "plot_efficient_frontier", "plot_weight_comparison",
    "plot_bl_returns_comparison",
    "plot_factor_betas", "plot_factor_contribution",
]


# ══════════════════════════════════════════════════════════════════════════════
# 효율적 투자선 차트
# ══════════════════════════════════════════════════════════════════════════════

def plot_efficient_frontier(
    result: dict,
    asset_points: dict | None = None,
) -> go.Figure:
    """효율적 투자선 + 최적 포트폴리오 + (선택) 개별 자산 위치."""
    frontier = result["frontier"]
    fig = go.Figure()

    if frontier:
        vols = [p["volatility"] for p in frontier]
        rets = [p["return"] for p in frontier]
        fig.add_trace(go.Scatter(
            x=vols, y=rets,
            mode="markers" if len(frontier) > 50 else "lines+markers",
            marker=dict(size=4, color="#3b82f6", opacity=0.5),
            line=dict(color="#3b82f6", width=1.5),
            name="효율적 투자선",
        ))

    fig.add_trace(go.Scatter(
        x=[result["volatility"]], y=[result["expected_return"]],
        mode="markers", marker=dict(size=16, color="#22c55e", symbol="star"),
        name=f"최적(Max Sharpe={result['sharpe_ratio']:.2f})",
    ))
    fig.add_trace(go.Scatter(
        x=[result["equal_weight_volatility"]], y=[result["equal_weight_return"]],
        mode="markers", marker=dict(size=12, color="#f59e0b", symbol="diamond"),
        name=f"동일비중(Sharpe={result['equal_weight_sharpe']:.2f})",
    ))

    if asset_points:
        for ticker, (vol, ret) in asset_points.items():
            fig.add_trace(go.Scatter(
                x=[vol], y=[ret], mode="markers+text",
                marker=dict(size=9, color="#8b949e"),
                text=[ticker], textposition="top center",
                textfont=dict(size=10, color="#8b949e"),
                showlegend=False,
            ))

    fig.update_layout(
        title=dict(text="효율적 투자선 (Efficient Frontier)", font=dict(size=14)),
        xaxis_title="연변동성 (%)", yaxis_title="기대 연수익률 (%)", height=440,
    )
    return fig


def plot_weight_comparison(result: dict, current_weights: dict | None = None) -> go.Figure:
    """최적 비중 vs 현재 비중 막대 비교."""
    opt_weights = result["weights"]
    tickers     = list(opt_weights.keys())

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=tickers, y=[opt_weights[t] * 100 for t in tickers],
        name="최적(Max Sharpe) 비중", marker_color="#22c55e",
    ))
    if current_weights:
        fig.add_trace(go.Bar(
            x=tickers, y=[current_weights.get(t, 0) * 100 for t in tickers],
            name="현재 비중", marker_color="#3b82f6",
        ))
    fig.update_layout(
        title=dict(text="비중 비교: 최적 vs 현재", font=dict(size=14)),
        yaxis_title="비중 (%)", barmode="group", height=380,
    )
    return fig


# ══════════════════════════════════════════════════════════════════════════════
# Black-Litterman 차트
# ══════════════════════════════════════════════════════════════════════════════

def plot_bl_returns_comparison(result: dict) -> go.Figure:
    """시장균형 수익률(Π) vs View 결합 후(E[R]) 비교 막대."""
    implied   = result["implied_returns"]
    posterior = result["posterior_returns"]
    tickers   = list(implied.keys())

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=tickers, y=[implied[t] for t in tickers],
        name="시장균형 기대수익률(Π)", marker_color="#8b949e",
    ))
    fig.add_trace(go.Bar(
        x=tickers, y=[posterior[t] for t in tickers],
        name="View 결합 후(E[R])", marker_color="#f59e0b",
    ))
    fig.update_layout(
        title=dict(text="Black-Litterman: 시장균형 vs 국면 시그널 반영 후",
                   font=dict(size=13)),
        yaxis_title="기대 연수익률 (%)", barmode="group", height=380,
    )
    return fig


# ══════════════════════════════════════════════════════════════════════════════
# 팩터 분석 차트
# ══════════════════════════════════════════════════════════════════════════════

def plot_factor_betas(result: dict) -> go.Figure:
    """팩터별 베타(노출도) 막대그래프."""
    factors = list(result["betas"].keys())
    betas   = list(result["betas"].values())
    colors  = ["#3b82f6" if b >= 0 else "#ef4444" for b in betas]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=factors, y=betas, marker_color=colors,
        text=[f"{b:+.2f}" for b in betas], textposition="outside",
    ))
    fig.add_hline(y=0, line=dict(color="#8b949e", width=1))
    fig.update_layout(
        title=dict(
            text=f"팩터 노출도(베타) · R²={result['r_squared']:.2f} · 알파={result['alpha']:+.1f}%/년",
            font=dict(size=13),
        ),
        yaxis_title="베타", height=380,
    )
    return fig


def plot_factor_contribution(result: dict) -> go.Figure:
    """각 팩터가 연수익률에 기여한 정도 막대그래프."""
    contrib = result["factor_contribution"]
    names   = list(contrib.keys()) + ["알파(고유 초과수익)"]
    values  = list(contrib.values()) + [result["alpha"]]
    colors  = ["#3b82f6", "#9b59b6", "#f39c12", "#1abc9c", "#22c55e"][:len(names)]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=names, y=values, marker_color=colors,
        text=[f"{v:+.1f}%" for v in values], textposition="outside",
    ))
    fig.add_hline(y=0, line=dict(color="#8b949e", width=1))
    fig.update_layout(
        title=dict(text="연수익률 성분 분해 (기여도 %)", font=dict(size=13)),
        yaxis_title="기여도 (%/년)", height=380,
    )
    return fig

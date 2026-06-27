"""
portfolio_optimizer_page.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━
TAB 2 — AI 포트폴리오 최적화 & 통계
- 섹션1: 스마트 포트폴리오 최적화 (Max Sharpe Ratio)
- 섹션2: 유전자 팩터 분석 (Market/Size/Value/Momentum 성분 분해)
"""

import numpy as np
import pandas as pd
import streamlit as st

from portfolio_optimizer import (
    optimize_max_sharpe,
    optimize_black_litterman,
    build_regime_views,
    plot_efficient_frontier,
    plot_weight_comparison,
    plot_bl_returns_comparison,
    generate_proxy_factors,
    factor_analysis,
    plot_factor_betas,
    plot_factor_contribution,
    _HAS_PYPFOPT,
)

_DARK_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(color="#e0e0e0", family="JetBrains Mono, monospace"),
    xaxis=dict(gridcolor="#21262d"),
    yaxis=dict(gridcolor="#21262d"),
    legend=dict(font=dict(size=10)),
    margin=dict(l=10, r=10, t=50, b=10),
)


def _style(fig):
    fig.update_layout(**_DARK_LAYOUT)
    return fig


# ── 5초 메모리 방패: 무위험수익률/비중 슬라이더 조작 시 야후 API 과다호출 방지
@st.cache_data(ttl=5)
def _load_close_history(tickers_tuple, period="2y"):
    import yfinance as yf
    data = yf.download(list(tickers_tuple), period=period, progress=False, auto_adjust=True)
    if isinstance(data.columns, pd.MultiIndex):
        return data['Close'].ffill()
    return data[['Close']].rename(columns={'Close': tickers_tuple[0]}).ffill()


def render():
    st.markdown("""
    <div style="padding:12px 0 8px;">
      <div style="font-size:10px;color:#58a6ff;font-weight:700;letter-spacing:2px;">QUANTITATIVE ANALYSIS</div>
      <div style="font-size:20px;font-weight:700;color:#f39c12;">PORTFOLIO OPTIMIZATION & FACTOR ANALYSIS</div>
      <div style="font-size:11px;color:#8b949e;">
        Max Sharpe 최적화 {engine} · 다중회귀 팩터 분해
      </div>
    </div>
    """.format(engine="(PyPortfolioOpt)" if _HAS_PYPFOPT else "(NumPy/SciPy 엔진)"),
    unsafe_allow_html=True)

    if not _HAS_PYPFOPT:
        st.caption("💡 `pip install PyPortfolioOpt` 설치 시 더 정교한 최적화 옵션(L2 정규화 등)을 사용할 수 있습니다. 지금은 NumPy/SciPy 기반 동일 알고리즘으로 동작합니다.")

    holdings = st.session_state.get("holdings") or {}
    port_tickers = [t for t in holdings if t != 'CASH']

    if not port_tickers:
        st.warning("⚠️ 알파 터미널에서 포트폴리오를 먼저 로드하세요.")
        return

    with st.spinner("시장 데이터 로딩 중..."):
        close_df = _load_close_history(tuple(port_tickers))

    if close_df.empty:
        st.error("시장 데이터를 불러올 수 없습니다.")
        return

    valid_tickers = [t for t in port_tickers if t in close_df.columns]
    returns_df = close_df[valid_tickers].pct_change().dropna()

    # 현재 비중 계산 (비교용)
    latest = close_df.iloc[-1]
    values = {t: float(latest[t]) * holdings[t]['q'] for t in valid_tickers}
    cash_val = holdings.get('CASH', {}).get('q', 0)
    total_val = sum(values.values()) + cash_val
    current_weights = {t: v / total_val for t, v in values.items()} if total_val > 0 else {}

    tab1, tab2 = st.tabs(["⚖️ 스마트 포트폴리오 최적화", "🧬 유전자 팩터 분석"])

    with tab1:
        _render_optimization_tab(returns_df, valid_tickers, current_weights, holdings)

    with tab2:
        _render_factor_tab(returns_df, current_weights)


# ── 섹션 1: 포트폴리오 최적화 ──────────────────────────────────────────────────
def _render_optimization_tab(returns_df: pd.DataFrame, tickers: list[str],
                              current_weights: dict, holdings: dict):
    st.markdown("<div style='font-size:11px;color:#8b949e;line-height:1.7;margin-bottom:10px;'>"
                "종목만 정해지면 <b style='color:#e0e0e0;'>위험 대비 수익률(샤프비율)</b>이 "
                "가장 극대화되는 <b style='color:#22c55e;'>황금 비중</b>을 수학적으로 계산합니다."
                "</div>", unsafe_allow_html=True)

    method_choice = st.radio(
        "최적화 모델",
        ["전통 MVO (과거 데이터 기반)", "Black-Litterman (시장 국면 시그널 반영)"],
        horizontal=True, key="opt_method",
        help="전통 MVO는 과거 평균수익률을 그대로 사용 — 과거 승자에 비중이 쏠리기 쉬움. "
             "Black-Litterman은 시장균형에서 출발해 현재 시장 국면(Bull/Bear) 전망을 "
             "베이지안 방식으로 결합해 더 안정적인 비중을 도출합니다."
    )
    use_bl = method_choice.startswith("Black")

    col1, col2 = st.columns(2)
    with col1:
        rf_rate = st.slider("무위험수익률 (연, %)", 0.0, 8.0, 4.0, 0.25, key="opt_rf") / 100
    with col2:
        max_weight = st.slider("종목당 최대 비중 (%)", 20, 100, 100, 5, key="opt_maxw") / 100

    regime_views = {}
    detected_regime = None

    if use_bl:
        st.markdown("<div style='font-size:10px;color:#8b949e;font-weight:700;letter-spacing:1.5px;margin:16px 0 8px;'>🌡️ 시장 국면 시그널 (View)</div>", unsafe_allow_html=True)

        view_source = st.radio(
            "View 입력 방식",
            ["K-means 국면감지 자동 연동", "직접 선택"],
            horizontal=True, key="opt_view_source"
        )

        sector_map = {t: holdings.get(t, {}).get("sector", "") for t in tickers}

        if view_source == "K-means 국면감지 자동 연동":
            if st.button("🔍 현재 시장 국면 감지 (S&P500 기준)", key="opt_detect_regime"):
                with st.spinner("K-means 국면 분석 중..."):
                    try:
                        from trading_signals import detect_market_regime
                        spx_df = _load_close_history(("^GSPC",), period="2y")
                        spx_price = spx_df["^GSPC"].dropna()
                        regime_result = detect_market_regime(spx_price, n_regimes=3)
                        st.session_state["_detected_regime"] = regime_result["current_regime"]
                    except Exception as e:
                        st.error(f"국면 감지 실패: {e}")

            detected_regime = st.session_state.get("_detected_regime")
            if detected_regime:
                regime_color = {"Bull": "#22c55e", "Bear": "#ef4444", "Sideways": "#8b949e"}.get(detected_regime, "#3b82f6")
                st.markdown(f"""
                <div style="background:#0d1117;border:1px solid #30363d;border-left:3px solid {regime_color};
                             padding:10px 14px;border-radius:2px;margin:8px 0;">
                  <span style="color:#8b949e;font-size:11px;">감지된 국면</span>
                  <span style="color:{regime_color};font-size:15px;font-weight:700;margin-left:8px;">{detected_regime}</span>
                </div>
                """, unsafe_allow_html=True)
                regime_views = build_regime_views(tickers, sector_map, detected_regime)
            else:
                st.info("버튼을 눌러 현재 시장 국면을 먼저 감지하세요.")
        else:
            manual_regime = st.selectbox("국면 직접 선택", ["Bull", "Bear", "Sideways"], key="opt_manual_regime")
            detected_regime = manual_regime
            regime_views = build_regime_views(tickers, sector_map, manual_regime)

        view_confidence = st.slider(
            "View 확신도", 0.1, 0.9, 0.5, 0.1, key="opt_view_conf",
            help="높을수록 국면 시그널이 시장균형보다 강하게 반영됨"
        )

        if regime_views:
            st.caption(f"💡 적용될 View: {', '.join(f'{t} {v:+.0%}' for t, v in regime_views.items())}")
        elif detected_regime == "Sideways":
            st.caption("💡 Sideways 국면에서는 View를 주입하지 않고 시장균형만 사용합니다.")

    if st.button("▶ 최적 비중 계산", type="primary", use_container_width=True, key="opt_run"):
        with st.spinner("효율적 투자선 계산 중..."):
            if use_bl:
                result = optimize_black_litterman(
                    returns_df[tickers],
                    market_weights=current_weights,
                    views=regime_views,
                    view_confidence=view_confidence,
                    risk_free_rate=rf_rate,
                    weight_bounds=(0.0, max_weight),
                )
            else:
                result = optimize_max_sharpe(
                    returns_df[tickers], risk_free_rate=rf_rate,
                    weight_bounds=(0.0, max_weight),
                )

        st.markdown("---")

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.metric("최적 샤프비율", f"{result['sharpe_ratio']:.2f}",
                      delta=f"동일비중 대비 {result['sharpe_ratio']-result['equal_weight_sharpe']:+.2f}")
        with c2:
            st.metric("기대 연수익률", f"{result['expected_return']:.1f}%")
        with c3:
            st.metric("연변동성", f"{result['volatility']:.1f}%")
        with c4:
            st.metric("최적화 엔진", result['method'])

        if use_bl and result.get("has_views"):
            st.success(f"✅ '{detected_regime}' 국면 시그널이 반영된 비중입니다. "
                       "공격형/방어형 섹터 비중이 시장균형 대비 조정되었습니다.")

        # 최적 비중 테이블
        st.markdown("<div style='font-size:10px;color:#8b949e;font-weight:700;letter-spacing:1.5px;margin:16px 0 8px;'>💰 황금 비중</div>", unsafe_allow_html=True)
        w_rows = []
        for t in tickers:
            opt_w = result["weights"].get(t, 0) * 100
            cur_w = current_weights.get(t, 0) * 100
            w_rows.append({
                "Ticker": t, "최적 비중(%)": round(opt_w, 1),
                "현재 비중(%)": round(cur_w, 1), "변화(%p)": round(opt_w - cur_w, 1),
            })
        w_df = pd.DataFrame(w_rows).sort_values("최적 비중(%)", ascending=False)
        st.dataframe(
            w_df, use_container_width=True, hide_index=True,
            column_config={
                "최적 비중(%)": st.column_config.NumberColumn(format="%.1f%%"),
                "현재 비중(%)": st.column_config.NumberColumn(format="%.1f%%"),
                "변화(%p)": st.column_config.NumberColumn(format="%+.1f"),
            }
        )

        # 시각화
        fig_w = plot_weight_comparison(result, current_weights)
        st.plotly_chart(_style(fig_w), use_container_width=True)

        if use_bl and result.get("has_views"):
            fig_bl = plot_bl_returns_comparison(result)
            st.plotly_chart(_style(fig_bl), use_container_width=True)
            st.caption(
                "💡 회색 막대는 시장균형(시총비중 기준 역산) 기대수익률, "
                "주황 막대는 국면 시그널을 베이지안으로 결합한 최종 기대수익률입니다. "
                "두 값의 차이가 클수록 국면 시그널의 영향력이 큰 것입니다."
            )

        # 개별 자산 위치 계산 (효율적 투자선 위에 점으로 표시)
        asset_points = {}
        for t in tickers:
            r = returns_df[t]
            asset_points[t] = (r.std() * np.sqrt(252) * 100, r.mean() * 252 * 100)

        fig_ef = plot_efficient_frontier(result, asset_points)
        st.plotly_chart(_style(fig_ef), use_container_width=True)

        st.caption(
            "💡 별표(★)는 최적(Max Sharpe) 포트폴리오, 다이아몬드(◆)는 동일비중 포트폴리오, "
            "회색 점은 개별 종목의 위험-수익 위치입니다."
        )


# ── 섹션 2: 팩터 분석 ─────────────────────────────────────────────────────────
def _render_factor_tab(returns_df: pd.DataFrame, current_weights: dict):
    st.markdown("<div style='font-size:11px;color:#8b949e;line-height:1.7;margin-bottom:10px;'>"
                "내 포트폴리오가 돈을 번 진짜 원인이 "
                "<b style='color:#3b82f6;'>시장(Market)</b> · "
                "<b style='color:#9b59b6;'>소형주(Size)</b> · "
                "<b style='color:#f39c12;'>가치주(Value)</b> · "
                "<b style='color:#1abc9c;'>모멘텀(Momentum)</b> 중 무엇 때문인지 분해합니다."
                "</div>", unsafe_allow_html=True)

    st.caption(
        "⚠️ 실제 Fama-French 팩터 데이터(Ken French Data Library) 대신, "
        "시장 수익률에 기반한 통계적 근사 팩터를 사용합니다. 방향성 참고용으로 활용하세요."
    )

    tickers = list(returns_df.columns)
    if not tickers:
        st.warning("분석할 종목 데이터가 없습니다.")
        return

    # 포트폴리오 합성 수익률 (현재 비중 기준)
    weights_arr = np.array([current_weights.get(t, 0) for t in tickers])
    if weights_arr.sum() == 0:
        weights_arr = np.full(len(tickers), 1 / len(tickers))
    else:
        weights_arr = weights_arr / weights_arr.sum()

    portfolio_returns = (returns_df[tickers].values @ weights_arr)

    # 시장 수익률 프록시로 S&P500 사용
    with st.spinner("시장 벤치마크(S&P500) 로딩 중..."):
        try:
            spx_df = _load_close_history(("^GSPC",))
            market_returns = spx_df["^GSPC"].pct_change().dropna().values
        except Exception:
            market_returns = portfolio_returns  # 폴백: 포트폴리오 자체를 시장으로 근사

    if st.button("▶ 팩터 분해 실행", type="primary", use_container_width=True, key="factor_run"):
        n_days = min(len(portfolio_returns), len(market_returns))
        factors = generate_proxy_factors(market_returns, n_days, seed=0)
        result = factor_analysis(portfolio_returns[-n_days:], factors)

        st.markdown("---")

        c1, c2, c3 = st.columns(3)
        with c1:
            st.metric("연환산 알파", f"{result['alpha']:+.2f}%",
                      help="팩터로 설명되지 않는 순수 초과수익. 양수면 종목선정 능력이 있다는 신호")
        with c2:
            st.metric("모델 설명력 (R²)", f"{result['r_squared']:.2f}",
                      help="1에 가까울수록 4팩터가 수익률 변동을 잘 설명함")
        with c3:
            st.metric("잔차(고유) 변동성", f"{result['residual_vol']:.1f}%",
                      help="팩터로 설명 안 되는 종목 고유의 변동성")

        # 베타 표
        st.markdown("<div style='font-size:10px;color:#8b949e;font-weight:700;letter-spacing:1.5px;margin:16px 0 8px;'>🧬 팩터 노출도(베타)</div>", unsafe_allow_html=True)
        beta_df = pd.DataFrame([
            {"팩터": k, "베타": round(v, 2), "해석": _interpret_beta(k, v)}
            for k, v in result["betas"].items()
        ])
        st.dataframe(beta_df, use_container_width=True, hide_index=True)

        fig_beta = plot_factor_betas(result)
        st.plotly_chart(_style(fig_beta), use_container_width=True)

        fig_contrib = plot_factor_contribution(result)
        st.plotly_chart(_style(fig_contrib), use_container_width=True)

        # 종합 해석
        dominant = max(result["betas"].items(), key=lambda x: abs(x[1]))
        st.markdown(f"""
        <div style="background:#0d1117;border:1px solid #30363d;border-left:3px solid #f39c12;
                     padding:14px;border-radius:2px;margin-top:8px;">
          <div style="font-size:13px;color:#e0e0e0;line-height:1.8;">
            가장 큰 영향을 미친 팩터는 <b style="color:#f39c12;">{dominant[0]}</b>
            (베타 {dominant[1]:+.2f})입니다.
            {'순수 종목선정 능력(알파)이 연 ' + f'{result["alpha"]:+.1f}%' + ' 기여했습니다.' if abs(result['alpha']) > 1 else '알파는 미미해 대부분의 수익이 팩터 노출로 설명됩니다.'}
          </div>
        </div>
        """, unsafe_allow_html=True)


def _interpret_beta(factor_name: str, beta: float) -> str:
    if abs(beta) < 0.15:
        return "노출 거의 없음"
    direction = "양(+)" if beta > 0 else "음(-)"
    strength = "강함" if abs(beta) > 0.6 else "보통"
    labels = {
        "Market": f"시장 민감도 {strength} ({direction})",
        "SMB(Size)": f"소형주 성향 {strength} ({direction})",
        "HML(Value)": f"가치주 성향 {strength} ({direction})" if beta > 0 else f"성장주 성향 {strength}",
        "MOM(Momentum)": f"모멘텀 추종 {strength} ({direction})",
    }
    return labels.get(factor_name, f"{direction} 노출")

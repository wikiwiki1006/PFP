"""
monte_carlo_page.py
━━━━━━━━━━━━━━━━━━━
몬테카를로 시뮬레이션 페이지 (Plotly 기반 — matplotlib 의존성 없음)
- 탭1: 포트폴리오 목표 수익률 달성 확률
- 탭2: 개별 종목 목표 주가 달성 확률
"""

import numpy as np
import pandas as pd
import streamlit as st

from monte_carlo import (
    monte_carlo_portfolio_target,
    monte_carlo_stock_target_price,
    plot_portfolio_distribution,
    plot_stock_distribution,
    monte_carlo_macro_scenario,
    plot_macro_distribution,
    plot_macro_paths,
    calculate_advanced_params,
    INDUSTRY_ETF_MAP,
)

# ── 다크 테마 Plotly 공통 레이아웃 ────────────────────────────────────────────
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


# ── 시장 데이터 로드 (5초 메모리 방패: 레버 조작 시 야후 API 과다호출 방지) ──
@st.cache_data(ttl=5)
def _load_close_history(tickers_tuple, period="3y"):
    import yfinance as yf
    data = yf.download(list(tickers_tuple), period=period, progress=False, auto_adjust=True)
    if isinstance(data.columns, pd.MultiIndex):
        return data['Close'].ffill()
    # 단일 티커일 경우
    return data[['Close']].rename(columns={'Close': tickers_tuple[0]}).ffill()


def render():
    st.markdown("""
    <div style="padding:12px 0 8px;">
      <div style="font-size:10px;color:#58a6ff;font-weight:700;letter-spacing:2px;">RISK SIMULATION</div>
      <div style="font-size:20px;font-weight:700;color:#2ecc71;">MONTE CARLO ANALYSIS</div>
      <div style="font-size:11px;color:#8b949e;">10,000회 시뮬레이션 · NumPy 벡터화 · 1년(252영업일)</div>
    </div>
    """, unsafe_allow_html=True)

    tab0, tab1, tab2 = st.tabs(["🌪️ 매크로 시나리오 터미널", "📊 포트폴리오 목표 수익률", "🎯 개별 종목 목표 주가"])

    with tab0:
        _render_macro_terminal_tab()

    with tab1:
        _render_portfolio_tab()

    with tab2:
        _render_stock_tab()


# ── 탭 1: 포트폴리오 목표 수익률 ──────────────────────────────────────────────
def _render_portfolio_tab():
    holdings = st.session_state.get("holdings")

    if not holdings:
        st.warning("⚠️ 알파 터미널에서 포트폴리오를 먼저 로드하세요.")
        return

    stock_tickers = [t for t in holdings if t != 'CASH']
    if not stock_tickers:
        st.warning("⚠️ 보유 종목이 없습니다.")
        return

    st.markdown("<div style='font-size:10px;color:#8b949e;font-weight:700;letter-spacing:1.5px;margin:8px 0;'>① 현재 포트폴리오 비중</div>", unsafe_allow_html=True)

    with st.spinner("시장 데이터 로딩 중..."):
        close_df = _load_close_history(tuple(stock_tickers))

    if close_df.empty:
        st.error("시장 데이터를 불러올 수 없습니다.")
        return

    latest = close_df.iloc[-1]

    # 비중 계산 (CASH 포함)
    values = {}
    for t in stock_tickers:
        if t in close_df.columns:
            values[t] = float(latest[t]) * holdings[t]['q']
    values['CASH'] = holdings.get('CASH', {}).get('q', 0)

    total_value = sum(values.values())
    weights_dict = {k: v / total_value for k, v in values.items() if total_value > 0}

    # 비중 테이블
    w_df = pd.DataFrame([
        {"자산": k, "비중(%)": round(w * 100, 1)}
        for k, w in weights_dict.items()
    ])
    st.dataframe(w_df, use_container_width=True, hide_index=True)

    # ── 일일 수익률 행렬 구성 (CASH = 0% 고정) ───────────────────────────────
    valid_tickers = [t for t in stock_tickers if t in close_df.columns]
    returns_df = close_df[valid_tickers].pct_change().dropna()
    returns_df['CASH'] = 0.0

    asset_order = valid_tickers + ['CASH']
    weights = np.array([weights_dict.get(t, 0) for t in asset_order])
    weights = weights / weights.sum()  # 정규화

    daily_returns = returns_df[asset_order].values

    st.markdown("<div style='font-size:10px;color:#8b949e;font-weight:700;letter-spacing:1.5px;margin:16px 0 8px;'>② 시뮬레이션 설정</div>", unsafe_allow_html=True)

    col1, col2, col3 = st.columns(3)
    with col1:
        target_return_pct = st.slider("목표 수익률 (연, %)", -20, 100, 15, 1)
    with col2:
        n_simulations = st.selectbox("시뮬레이션 횟수", [5000, 10000, 20000], index=1)
    with col3:
        n_days = st.selectbox("기간 (영업일)", [63, 126, 252, 504], index=2,
                              format_func=lambda x: {63:"3개월",126:"6개월",252:"1년",504:"2년"}[x])

    if st.button("▶ 시뮬레이션 실행", type="primary", use_container_width=True):
        with st.spinner("10,000개 경로 계산 중..."):
            result = monte_carlo_portfolio_target(
                weights=weights,
                daily_returns=daily_returns,
                target_return=target_return_pct / 100,
                n_simulations=n_simulations,
                n_days=n_days,
                seed=None,
            )

        st.markdown("---")

        # 결과 메트릭
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.metric("목표 달성 확률", f"{result['probability']:.2f}%")
        with c2:
            st.metric("평균 예상 수익률", f"{result['mean_return']:+.2f}%")
        with c3:
            st.metric("중간값 수익률", f"{result['median_return']:+.2f}%")
        with c4:
            st.metric("95% VaR (하위 5%)", f"{result['var_95']:+.2f}%")

        st.markdown(f"""
        <div style="background:#0d1117;border:1px solid #30363d;border-left:3px solid #2ecc71;
                     padding:14px;border-radius:2px;margin-top:8px;">
          <div style="font-size:14px;color:#e0e0e0;font-weight:600;">{result['message']}</div>
        </div>
        """, unsafe_allow_html=True)

        # 히스토그램
        fig = plot_portfolio_distribution(
            result, target_return=target_return_pct / 100, initial_value=total_value
        )
        st.plotly_chart(_style(fig), use_container_width=True)


# ── 탭 2: 개별 종목 목표 주가 ─────────────────────────────────────────────────
def _render_stock_tab():
    holdings = st.session_state.get("holdings") or {}
    port_tickers = [t for t in holdings if t != 'CASH']

    col_t1, col_t2 = st.columns([2, 1])
    with col_t1:
        if port_tickers:
            ticker = st.selectbox("티커 선택", port_tickers + ["직접 입력"])
            if ticker == "직접 입력":
                ticker = st.text_input("티커 심볼 입력", placeholder="예: NVDA").upper().strip()
        else:
            ticker = st.text_input("티커 심볼 입력", placeholder="예: NVDA").upper().strip()

    if not ticker:
        st.info("티커를 입력하거나 선택하세요.")
        return

    with st.spinner(f"{ticker} 데이터 로딩 중..."):
        try:
            close_df = _load_close_history((ticker,))
        except Exception as e:
            st.error(f"데이터 로드 실패: {e}")
            return

    if ticker not in close_df.columns or close_df[ticker].dropna().empty:
        st.error(f"{ticker} 데이터를 찾을 수 없습니다.")
        return

    current_price = float(close_df[ticker].dropna().iloc[-1])
    daily_returns = close_df[ticker].pct_change().dropna().values

    st.markdown(f"""
    <div style="background:#0d1117;border:1px solid #30363d;padding:10px;border-radius:2px;margin:8px 0;">
      <span style="color:#8b949e;font-size:11px;">현재가</span>
      <span style="color:#00e6ff;font-size:18px;font-weight:700;margin-left:8px;">${current_price:,.2f}</span>
      <span style="color:#8b949e;font-size:11px;margin-left:16px;">연환산 변동성</span>
      <span style="color:#e0e0e0;font-size:14px;font-weight:600;margin-left:8px;">
        {daily_returns.std() * np.sqrt(252) * 100:.1f}%
      </span>
    </div>
    """, unsafe_allow_html=True)

    col1, col2, col3 = st.columns(3)
    with col1:
        target_price = st.number_input("목표 주가 ($)", value=round(current_price * 1.2, 2), step=1.0)
    with col2:
        n_simulations = st.selectbox("시뮬레이션 횟수", [5000, 10000, 20000], index=1, key="stock_nsim")
    with col3:
        n_days = st.selectbox("기간 (영업일)", [63, 126, 252, 504], index=2, key="stock_ndays",
                              format_func=lambda x: {63:"3개월",126:"6개월",252:"1년",504:"2년"}[x])

    if st.button("▶ 시뮬레이션 실행", type="primary", use_container_width=True, key="stock_run"):
        with st.spinner("10,000개 가격 경로 계산 중..."):
            result = monte_carlo_stock_target_price(
                current_price=current_price,
                daily_returns=daily_returns,
                target_price=target_price,
                n_simulations=n_simulations,
                n_days=n_days,
                seed=None,
            )

        st.markdown("---")

        c1, c2, c3 = st.columns(3)
        with c1:
            st.metric("종가 기준 달성 확률", f"{result['probability_final']:.2f}%",
                      help="기간 종료 시점 가격이 목표가에 도달")
        with c2:
            st.metric("터치 기준 달성 확률", f"{result['probability_touch']:.2f}%",
                      help="기간 중 한 번이라도 목표가에 도달")
        with c3:
            upside = (target_price / current_price - 1) * 100
            st.metric("목표가까지 변동률", f"{upside:+.1f}%")

        st.markdown(f"""
        <div style="background:#0d1117;border:1px solid #30363d;border-left:3px solid #2ecc71;
                     padding:14px;border-radius:2px;margin-top:8px;">
          <div style="font-size:14px;color:#e0e0e0;font-weight:600;">{result['message']}</div>
        </div>
        """, unsafe_allow_html=True)

        fig_hist, fig_path = plot_stock_distribution(result, current_price, target_price)

        col_a, col_b = st.columns(2)
        with col_a:
            st.plotly_chart(_style(fig_hist), use_container_width=True)
        with col_b:
            st.plotly_chart(_style(fig_path), use_container_width=True)


# ── 탭 0: 매크로 시나리오 터미널 (Cox/Hawkes 점프 클러스터링) ─────────────────
def _render_macro_terminal_tab():
    st.markdown("""
    <div style="font-size:11px;color:#8b949e;line-height:1.7;margin-bottom:10px;">
    금리·환율 레버를 움직이면 <b style="color:#e0e0e0;">점프-확산(Jump-Diffusion) 엔진</b>이
    악재가 몰아치는 <b style="color:#ef4444;">시장 패닉 쏠림(클러스터링)</b>을 반영해
    1만 개의 평행우주를 다시 계산합니다.
    </div>
    """, unsafe_allow_html=True)

    holdings = st.session_state.get("holdings") or {}
    port_tickers = [t for t in holdings if t != 'CASH']

    # ── 대상 선택: 포트폴리오 전체 vs 개별 종목 ──────────────────────────────
    col_target, col_ticker = st.columns([1, 2])
    with col_target:
        target_mode = st.radio("시뮬레이션 대상", ["전체 포트폴리오", "개별 종목"], key="macro_target_mode")

    current_value = None
    daily_returns = None
    value_label = "포트폴리오 가치"

    # 개별 종목 모드에서 자동 산출된 고급 파라미터 (기본값: 보정 없음)
    auto_sector_beta = 1.0
    auto_drift_shim = 0.0
    adv_params = None

    # 추가 외생변수 보정값 (슬라이더 블록 이전 기본값 — 아래 슬라이더에서 덮어씀)
    extra_daily_drift = 0.0
    extra_daily_vol_factor = 1.0
    ticker_drift_adj = 0.0
    ticker_vol_adj = 1.0
    ticker = ""  # 개별 종목 모드에서 덮어씀

    if target_mode == "전체 포트폴리오":
        if not holdings or not port_tickers:
            st.warning("⚠️ 알파 터미널에서 포트폴리오를 먼저 로드하세요.")
            return

        with st.spinner("포트폴리오 데이터 로딩 중..."):
            close_df = _load_close_history(tuple(port_tickers))
        if close_df.empty:
            st.error("시장 데이터를 불러올 수 없습니다.")
            return

        latest = close_df.iloc[-1]
        values = {}
        for t in port_tickers:
            if t in close_df.columns:
                values[t] = float(latest[t]) * holdings[t]['q']
        values['CASH'] = holdings.get('CASH', {}).get('q', 0)
        total_value = sum(values.values())
        if total_value <= 0:
            st.error("포트폴리오 가치가 0입니다.")
            return
        weights_dict = {k: v / total_value for k, v in values.items()}

        valid_tickers = [t for t in port_tickers if t in close_df.columns]
        returns_df = close_df[valid_tickers].pct_change().dropna()
        returns_df['CASH'] = 0.0
        asset_order = valid_tickers + ['CASH']
        w = np.array([weights_dict.get(t, 0) for t in asset_order])
        w = w / w.sum()

        # 포트폴리오 합성 일일수익률 (가중합)
        daily_returns = (returns_df[asset_order].values @ w)
        current_value = total_value
        value_label = "포트폴리오 가치"

        st.caption(f"💼 현재 포트폴리오 가치: ₩{current_value:,.0f} "
                   f"({len(valid_tickers)}개 종목 + 현금)")
        st.caption("ℹ️ 포트폴리오 전체 모드에서는 종목별 펀더멘탈 지표가 혼합되어 "
                   "Sector Beta/Drift 자동산출을 적용하지 않습니다. 아래 슬라이더로 직접 조절하세요.")

    else:
        with col_ticker:
            if port_tickers:
                ticker = st.selectbox("티커 선택", port_tickers + ["직접 입력"], key="macro_ticker_sel")
                if ticker == "직접 입력":
                    ticker = st.text_input("티커 심볼 입력", placeholder="예: NVDA", key="macro_ticker_input").upper().strip()
            else:
                ticker = st.text_input("티커 심볼 입력", placeholder="예: NVDA", key="macro_ticker_input2").upper().strip()

        if not ticker:
            st.info("티커를 입력하거나 선택하세요.")
            return

        with st.spinner(f"{ticker} 데이터 로딩 중..."):
            try:
                close_df = _load_close_history((ticker,))
            except Exception as e:
                st.error(f"데이터 로드 실패: {e}")
                return

        if ticker not in close_df.columns or close_df[ticker].dropna().empty:
            st.error(f"{ticker} 데이터를 찾을 수 없습니다.")
            return

        current_value = float(close_df[ticker].dropna().iloc[-1])
        daily_returns = close_df[ticker].pct_change().dropna().values
        value_label = f"{ticker} 주가"

        st.caption(f"💼 현재가: ${current_value:,.2f}")

        # ── 산업 베타 + 펀더멘탈 Drift 자동 산출 ─────────────────────────────
        st.markdown("<div style='font-size:10px;color:#8b949e;font-weight:700;letter-spacing:1.5px;margin:16px 0 8px;'>🧮 산업 베타 & 펀더멘탈 자동 분석</div>", unsafe_allow_html=True)

        sector_hint = holdings.get(ticker, {}).get("sector", "") if ticker in holdings else ""
        default_etf = INDUSTRY_ETF_MAP.get(sector_hint, "SPY")

        col_etf1, col_etf2 = st.columns([2, 1])
        with col_etf1:
            etf_options = sorted(set(INDUSTRY_ETF_MAP.values()))
            etf_idx = etf_options.index(default_etf) if default_etf in etf_options else 0
            industry_etf = st.selectbox(
                "산업 대표 ETF", etf_options, index=etf_idx, key="macro_industry_etf",
                help=f"보유종목 섹터('{sector_hint}') 기준 추천: {default_etf}" if sector_hint else "베타 계산 기준이 될 산업 ETF"
            )
        with col_etf2:
            st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
            run_auto = st.button("🔍 자동 분석 실행", key="macro_run_advanced", use_container_width=True)

        cache_key = f"_adv_params_{ticker}_{industry_etf}"
        if run_auto:
            with st.spinner(f"{ticker} vs {industry_etf} 베타·펀더멘탈 계산 중..."):
                st.session_state[cache_key] = calculate_advanced_params(ticker, industry_etf)

        adv_params = st.session_state.get(cache_key)

        if adv_params:
            auto_sector_beta = adv_params["sector_beta"]
            auto_drift_shim = adv_params["fundamental_drift_shim"]

            if adv_params["is_fallback"]:
                st.warning("⚠️ " + " / ".join(adv_params["warnings"]) +
                          " — 기본값(Beta 1.0, Drift 보정 없음)으로 대체되었습니다.")

            beta_color = "#22c55e" if 0.8 <= auto_sector_beta <= 1.3 else "#f59e0b"
            shim_color = "#22c55e" if adv_params["annual_drift_bonus_pct"] > 0 else "#8b949e"

            c_b1, c_b2, c_b3, c_b4 = st.columns(4)
            with c_b1:
                st.metric("Sector Beta", f"{auto_sector_beta:.2f}",
                          help=f"{ticker} vs {industry_etf} 공분산 기반 베타")
            with c_b2:
                st.metric("펀더멘탈 Drift 가산", f"+{adv_params['annual_drift_bonus_pct']:.2f}%/년")
            with c_b3:
                fwd = adv_params["forward_pe"]
                trl = adv_params["trailing_pe"]
                st.metric("Fwd PE / Trail PE",
                          f"{fwd if fwd else 'N/A'} / {trl if trl else 'N/A'}")
            with c_b4:
                peg = adv_params["peg_ratio"]
                st.metric("PEG Ratio", f"{peg if peg else 'N/A'}")

            st.markdown(f"""
            <div style="background:#0d1117;border:1px solid #30363d;border-left:3px solid {shim_color};
                         padding:8px 14px;border-radius:2px;margin:6px 0 4px;">
              <span style="color:#8b949e;font-size:11px;">국면 판정</span>
              <span style="color:{shim_color};font-size:13px;font-weight:700;margin-left:8px;">{adv_params['regime']}</span>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.caption("💡 '자동 분석 실행' 버튼을 누르면 Sector Beta와 펀더멘탈 Drift 보정치가 "
                       "아래 슬라이더에 자동 반영됩니다. (미실행 시 기본값 Beta=1.0, 보정 없음 사용)")

    # ── 유저 입력: 목표 수익률 ────────────────────────────────────────────────
    st.markdown("<div style='font-size:10px;color:#8b949e;font-weight:700;letter-spacing:1.5px;margin:16px 0 8px;'>🎯 목표 설정</div>", unsafe_allow_html=True)
    target_return_pct = st.slider(
        "목표 수익률 (연, %)", -50, 150, 60, 5, key="macro_target_return",
        help="예: +60% = 1년 뒤 자산가치가 현재의 1.6배가 되는 것"
    )

    # ── 매크로 외생변수 레버 (확장) ──────────────────────────────────────────
    st.markdown("<div style='font-size:10px;color:#8b949e;font-weight:700;letter-spacing:1.5px;margin:16px 0 8px;'>🎛️ 매크로 외생변수 레버</div>", unsafe_allow_html=True)

    col_lev1, col_lev2 = st.columns(2)
    with col_lev1:
        rate_shock = st.slider(
            "미국 국채 금리 충격 (%p)", -2.0, 2.0, 0.0, 0.25, key="macro_rate_lever",
            help="+ : 금리 인상 충격(악재) · − : 금리 인하(완화)"
        )
    with col_lev2:
        fx_shock = st.slider(
            "원/달러 환율 충격 (%)", -10.0, 10.0, 0.0, 0.5, key="macro_fx_lever",
            help="+ : 원화 약세(악재) · − : 원화 강세(완화)"
        )

    col_lev3, col_lev4 = st.columns(2)
    with col_lev3:
        vix_shock = st.slider(
            "VIX 공포지수 충격 (%)", -30.0, 80.0, 0.0, 5.0, key="macro_vix_lever",
            help="+ : VIX 급등(시장 패닉 강화) · − : VIX 하락(시장 안정)"
        )
    with col_lev4:
        oil_shock = st.slider(
            "유가(WTI) 충격 (%)", -40.0, 60.0, 0.0, 5.0, key="macro_oil_lever",
            help="+ : 유가 급등(에너지 섹터 호재, 소비재 악재) · − : 유가 급락"
        )

    col_lev5, col_lev6 = st.columns(2)
    with col_lev5:
        inflation_shock = st.slider(
            "인플레이션 충격 (%p)", -2.0, 4.0, 0.0, 0.25, key="macro_inflation_lever",
            help="+ : 인플레이션 상승(금리인상 압력 → Drift 하락) · − : 디플레이션"
        )
    with col_lev6:
        gdp_shock = st.slider(
            "GDP 성장률 충격 (%p)", -5.0, 5.0, 0.0, 0.5, key="macro_gdp_lever",
            help="+ : 경기확장(Drift 상승) · − : 경기침체(Drift 하락)"
        )

    # 추가 외생변수 → Drift/Volatility 보정 계수로 변환
    # VIX +10% → σ 5% 추가, Oil +30% → μ 1% 추가(에너지 포트에선 다름)
    vix_vol_adj = vix_shock * 0.005        # VIX 1%p 당 일간 σ 0.5bp 추가
    inflation_drift_adj = -inflation_shock * 0.004   # 인플레 1%p당 연 μ -0.4%
    gdp_drift_adj = gdp_shock * 0.006      # GDP 1%p당 연 μ +0.6%
    oil_drift_adj = oil_shock * 0.001      # 유가 1% 당 포트 μ +0.1bp (혼합 섹터)

    # 연간 추가 Drift(합산)를 daily shim으로 변환
    extra_daily_drift = (inflation_drift_adj + gdp_drift_adj + oil_drift_adj) / 252
    extra_daily_vol_factor = 1.0 + vix_vol_adj   # 변동성 배율

    sector_beta = st.slider(
        "포트폴리오 매크로 민감도 (Sector β)", 0.0, 3.0, float(round(auto_sector_beta, 2)), 0.1,
        key=f"macro_sector_beta_{round(auto_sector_beta, 2)}",
        help="1.0=시장평균 · 1.5=AI/성장주 등 고민감 섹터 · 0.5=방어주 등 저민감 섹터. "
             "개별 종목 모드에서 '자동 분석 실행'을 누르면 산업ETF 공분산 기반 베타가 "
             "자동으로 채워집니다. 슬라이더로 직접 덮어쓸 수도 있습니다."
    )

    if adv_params and not adv_params["is_fallback"]:
        st.caption(f"✓ Sector Beta가 {ticker if target_mode=='개별 종목' else ''} vs "
                   f"{adv_params['industry_etf']} 자동산출값({auto_sector_beta:.2f})으로 초기화되었습니다. "
                   "필요시 위 슬라이더로 직접 조정하세요.")

    # ── 개별 종목 GBM 파라미터 가중치 (개별 종목 모드일 때만 표시) ─────────────
    ticker_drift_adj = 0.0
    ticker_vol_adj = 1.0
    if target_mode == "개별 종목" and ticker:
        st.markdown(
            "<div style='font-size:10px;color:#8b949e;font-weight:700;letter-spacing:1.5px;"
            "margin:16px 0 8px;'>🎯 개별 종목 GBM 파라미터 직접 주입</div>",
            unsafe_allow_html=True,
        )
        st.caption(
            f"**{ticker}** 의 Drift(μ)와 Volatility(σ)를 슬라이더로 직접 보정하면 "
            "기하 브라운 운동 공식 내부 파라미터가 실시간 변경됩니다."
        )
        col_d, col_v = st.columns(2)
        with col_d:
            ticker_drift_pct = st.slider(
                f"{ticker} 연간 Drift(μ) 전망 보정 (%/년)", -60, 120, 0, 5,
                key=f"ticker_drift_{ticker}",
                help="양수: 해당 종목 상승 전망 → μ 추가 · 음수: 하락 전망 → μ 감소",
            )
            ticker_drift_adj = ticker_drift_pct / 100 / 252   # 일간 drift 보정
        with col_v:
            ticker_vol_pct = st.slider(
                f"{ticker} Volatility(σ) 배율 조정 (%)", 50, 200, 100, 5,
                key=f"ticker_vol_{ticker}",
                help="100% = 기존 σ 그대로 · 150% = σ 1.5배(고변동성 시나리오)",
            )
            ticker_vol_adj = ticker_vol_pct / 100.0

        # 실시간 프리뷰
        if ticker_drift_pct != 0 or ticker_vol_pct != 100:
            d_clr = "#22c55e" if ticker_drift_pct >= 0 else "#ef4444"
            v_clr = "#ef4444" if ticker_vol_pct > 100 else "#22c55e"
            st.markdown(
                f'<div style="background:#0d1117;border:1px solid #30363d;border-left:3px solid #58a6ff;'
                f'padding:8px 14px;border-radius:2px;margin:4px 0;">'
                f'<span style="color:#8b949e;font-size:11px;">GBM Drift 보정</span>'
                f'<span style="color:{d_clr};font-size:14px;font-weight:700;margin-left:8px;">'
                f'{ticker_drift_pct:+d}%/년</span>'
                f'<span style="color:#8b949e;font-size:11px;margin-left:16px;">Volatility 배율</span>'
                f'<span style="color:{v_clr};font-size:14px;font-weight:700;margin-left:8px;">'
                f'×{ticker_vol_adj:.2f}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

    # 매크로 보정 실시간 프리뷰 (drift/vol 직접 보정 + 점프 패닉 강도)
    from monte_carlo import _macro_to_jump_params, macro_drift_vol_multipliers
    preview_jp = _macro_to_jump_params(rate_shock, fx_shock, 0.3)
    preview_mv = macro_drift_vol_multipliers(rate_shock, fx_shock, sector_beta)
    stress_pct = preview_jp["stress_level"] * 100
    stress_color = "#22c55e" if stress_pct < 25 else ("#f59e0b" if stress_pct < 70 else "#ef4444")
    stress_label = "평시" if stress_pct < 5 else ("주의" if stress_pct < 40 else ("경계" if stress_pct < 75 else "패닉"))

    mu_color = "#22c55e" if preview_mv["mu_shift_pct"] >= 0 else "#ef4444"
    sigma_color = "#22c55e" if preview_mv["sigma_shift_pct"] <= 0 else "#ef4444"
    shim_annual_pct = auto_drift_shim * 252 * 100
    shim_line = (
        f'<div style="margin-top:6px;">'
        f'<span style="color:#8b949e;font-size:11px;">펀더멘탈 Drift 가산</span>'
        f'<span style="color:#22c55e;font-size:14px;font-weight:700;margin-left:8px;">+{shim_annual_pct:.2f}%/년</span>'
        f'</div>'
    ) if shim_annual_pct > 1e-6 else ""

    st.markdown(f"""
    <div style="background:#0d1117;border:1px solid #30363d;border-left:3px solid {stress_color};
                 padding:10px 14px;border-radius:2px;margin:8px 0 16px;">
      <div style="margin-bottom:6px;">
        <span style="color:#8b949e;font-size:11px;">패닉 강도 지수</span>
        <span style="color:{stress_color};font-size:16px;font-weight:700;margin-left:8px;">{stress_pct:.0f}%</span>
        <span style="color:{stress_color};font-size:11px;font-weight:700;margin-left:6px;">[{stress_label}]</span>
        <span style="color:#8b949e;font-size:11px;margin-left:16px;">예상 점프 발생/년</span>
        <span style="color:#e0e0e0;font-size:13px;font-weight:600;margin-left:6px;">
          {preview_jp['lambda_base']*252:.1f}회
        </span>
      </div>
      <div>
        <span style="color:#8b949e;font-size:11px;">Drift(μ) 보정</span>
        <span style="color:{mu_color};font-size:14px;font-weight:700;margin-left:8px;">{preview_mv['mu_shift_pct']:+.1f}%</span>
        <span style="color:#8b949e;font-size:11px;margin-left:16px;">Volatility(σ) 보정</span>
        <span style="color:{sigma_color};font-size:14px;font-weight:700;margin-left:8px;">{preview_mv['sigma_shift_pct']:+.1f}%</span>
      </div>
      {shim_line}
    </div>
    """, unsafe_allow_html=True)

    # ── 시뮬레이션 옵션 ───────────────────────────────────────────────────────
    col_o1, col_o2 = st.columns(2)
    with col_o1:
        n_simulations = st.selectbox("시뮬레이션 횟수", [5000, 10000, 20000], index=1, key="macro_nsim")
    with col_o2:
        n_days = st.selectbox("기간 (영업일)", [63, 126, 252, 504], index=2, key="macro_ndays",
                              format_func=lambda x: {63:"3개월",126:"6개월",252:"1년",504:"2년"}[x])

    if st.button("▶ 평행우주 시뮬레이션 실행", type="primary", use_container_width=True, key="macro_run"):
        with st.spinner(f"{n_simulations:,}개 평행우주 계산 중... (Drift/Vol 보정 + 펀더멘탈 + 점프-확산 엔진)"):
            # ── 추가 외생변수 + 개별 종목 GBM 주입 ─────────────────────────
            adj_returns = daily_returns.copy().astype(float)
            # VIX 기반 변동성 배율 적용
            if extra_daily_vol_factor != 1.0:
                r_mean = np.mean(adj_returns)
                adj_returns = r_mean + (adj_returns - r_mean) * extra_daily_vol_factor
            # 매크로 추가 drift (인플레·GDP·유가)
            if extra_daily_drift != 0.0:
                adj_returns = adj_returns + extra_daily_drift
            # 개별 종목 GBM drift + vol 주입 (개별 종목 모드)
            if target_mode == "개별 종목" and (ticker_drift_adj != 0.0 or ticker_vol_adj != 1.0):
                r_mean2 = np.mean(adj_returns)
                adj_returns = r_mean2 + ticker_drift_adj + (adj_returns - r_mean2) * ticker_vol_adj

            result = monte_carlo_macro_scenario(
                current_value=current_value,
                daily_returns=adj_returns,
                target_return=target_return_pct / 100,
                rate_shock_pp=rate_shock,
                fx_shock_pct=fx_shock,
                sector_beta=sector_beta,
                fundamental_drift_shim=auto_drift_shim,
                n_simulations=n_simulations,
                n_days=n_days,
                seed=None,
            )

        st.markdown("---")

        # ── 확률 쪼개기: 터치 vs 종가 ─────────────────────────────────────────
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.metric("종가 달성 확률", f"{result['probability_final']:.2f}%",
                      help="기간 종료 시점(1년 뒤) 자리에 안착할 확률")
        with c2:
            st.metric("터치 달성 확률", f"{result['probability_touch']:.2f}%",
                      help="기간 중 단 하루라도 목표를 스칠 확률")
        with c3:
            st.metric("95% VaR", f"{result['var_95']:+.1f}%",
                      help="하위 5% 최악의 시나리오에서의 손실률")
        with c4:
            st.metric("95% CVaR", f"{result['cvar_95']:+.1f}%",
                      help="하위 5% 시나리오들의 평균 손실률 (VaR보다 보수적)")

        st.markdown(f"""
        <div style="background:#0d1117;border:1px solid #30363d;border-left:3px solid #2ecc71;
                     padding:14px;border-radius:2px;margin-top:8px;">
          <div style="font-size:14px;color:#e0e0e0;font-weight:600;">{result['message']}</div>
        </div>
        """, unsafe_allow_html=True)

        # ── 시각화: 히스토그램 + 경로(점프 클러스터링 표시) ──────────────────
        fig_hist = plot_macro_distribution(
            result, current_value, target_return_pct / 100, value_label=value_label
        )
        st.plotly_chart(_style(fig_hist), use_container_width=True)

        fig_path = plot_macro_paths(
            result, current_value, target_return_pct / 100, value_label=value_label
        )
        st.plotly_chart(_style(fig_path), use_container_width=True)

        st.caption(
            "💡 빨간 점은 점프(패닉성 급락) 발생 지점입니다. 레버를 세게 꺾을수록 "
            "점프가 시간축에서 뭉쳐서 나타나는 클러스터링 현상을 확인할 수 있어요."
        )

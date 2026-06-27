"""
trading_signals_page.py
━━━━━━━━━━━━━━━━━━━━━━━
TAB 3 — 실전 매매 타이밍 엔진
- 매크로 저승사자 레이더 (상단 상시 표시, 경보 시 모든 매수신호 차단)
- 페어 트레이딩(상관계수 락) / 평균회귀 / 모멘텀 돌파 / 시장 국면 감지
"""

import numpy as np
import pandas as pd
import streamlit as st

from trading_signals import (
    fetch_macro_doom_indicators, evaluate_doom_radar, apply_doom_filter, plot_doom_radar,
    pairs_trading_signal, plot_pairs_trading, find_best_pair,
    mean_reversion_signal, plot_mean_reversion,
    momentum_breakout_signal, plot_momentum_breakout,
    detect_market_regime, plot_regime_chart, plot_regime_scatter,
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
    for ax in fig.layout:
        if ax.startswith("xaxis") or ax.startswith("yaxis"):
            fig.layout[ax].gridcolor = "#21262d"
    return fig


def _clean(sig):
    """None/NaN 신호값을 사람이 읽을 수 있는 라벨로 변환."""
    if sig is None or (isinstance(sig, float) and pd.isna(sig)):
        return "대기 (신호 없음)"
    return sig


# ── 5초 메모리 방패: 레버/슬라이더 조작 시 야후 API 과다호출 방지
@st.cache_data(ttl=5)
def _load_history(tickers_tuple, period="1y"):
    import yfinance as yf
    data = yf.download(list(tickers_tuple), period=period, progress=False, auto_adjust=True)
    return data


def render():
    st.markdown("""
    <div style="padding:12px 0 8px;">
      <div style="font-size:10px;color:#58a6ff;font-weight:700;letter-spacing:2px;">TRADING SIGNALS</div>
      <div style="font-size:20px;font-weight:700;color:#ef4444;">⚡ TIMING ENGINE</div>
      <div style="font-size:11px;color:#8b949e;">매크로 저승사자 레이더 · 페어 트레이딩 · 평균회귀 · 모멘텀 돌파 · K-means 시장국면 감지</div>
    </div>
    """, unsafe_allow_html=True)

    # ── 매크로 저승사자 레이더 (항상 상단에 표시, 모든 탭에 영향) ──────────────
    doom = _render_doom_radar()

    st.divider()

    holdings = st.session_state.get("holdings") or {}
    port_tickers = [t for t in holdings if t != 'CASH']

    tab1, tab2, tab3, tab4 = st.tabs([
        "🔗 페어 트레이딩", "📐 평균 회귀", "🚀 모멘텀 돌파", "🌡️ 시장 국면"
    ])

    with tab1:
        _render_pairs_tab(port_tickers, doom)
    with tab2:
        _render_mean_reversion_tab(port_tickers, doom)
    with tab3:
        _render_momentum_tab(port_tickers, doom)
    with tab4:
        _render_regime_tab(port_tickers)


# ── 매크로 저승사자 레이더 ──────────────────────────────────────────────────────
def _render_doom_radar() -> dict:
    st.markdown("<div style='font-size:10px;color:#8b949e;font-weight:700;letter-spacing:1.5px;margin-bottom:8px;'>🌪️ 매크로 저승사자 레이더</div>", unsafe_allow_html=True)

    if "_doom_macro" not in st.session_state:
        with st.spinner("장단기 금리차 · 하이일드 스프레드 로딩 중..."):
            st.session_state["_doom_macro"] = fetch_macro_doom_indicators()

    col_btn, col_info = st.columns([1, 4])
    with col_btn:
        if st.button("🔄 레이더 갱신", key="doom_refresh", use_container_width=True):
            st.cache_data.clear()
            st.session_state["_doom_macro"] = fetch_macro_doom_indicators()
            st.rerun()

    macro = st.session_state["_doom_macro"]
    doom = evaluate_doom_radar(macro["rate_spread"], macro["hy_spread"])

    severity_color = {"평시": "#22c55e", "주의": "#f59e0b", "경보": "#ef4444"}[doom["severity"]]
    severity_icon = {"평시": "🟢", "주의": "🟡", "경보": "🚨"}[doom["severity"]]

    with col_info:
        st.caption(f"데이터 출처: {macro['source']}")

    c1, c2, c3 = st.columns(3)
    with c1:
        rs_color = "#ef4444" if doom["rate_inverted"] else "#22c55e"
        st.metric("10Y-2Y 금리차", f"{macro['rate_spread']:+.2f}%p",
                  delta="역전" if doom["rate_inverted"] else "정상",
                  delta_color="inverse" if doom["rate_inverted"] else "normal")
    with c2:
        st.metric("하이일드 스프레드", f"{macro['hy_spread']:.1f}%",
                  delta="위험" if doom["hy_elevated"] else "안정",
                  delta_color="inverse" if doom["hy_elevated"] else "normal")
    with c3:
        st.markdown(f"""
        <div style="background:#0d1117;border:1px solid #30363d;border-left:3px solid {severity_color};
                     padding:8px 12px;border-radius:2px;">
          <span style="color:#8b949e;font-size:10px;">레이더 상태</span><br>
          <span style="color:{severity_color};font-size:18px;font-weight:800;">{severity_icon} {doom['severity']}</span>
        </div>
        """, unsafe_allow_html=True)

    st.markdown(f"""
    <div style="background:#0d1117;border:1px solid #30363d;border-left:3px solid {severity_color};
                 padding:12px 14px;border-radius:2px;margin-top:8px;">
      <div style="font-size:12px;color:#c9d1d9;line-height:1.7;">{doom['comment']}</div>
    </div>
    """, unsafe_allow_html=True)

    if doom["is_doom"]:
        st.error("🚫 경보 활성화 — 아래 모든 탭에서 매수성 신호(BUY/BREAKOUT/페어 롱)가 자동 차단됩니다.")

    with st.expander("📊 금리차 · 스프레드 시계열 보기"):
        fig = plot_doom_radar(macro, doom)
        st.plotly_chart(_style(fig), use_container_width=True)

    return doom


# ── 탭 1: 페어 트레이딩 ────────────────────────────────────────────────────────
def _render_pairs_tab(port_tickers: list[str], doom: dict):
    st.markdown(
        "<div style='font-size:11px;color:#8b949e;line-height:1.7;margin-bottom:10px;'>"
        "두 종목의 가격 격차(스프레드)가 평소 범위를 벗어나면, 결국 좁혀질 것을 노리고 "
        "<b style='color:#22c55e;'>롱-숏</b> 진입 신호를 생성합니다. "
        "🧲 AI 자석 매칭이 최적 단짝을 자동으로 찾아드립니다."
        "</div>", unsafe_allow_html=True)

    # ── 🧲 AI 자석 매칭 섹션 ────────────────────────────────────────────────
    st.markdown("<div style='font-size:10px;color:#8b949e;font-weight:700;letter-spacing:1.5px;margin-bottom:8px;'>🧲 AI 자석 매칭</div>", unsafe_allow_html=True)

    mag_col1, mag_col2, mag_col3 = st.columns([2, 1, 2])
    with mag_col1:
        options_a_all = port_tickers if port_tickers else []
        ticker_a_input = st.selectbox(
            "종목 A 선택",
            options_a_all + ["직접 입력"],
            key="magnet_a_sel",
        )
        if ticker_a_input == "직접 입력":
            ticker_a_input = st.text_input("종목 A 티커", placeholder="ALAB", key="magnet_a_text").upper().strip()

    with mag_col2:
        st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
        run_magnet = st.button("🧲 최적 페어 탐색", use_container_width=True, key="magnet_run")

    # 추가 유니버스: 보유종목 + 대형주
    UNIVERSE_EXTRA = [
        "MSFT", "NVDA", "AAPL", "AVGO", "GOOGL", "META", "AMZN", "TSM",
        "JPM", "V", "MA", "BAC", "GS", "XOM", "CVX", "OXY", "LLY",
        "UNH", "MRK", "PFE", "HD", "WMT", "COST",
        "AMD", "INTC", "QCOM", "MU", "ANET", "VRT", "PLTR", "CRWD",
    ]

    if run_magnet and ticker_a_input:
        candidates = list(set(port_tickers + UNIVERSE_EXTRA))
        candidates = [t for t in candidates if t != ticker_a_input]
        with st.spinner(f"{ticker_a_input} 기준 최적 단짝 탐색 중 ({len(candidates)}개 후보)..."):
            matches = find_best_pair(ticker_a_input, candidates, period="1y", top_n=5)
        st.session_state["_magnet_result"] = {"source": ticker_a_input, "matches": matches}

    magnet_result = st.session_state.get("_magnet_result")
    auto_pair_ticker = None

    if magnet_result and magnet_result.get("source") == ticker_a_input:
        matches = magnet_result["matches"]
        if matches:
            auto_pair_ticker = matches[0]["ticker"]
            grade_colors = {"S": "#22c55e", "A": "#58a6ff", "B": "#f59e0b", "C": "#8b949e"}
            chips_html = ""
            for m in matches:
                gc = grade_colors.get(m["grade"], "#8b949e")
                chips_html += (
                    f'<span style="display:inline-block;background:#0d1117;border:1px solid {gc};'
                    f'border-radius:4px;padding:3px 8px;margin-right:6px;margin-bottom:4px;">'
                    f'<b style="color:{gc};">[{m["grade"]}]</b> '
                    f'<span style="color:#e0e0e0;font-weight:700;">{m["ticker"]}</span> '
                    f'<span style="color:#8b949e;font-size:10px;">corr={m["correlation"]:+.2f}</span>'
                    f'</span>'
                )
            st.markdown(
                f'<div style="background:#0d1117;border:1px solid #30363d;border-left:3px solid #22c55e;'
                f'padding:10px 14px;border-radius:4px;margin-bottom:10px;">'
                f'<div style="font-size:10px;color:#8b949e;margin-bottom:6px;">🧲 {ticker_a_input}의 최적 단짝 후보</div>'
                f'{chips_html}'
                f'</div>',
                unsafe_allow_html=True,
            )

    st.divider()

    # ── 종목 선택 (AI 추천 기반 디폴트 + 수동 오픈) ─────────────────────────
    col1, col2 = st.columns(2)
    with col1:
        options_a = port_tickers if port_tickers else ["직접 입력"]
        default_a_idx = options_a.index(ticker_a_input) if ticker_a_input in options_a else 0
        ticker_a = st.selectbox("종목 A", options_a + (["직접 입력"] if port_tickers else []),
                                index=default_a_idx, key="pair_a")
        if ticker_a == "직접 입력":
            ticker_a = st.text_input("종목 A 티커", value=ticker_a_input or "KO", key="pair_a_input").upper().strip()
    with col2:
        options_b_all = port_tickers + UNIVERSE_EXTRA
        # AI 추천 종목을 리스트 맨 앞에 삽입
        if auto_pair_ticker and auto_pair_ticker not in options_b_all:
            options_b_all = [auto_pair_ticker] + options_b_all
        elif auto_pair_ticker and auto_pair_ticker in options_b_all:
            options_b_all = [auto_pair_ticker] + [t for t in options_b_all if t != auto_pair_ticker]
        options_b_all = list(dict.fromkeys(options_b_all))  # 중복 제거
        options_b_full = options_b_all + ["직접 입력"]
        ticker_b = st.selectbox("종목 B (AI 추천 우선 배치)", options_b_full, key="pair_b")
        if ticker_b == "직접 입력":
            ticker_b = st.text_input("종목 B 티커", value="PEP", key="pair_b_input").upper().strip()

    col3, col4, col5, col6 = st.columns(4)
    with col3:
        lookback = st.slider("Z-score 윈도우 (일)", 20, 120, 60, 5, key="pair_lookback")
    with col4:
        entry_z = st.slider("진입 임계값 |Z|", 1.0, 3.5, 2.0, 0.1, key="pair_entry")
    with col5:
        exit_z = st.slider("청산 임계값 |Z|", 0.0, 1.5, 0.5, 0.1, key="pair_exit")
    with col6:
        min_corr = st.slider("최소 상관계수 (페어 유효성)", 0.3, 0.9, 0.70, 0.05, key="pair_mincorr")

    if not ticker_a or not ticker_b or ticker_a == ticker_b:
        st.info("서로 다른 두 종목을 선택하세요.")
        return

    if st.button("▶ 페어 분석 실행", type="primary", use_container_width=True, key="pair_run"):
        with st.spinner("데이터 로딩 및 분석 중..."):
            data = _load_history((ticker_a, ticker_b))
            try:
                close = data['Close']
            except KeyError:
                st.error("데이터를 불러올 수 없습니다.")
                return

            if ticker_a not in close.columns or ticker_b not in close.columns:
                st.error("티커를 찾을 수 없습니다.")
                return

            price_a = close[ticker_a].dropna()
            price_b = close[ticker_b].dropna()
            common_idx = price_a.index.intersection(price_b.index)
            price_a, price_b = price_a.loc[common_idx], price_b.loc[common_idx]

            result = pairs_trading_signal(price_a, price_b, lookback, entry_z, exit_z, min_corr)

        st.markdown("---")

        c1, c2, c3 = st.columns(3)
        with c1:
            corr_color = "#22c55e" if result["is_valid_pair"] else "#ef4444"
            st.markdown(f"""
            <div style="background:#0d1117;border:1px solid #30363d;border-left:3px solid {corr_color};
                         padding:8px 12px;border-radius:2px;">
              <span style="color:#8b949e;font-size:10px;">상관계수</span><br>
              <span style="color:{corr_color};font-size:18px;font-weight:700;">{result['correlation']:.3f}</span>
            </div>
            """, unsafe_allow_html=True)
        with c2:
            beta_label = f"{result['beta']:.3f}" if result["is_valid_pair"] else "N/A"
            st.metric("헤지비율 (β)", beta_label)
        with c3:
            st.metric("현재 Z-score", f"{result['current_z']:+.2f}" if result["is_valid_pair"] else "N/A")

        # ── 낮은 상관계수 경고 (분석은 계속 진행) ────────────────────────────
        if not result["is_valid_pair"] and result.get("lock_message"):
            st.warning(result["lock_message"])

        # ── 저승사자 필터 적용 ────────────────────────────────────────────────
        raw_sig = result["current_signal"]
        filtered_sig = apply_doom_filter(raw_sig, doom)
        sig = _clean(filtered_sig)

        is_doom_blocked = filtered_sig != raw_sig
        if is_doom_blocked:
            sig_color = "#ef4444"
        else:
            sig_color = "#22c55e" if "LONG_A" in str(sig) else ("#ef4444" if "LONG_B" in str(sig) else "#8b949e")

        sig_label = {
            "LONG_A_SHORT_B": f"🟢 롱 {ticker_a} / 숏 {ticker_b}",
            "LONG_B_SHORT_A": f"🔴 롱 {ticker_b} / 숏 {ticker_a}",
            "EXIT": "⚪ 포지션 청산",
        }.get(sig, sig)

        st.markdown(f"""
        <div style="background:#0d1117;border:1px solid #30363d;border-left:3px solid {sig_color};
                     padding:14px;border-radius:2px;margin-top:8px;">
          <div style="font-size:14px;color:#e0e0e0;font-weight:600;">현재 신호: {sig_label}</div>
          {'<div style="font-size:11px;color:#ef4444;margin-top:4px;">⚠️ 저승사자 레이더 경보로 원래 신호가 차단되었습니다.</div>' if is_doom_blocked else ''}
        </div>
        """, unsafe_allow_html=True)

        fig = plot_pairs_trading(price_a, price_b, result, ticker_a, ticker_b)
        st.plotly_chart(_style(fig), use_container_width=True)


# ── 탭 2: 평균 회귀 ────────────────────────────────────────────────────────────
def _render_mean_reversion_tab(port_tickers: list[str], doom: dict):
    st.markdown(
        "<div style='font-size:11px;color:#8b949e;line-height:1.7;margin-bottom:10px;'>"
        "주가가 통계적 정상범주(볼린저 밴드)를 벗어나 과하게 움직이면, "
        "<b style='color:#22c55e;'>중앙선(이동평균)으로 돌아올 것</b>을 노리고 역추세 진입합니다."
        "</div>", unsafe_allow_html=True)

    col1, col2 = st.columns([2, 1])
    with col1:
        options = port_tickers + ["직접 입력"] if port_tickers else ["직접 입력"]
        ticker = st.selectbox("티커", options, key="mr_ticker")
        if ticker == "직접 입력":
            ticker = st.text_input("티커 입력", placeholder="예: AAPL", key="mr_input").upper().strip()
    with col2:
        window = st.slider("이동평균 윈도우", 10, 60, 20, 5, key="mr_window")

    n_std = st.slider("밴드 폭 (표준편차 배수)", 1.0, 3.0, 2.0, 0.25, key="mr_std")

    if not ticker:
        st.info("티커를 입력하거나 선택하세요.")
        return

    if st.button("▶ 평균회귀 분석 실행", type="primary", use_container_width=True, key="mr_run"):
        with st.spinner("데이터 로딩 중..."):
            data = _load_history((ticker,))
            try:
                price = data['Close'][ticker].dropna() if isinstance(data.columns, pd.MultiIndex) \
                        else data['Close'].dropna()
            except (KeyError, TypeError):
                st.error("데이터를 불러올 수 없습니다.")
                return

            result = mean_reversion_signal(price, window, n_std)

        st.markdown("---")

        raw_sig = result["current_signal"]
        filtered_sig = apply_doom_filter(raw_sig, doom)
        sig = _clean(filtered_sig)
        is_doom_blocked = filtered_sig != raw_sig

        if is_doom_blocked:
            sig_color = "#ef4444"
        else:
            sig_color = "#22c55e" if sig == "BUY" else ("#ef4444" if sig == "SELL" else "#8b949e")
        sig_label = {"BUY": "🟢 매수 신호 (과매도)", "SELL": "🔴 매도 신호 (과매수)"}.get(sig, sig)

        c1, c2 = st.columns(2)
        with c1:
            st.metric("현재 Z-score", f"{result['current_z']:+.2f}")
        with c2:
            st.markdown(f"""
            <div style="background:#0d1117;border:1px solid #30363d;border-left:3px solid {sig_color};
                         padding:10px 14px;border-radius:2px;">
              <span style="color:#8b949e;font-size:11px;">현재 신호</span><br>
              <span style="color:{sig_color};font-size:15px;font-weight:700;">{sig_label}</span>
            </div>
            """, unsafe_allow_html=True)

        if is_doom_blocked:
            st.caption("⚠️ 저승사자 레이더 경보로 원래 매수 신호가 차단되었습니다.")

        fig = plot_mean_reversion(price, result, ticker)
        st.plotly_chart(_style(fig), use_container_width=True)


# ── 탭 3: 모멘텀 돌파 ──────────────────────────────────────────────────────────
def _render_momentum_tab(port_tickers: list[str], doom: dict):
    st.markdown(
        "<div style='font-size:11px;color:#8b949e;line-height:1.7;margin-bottom:10px;'>"
        "강한 <b style='color:#f59e0b;'>거래량</b>과 함께 오랜 저항선을 뚫고 솟구치면, "
        "<b style='color:#22c55e;'>추세 시작</b>으로 보고 추격 매수 신호를 띄웁니다."
        "</div>", unsafe_allow_html=True)

    col1, col2 = st.columns([2, 1])
    with col1:
        options = port_tickers + ["직접 입력"] if port_tickers else ["직접 입력"]
        ticker = st.selectbox("티커", options, key="mb_ticker")
        if ticker == "직접 입력":
            ticker = st.text_input("티커 입력", placeholder="예: NVDA", key="mb_input").upper().strip()
    with col2:
        lookback = st.slider("저항선 기간 (일)", 20, 120, 55, 5, key="mb_lookback")

    volume_mult = st.slider("거래량 급증 배수", 1.0, 3.0, 1.5, 0.1, key="mb_volmult")

    if not ticker:
        st.info("티커를 입력하거나 선택하세요.")
        return

    if st.button("▶ 돌파 분석 실행", type="primary", use_container_width=True, key="mb_run"):
        with st.spinner("데이터 로딩 중..."):
            data = _load_history((ticker,))
            try:
                if isinstance(data.columns, pd.MultiIndex):
                    price = data['Close'][ticker].dropna()
                    volume = data['Volume'][ticker].dropna()
                else:
                    price = data['Close'].dropna()
                    volume = data['Volume'].dropna()
            except (KeyError, TypeError):
                st.error("데이터를 불러올 수 없습니다.")
                return

            common_idx = price.index.intersection(volume.index)
            price, volume = price.loc[common_idx], volume.loc[common_idx]

            result = momentum_breakout_signal(price, volume, lookback, volume_mult)

        st.markdown("---")

        raw_is_breakout = result["is_breakout_today"]
        doom_blocks_today = raw_is_breakout and doom["is_doom"]
        effective_is_breakout = raw_is_breakout and not doom["is_doom"]

        c1, c2 = st.columns(2)
        with c1:
            n_signals = (result["signals"] == "BREAKOUT").sum()
            st.metric(f"최근 {lookback*4}일 돌파 신호 횟수", int(n_signals))
        with c2:
            if doom_blocks_today:
                color, label = "#ef4444", "🚫 매수 금지 (대재앙 경보)"
            elif effective_is_breakout:
                color, label = "#22c55e", "🚀 오늘 돌파 발생!"
            else:
                color, label = "#8b949e", "⚪ 대기 중"
            st.markdown(f"""
            <div style="background:#0d1117;border:1px solid #30363d;border-left:3px solid {color};
                         padding:10px 14px;border-radius:2px;">
              <span style="color:#8b949e;font-size:11px;">오늘 신호</span><br>
              <span style="color:{color};font-size:15px;font-weight:700;">{label}</span>
            </div>
            """, unsafe_allow_html=True)

        if doom_blocks_today:
            st.caption("⚠️ 가격·거래량상 돌파 조건은 충족했지만, 저승사자 레이더 경보로 매수 신호가 차단되었습니다.")

        fig = plot_momentum_breakout(price, volume, result, ticker)
        st.plotly_chart(_style(fig), use_container_width=True)


# ── 탭 4: 시장 국면 감지 ────────────────────────────────────────────────────────
def _render_regime_tab(port_tickers: list[str]):
    st.markdown(
        "<div style='font-size:11px;color:#8b949e;line-height:1.7;margin-bottom:10px;'>"
        "머신러닝(K-means)이 (수익률, 변동성) 패턴을 학습해 현재 시장이 "
        "<b style='color:#22c55e;'>Bull(상승)</b> · <b style='color:#8b949e;'>Sideways(횡보)</b> · "
        "<b style='color:#ef4444;'>Bear(하락)</b> 중 어디에 속하는지 자동 판별합니다."
        "</div>", unsafe_allow_html=True)

    col1, col2 = st.columns([2, 1])
    with col1:
        options = (["전체 포트폴리오 (대표지수)"] + port_tickers + ["직접 입력"]) if port_tickers else ["직접 입력"]
        ticker = st.selectbox("대상", options, key="rg_ticker")
        if ticker == "직접 입력":
            ticker = st.text_input("티커 입력", placeholder="예: ^GSPC (S&P500)", key="rg_input").upper().strip()
        elif ticker == "전체 포트폴리오 (대표지수)":
            ticker = "^GSPC"
            st.caption("💡 포트폴리오 전체 대신 S&P500을 대표지수로 사용합니다.")
    with col2:
        period_label = st.selectbox("조회 기간", ["1y", "2y", "3y"], index=1, key="rg_period")

    if not ticker:
        st.info("티커를 입력하거나 선택하세요.")
        return

    if st.button("▶ 국면 감지 실행", type="primary", use_container_width=True, key="rg_run"):
        with st.spinner("K-means 군집화 중..."):
            data = _load_history((ticker,), period=period_label)
            try:
                price = data['Close'][ticker].dropna() if isinstance(data.columns, pd.MultiIndex) \
                        else data['Close'].dropna()
            except (KeyError, TypeError):
                st.error("데이터를 불러올 수 없습니다.")
                return

            result = detect_market_regime(price, n_regimes=3)

        st.session_state["_detected_regime"] = result["current_regime"]  # 옵티마이저 탭과 연동

        st.markdown("---")

        current = result["current_regime"]
        regime_colors = {"Bull": "#22c55e", "Bear": "#ef4444", "Sideways": "#8b949e"}
        regime_icons = {"Bull": "📈", "Bear": "📉", "Sideways": "➡️"}
        color = regime_colors.get(current, "#3b82f6")
        icon = regime_icons.get(current, "❔")

        st.markdown(f"""
        <div style="background:#0d1117;border:1px solid #30363d;border-left:3px solid {color};
                     padding:16px;border-radius:2px;margin-bottom:12px;">
          <span style="color:#8b949e;font-size:11px;">현재 시장 국면</span><br>
          <span style="color:{color};font-size:22px;font-weight:800;">{icon} {current}</span>
        </div>
        """, unsafe_allow_html=True)

        if result["regime_raw"] is not None:
            dist = result["regime_labels"].value_counts()
            c1, c2, c3 = st.columns(3)
            for col, name in zip([c1, c2, c3], ["Bull", "Sideways", "Bear"]):
                with col:
                    pct = dist.get(name, 0) / len(result["regime_labels"]) * 100
                    st.metric(f"{name} 비중", f"{pct:.1f}%")

        fig1 = plot_regime_chart(price, result, ticker)
        st.plotly_chart(_style(fig1), use_container_width=True)

        if result["features"] is not None:
            fig2 = plot_regime_scatter(result)
            st.plotly_chart(_style(fig2), use_container_width=True)

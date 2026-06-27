"""
auto_trading.py
───────────────
LENS Auto Trading 현황 대시보드
- 30초 자동 새로고침
- 포트폴리오 수익곡선 멀티타임프레임 (1분/10분/1H/1D/1M/6M/1Y)
"""

import json
from datetime import datetime
from pathlib import Path

import streamlit as st
import pandas as pd
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

_LT_ENV       = Path(r"C:\Users\a6225\OneDrive\바탕 화면\lens_trader\lens_trader\.env")
_SIGNALS_PATH = Path(r"C:\Users\a6225\OneDrive\바탕 화면\lens_trader\lens_trader\signals.json")
_TRADE_LOG    = Path(r"C:\Users\a6225\OneDrive\바탕 화면\lens_trader\lens_trader\trade_log.json")
_REPORT_LOG   = Path(r"C:\Users\a6225\OneDrive\바탕 화면\lens_trader\lens_trader\report_log.json")

# 타임프레임 설정: (표시명, period, timeframe_str)
_TIMEFRAMES = [
    ("1분",  "1D",  "1Min"),
    ("10분", "1D",  "15Min"),   # Alpaca: 15Min이 최소단위
    ("1시간","1W",  "1H"),
    ("1일",  "1M",  "1D"),
    ("1달",  "3M",  "1D"),
    ("6개월","6M",  "1D"),
    ("1년",  "1A",  "1D"),
]


def _load_alpaca_env():
    from dotenv import dotenv_values
    return dotenv_values(_LT_ENV) if _LT_ENV.exists() else {}


@st.cache_resource
def _get_trading_client():
    env = _load_alpaca_env()
    from alpaca.trading.client import TradingClient
    return TradingClient(
        api_key=env.get("ALPACA_API_KEY", ""),
        secret_key=env.get("ALPACA_SECRET_KEY", ""),
        paper=env.get("ALPACA_PAPER", "true").lower() == "true",
    )


def _fetch_history(client, period: str, timeframe: str):
    from alpaca.trading.requests import GetPortfolioHistoryRequest
    req  = GetPortfolioHistoryRequest(period=period, timeframe=timeframe)
    hist = client.get_portfolio_history(req)
    ts   = [datetime.fromtimestamp(t) for t in (hist.timestamp or [])]
    eq   = [e for e in (hist.equity or [])]
    return ts, eq


def _portfolio_chart(client, acct):
    import plotly.graph_objects as go

    st.markdown("#### 포트폴리오 수익 곡선")

    tf_labels = [t[0] for t in _TIMEFRAMES]
    selected  = st.radio(
        "", tf_labels, index=3,
        horizontal=True, label_visibility="collapsed",
        key="tf_radio",
    )
    idx = tf_labels.index(selected)
    _, period, tf_str = _TIMEFRAMES[idx]

    try:
        ts, eq = _fetch_history(client, period, tf_str)
        if not ts:
            ts = [datetime.now()]
            eq = [float(acct.equity)]
    except Exception as e:
        st.caption(f"차트 로드 실패: {e}")
        return

    # x축 형식
    xfmt = "%H:%M" if selected in ("1분", "10분", "1시간") else (
           "%m/%d" if selected in ("1일", "1달") else "%Y/%m")

    valid_eq   = [e for e in eq if e is not None and e > 0]
    start_eq   = valid_eq[0]  if valid_eq else float(acct.equity)
    end_eq     = valid_eq[-1] if valid_eq else float(acct.equity)
    line_color = "#22c55e" if end_eq >= start_eq else "#ef4444"
    fill_rgba  = "34,197,94" if line_color == "#22c55e" else "239,68,68"

    # y축 범위: 데이터 실제 변동 ±0.5% 여유만 줌 (0부터 시작 X)
    y_min = min(valid_eq) * 0.9995 if valid_eq else start_eq * 0.99
    y_max = max(valid_eq) * 1.0005 if valid_eq else start_eq * 1.01

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=ts, y=eq,
        mode="lines", name="Equity",
        line=dict(color=line_color, width=2),
        fill="tonexty",
        fillcolor=f"rgba({fill_rgba},0.10)",
        hovertemplate="$%{y:,.2f}<extra></extra>",
    ))
    # 기준선 (시작 equity)
    fig.add_hline(
        y=start_eq,
        line_dash="dot", line_color="rgba(255,255,255,0.15)", line_width=1,
    )
    fig.update_layout(
        height=260,
        margin=dict(l=0, r=0, t=6, b=0),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#0d1117",
        xaxis=dict(showgrid=False, color="#8b949e", tickformat=xfmt),
        yaxis=dict(
            showgrid=True, gridcolor="#21262d", color="#8b949e",
            tickprefix="$", tickformat=",.0f",
            range=[y_min, y_max],
        ),
        hovermode="x unified",
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True)


@st.fragment(run_every=30)
def _dashboard():
    try:
        client = _get_trading_client()
    except Exception as e:
        st.error(f"Alpaca 연결 실패: {e}")
        return

    _ts = datetime.now().strftime("%H:%M:%S")
    st.caption(f"🔁 30초 자동갱신 · {_ts}")

    # ── 계좌 요약 ────────────────────────────────────────────────────────
    st.markdown("#### 계좌 현황")
    try:
        acct  = client.get_account()
        clock = client.get_clock()

        status_html = (
            '<span style="color:#22c55e;font-weight:700;">● 개장 중</span>'
            if clock.is_open else
            '<span style="color:#ef4444;font-weight:700;">● 장 마감</span>'
        )
        st.markdown(f"<div style='font-size:12px;margin-bottom:8px;'>{status_html}</div>",
                    unsafe_allow_html=True)

        pv         = float(acct.portfolio_value)
        equity     = float(acct.equity)
        last_eq    = float(acct.last_equity)
        bp         = float(acct.buying_power)
        daily_pnl  = equity - last_eq
        daily_pct  = (daily_pnl / last_eq * 100) if last_eq else 0

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("포트폴리오",      f"${pv:,.0f}")
        c2.metric("Equity",          f"${equity:,.0f}")
        c3.metric("오늘 손익",       f"${daily_pnl:+,.2f}", f"{daily_pct:+.2f}%",
                  delta_color="normal" if daily_pnl >= 0 else "inverse")
        c4.metric("매수여력 (4x)",   f"${bp:,.0f}")
    except Exception as e:
        st.error(f"계좌 조회 실패: {e}")
        return

    # ── 포트폴리오 차트 (멀티 타임프레임) ──────────────────────────────
    _portfolio_chart(client, acct)

    st.divider()

    # ── 보유 포지션 ──────────────────────────────────────────────────────
    st.markdown("#### 보유 포지션")
    try:
        positions = client.get_all_positions()
        if not positions:
            st.caption("현재 보유 포지션 없음")
        else:
            rows = []
            for p in positions:
                qty       = float(p.qty)
                side_tag  = "🔴 숏" if qty < 0 else "🟢 롱"
                pnl       = float(p.unrealized_pl)
                pnl_pct   = float(p.unrealized_plpc) * 100
                rows.append({
                    "종목":      p.symbol,
                    "방향":      side_tag,
                    "수량":      int(abs(qty)),
                    "평균단가":  f"${float(p.avg_entry_price):.2f}",
                    "현재가":    f"${float(p.current_price):.2f}",
                    "평가금액":  f"${float(p.market_value):,.0f}",
                    "미실현손익": f"${pnl:+,.2f}",
                    "수익률":    f"{pnl_pct:+.2f}%",
                })
            df = pd.DataFrame(rows)

            def _color_pnl(val):
                color = "#22c55e" if "+" in str(val) else "#ef4444"
                return f"color: {color}"

            st.dataframe(
                df.style.applymap(_color_pnl, subset=["미실현손익", "수익률"]),
                use_container_width=True, hide_index=True,
            )
    except Exception as e:
        st.error(f"포지션 조회 실패: {e}")

    st.divider()

    # ── 전체 주문 내역 ───────────────────────────────────────────────────
    st.markdown("#### 전체 주문 내역")
    try:
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus
        orders = client.get_orders(GetOrdersRequest(status=QueryOrderStatus.ALL, limit=500))
        if not orders:
            st.caption("주문 내역 없음")
        else:
            rows = []
            for o in orders:
                ts = o.filled_at.strftime("%m/%d %H:%M") if o.filled_at else "-"
                rows.append({
                    "시각":   ts,
                    "종목":   o.symbol,
                    "방향":   o.side.value.upper(),
                    "타입":   o.type.value,
                    "수량":   o.qty,
                    "체결가": f"${float(o.filled_avg_price):.2f}" if o.filled_avg_price else "-",
                    "상태":   o.status.value,
                })
            df = pd.DataFrame(rows)

            def _color_side(val):
                return "color: #22c55e" if val == "BUY" else "color: #ef4444"

            st.dataframe(
                df.style.applymap(_color_side, subset=["방향"]),
                use_container_width=True, hide_index=True,
            )
    except Exception as e:
        st.error(f"주문 조회 실패: {e}")

    st.divider()

    # ── 거래 기록 ────────────────────────────────────────────────────────
    st.markdown("#### 거래 기록 (이유 포함)")
    if _TRADE_LOG.exists():
        try:
            logs = list(reversed(json.loads(_TRADE_LOG.read_text(encoding="utf-8"))))
            if not logs:
                st.caption("거래 기록 없음")
            else:
                for entry in logs:
                    action = entry.get("action", "")
                    color  = "#22c55e" if action == "BUY" else "#ef4444"
                    ticker = entry.get("ticker", "")
                    ts     = entry.get("timestamp", "")
                    reason = entry.get("reason", "")
                    price  = entry.get("price", 0)
                    qty    = entry.get("qty", 0)
                    sl     = entry.get("stop_loss", 0)
                    sl_txt = f" · SL ${sl:.2f}" if sl else ""
                    st.markdown(
                        f'<div style="border-left:3px solid {color};padding:8px 12px;'
                        f'margin-bottom:6px;background:#0d1117;border-radius:2px;">'
                        f'<span style="color:{color};font-weight:700;">{action}</span> '
                        f'<span style="color:#e0e0e0;font-weight:700;">{ticker}</span> '
                        f'<span style="color:#8b949e;font-size:11px;">{qty}주 @ ${price:.2f}{sl_txt} · {ts}</span><br>'
                        f'<span style="color:#8b949e;font-size:11px;">{reason}</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
        except Exception as e:
            st.caption(f"로그 읽기 실패: {e}")
    else:
        st.caption("아직 거래 없음")

    st.divider()

    # ── 현재 신호 ────────────────────────────────────────────────────────
    st.markdown("#### 현재 신호 (signals.json)")
    if _SIGNALS_PATH.exists():
        try:
            data        = json.loads(_SIGNALS_PATH.read_text(encoding="utf-8"))
            ts_sig      = data.get("timestamp", "?")
            macro_block = data.get("macro_block", False)
            signals     = data.get("signals", [])

            col_ts, col_doom = st.columns([3, 1])
            col_ts.caption(f"마지막 갱신: {ts_sig}")
            if macro_block:
                col_doom.markdown('<span style="color:#ef4444;font-weight:700;">🚨 DOOM RADAR — BUY 차단</span>',
                                  unsafe_allow_html=True)
            else:
                col_doom.markdown('<span style="color:#22c55e;font-weight:700;">🟢 거시경제 정상</span>',
                                  unsafe_allow_html=True)

            if signals:
                rows = []
                for s in signals:
                    rows.append({
                        "종목":   s.get("ticker", ""),
                        "방향":   s.get("action", ""),
                        "전략":   s.get("strategy", ""),
                        "강도":   f"{s.get('strength', 0):.0%}",
                        "근거":   s.get("reason", "-"),
                        "참고가": f"${s.get('price', 0):.2f}",
                    })
                df = pd.DataFrame(rows)

                def _color_action(val):
                    return "color: #22c55e" if val == "BUY" else "color: #ef4444"

                st.dataframe(
                    df.style.applymap(_color_action, subset=["방향"]),
                    use_container_width=True, hide_index=True,
                )
            else:
                st.caption("신호 없음")
        except Exception as e:
            st.error(f"signals.json 읽기 실패: {e}")
    else:
        st.caption("signals.json 없음 — lens_trader 실행 후 스캔 대기 중")

    st.divider()

    # ── 일일 레포트 히스토리 ──────────────────────────────────────────────
    st.markdown("#### 📊 일일 손익 레포트 (매일 09:00 ET 자동 생성)")
    if _REPORT_LOG.exists():
        try:
            reports = list(reversed(json.loads(_REPORT_LOG.read_text(encoding="utf-8"))))
            if not reports:
                st.caption("아직 레포트 없음")
            else:
                for rpt in reports[:5]:  # 최근 5개
                    date     = rpt.get("date", "?")
                    real_pnl = rpt.get("total_realized", 0)
                    unreal   = rpt.get("total_unrealized", 0)
                    cnt      = rpt.get("trade_count", 0)
                    pnl_color = "#22c55e" if real_pnl >= 0 else "#ef4444"

                    with st.expander(
                        f"📅 {date}  |  실현손익 "
                        f"{'▲' if real_pnl >= 0 else '▼'} ${abs(real_pnl):,.2f}  |  "
                        f"거래 {cnt}건"
                    ):
                        c1, c2 = st.columns(2)
                        c1.metric("실현 손익", f"${real_pnl:+,.2f}",
                                  delta_color="normal" if real_pnl >= 0 else "inverse")
                        c2.metric("미실현 손익 (당시)", f"${unreal:+,.2f}",
                                  delta_color="normal" if unreal >= 0 else "inverse")

                        winners = rpt.get("winners", [])
                        losers  = rpt.get("losers", [])

                        if winners:
                            st.markdown("**🏆 수익 상위**")
                            for w in winners:
                                reason_short = w.get("reason", "").replace("[매수 근거] ", "").split("|")[0].strip()
                                st.markdown(
                                    f'<div style="border-left:3px solid #22c55e;padding:4px 10px;'
                                    f'margin-bottom:4px;background:#0d1117;border-radius:2px;">'
                                    f'✅ <b style="color:#22c55e">{w["ticker"]}</b> '
                                    f'<span style="color:#e0e0e0">${w["pnl"]:+,.2f}</span> '
                                    f'<span style="color:#8b949e;font-size:11px">← {reason_short}</span>'
                                    f'</div>',
                                    unsafe_allow_html=True,
                                )

                        if losers:
                            st.markdown("**💥 손실 하위**")
                            for l in losers:
                                reason_short = l.get("reason", "").replace("[매수 근거] ", "").split("|")[0].strip()
                                st.markdown(
                                    f'<div style="border-left:3px solid #ef4444;padding:4px 10px;'
                                    f'margin-bottom:4px;background:#0d1117;border-radius:2px;">'
                                    f'❌ <b style="color:#ef4444">{l["ticker"]}</b> '
                                    f'<span style="color:#e0e0e0">${l["pnl"]:+,.2f}</span> '
                                    f'<span style="color:#8b949e;font-size:11px">← {reason_short}</span>'
                                    f'</div>',
                                    unsafe_allow_html=True,
                                )

                        st.markdown("**📋 매매 기준**")
                        st.code(
                            "RSI<35→롱 / RSI>65→숏\n"
                            "MACD 골든/데드크로스\n"
                            "볼린저밴드 이탈\n"
                            "거래량 급증 확인\n"
                            "손절: ±3% GTC 자동\n"
                            "포지션 크기: 신호강도 비례 (5~25%)",
                            language=None,
                        )
        except Exception as e:
            st.caption(f"레포트 로드 실패: {e}")
    else:
        st.caption("레포트 없음 — 매일 09:00 ET에 자동 생성됩니다")


def render():
    st.markdown("""
    <div style="padding:4px 0 16px;">
      <span style="font-size:18px;font-weight:800;color:#e0e0e0;">🤖 AUTO TRADING</span>
      <span style="font-size:11px;color:#8b949e;margin-left:10px;">LENS Trader · Alpaca Paper</span>
    </div>
    """, unsafe_allow_html=True)
    _dashboard()

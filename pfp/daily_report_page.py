"""
daily_report_page.py — ALPHA TERMINAL 데일리 브리프 Streamlit 페이지
"""
from __future__ import annotations

import pathlib
from datetime import datetime

import streamlit as st


BASE_DIR   = pathlib.Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)


def render():
    # ── 헤더 ────────────────────────────────────────────────────────────────
    st.markdown("""
    <div style="padding:10px 0 6px;">
      <div style="font-size:10px;color:#f97316;font-weight:700;letter-spacing:2px;">
        ALPHA TERMINAL</div>
      <div style="font-size:20px;font-weight:700;color:#e0e0e0;">
        Daily Portfolio Brief</div>
      <div style="font-size:11px;color:#8b949e;">
        전일 보유 종목 변동 + 뉴스 원인 분석 — 매일 아침 1회 실행
      </div>
    </div>
    """, unsafe_allow_html=True)

    st.divider()

    # ── 보유 종목 확인 ────────────────────────────────────────────────────────
    holdings = st.session_state.get("holdings") or {}
    stock_holdings = {t: v for t, v in holdings.items() if t != "CASH"}

    if not stock_holdings:
        st.warning("Alpha Terminal에 보유 종목이 없습니다. 먼저 포트폴리오를 입력하세요.")
        return

    # 현재 보유 종목 미리보기
    ticker_pills = " · ".join(stock_holdings.keys())
    st.markdown(
        f'<div style="background:#0d1117;border:.5px solid #30363d;border-left:3px solid #f97316;'
        f'padding:10px 16px;border-radius:4px;font-size:12px;color:#8b949e;margin-bottom:16px;">'
        f'<span style="color:#e0e0e0;font-weight:600;">분석 대상:</span> {ticker_pills} '
        f'<span style="color:#6e7681;">+ 현금 ${holdings.get("CASH", {}).get("q", 0):,.0f}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── 생성 버튼 ────────────────────────────────────────────────────────────
    col_btn, col_hist = st.columns([2, 3])

    with col_btn:
        generate = st.button(
            "📊 데일리 브리프 생성",
            type="primary",
            use_container_width=True,
            key="btn_daily_brief",
        )

    with col_hist:
        # 이전 리포트 파일 목록
        past = sorted(OUTPUT_DIR.glob("daily_brief_*.md"), reverse=True)
        if past:
            sel = st.selectbox(
                "이전 브리프 불러오기",
                ["— 새로 생성 —"] + [p.name for p in past[:10]],
                label_visibility="collapsed",
                key="sel_past_brief",
            )
            if sel != "— 새로 생성 —":
                content = (OUTPUT_DIR / sel).read_text(encoding="utf-8")
                st.session_state["_daily_brief_md"] = content
                st.session_state["_daily_brief_price"] = {}

    # ── 생성 파이프라인 ───────────────────────────────────────────────────────
    if generate:
        st.session_state.pop("_daily_brief_md", None)
        st.session_state.pop("_daily_brief_price", None)

        status_box = st.empty()
        steps: list[str] = []

        def log(msg: str):
            steps.append(msg)
            status_box.markdown(
                '<div style="background:#0d1117;border:.5px solid #30363d;padding:12px 16px;'
                'border-radius:4px;">'
                + "".join(
                    f'<div style="font-size:12px;color:{"#e0e0e0" if i==len(steps)-1 else "#6e7681"}'
                    f';padding:2px 0;">{"⟳" if i==len(steps)-1 else "✅"} {s}</div>'
                    for i, s in enumerate(steps)
                )
                + "</div>",
                unsafe_allow_html=True,
            )

        try:
            from daily_portfolio_report import generate_daily_report

            report_md, price_data = generate_daily_report(holdings, log=log)

            # 파일 저장
            fname   = f"daily_brief_{datetime.now().strftime('%Y%m%d_%H%M')}.md"
            fpath   = OUTPUT_DIR / fname
            fpath.write_text(report_md, encoding="utf-8")

            st.session_state["_daily_brief_md"]    = report_md
            st.session_state["_daily_brief_price"] = price_data

            status_box.success(f"✅ 브리프 생성 완료 — {fname}")

        except ImportError as e:
            status_box.error(f"모듈 임포트 실패: {e}")
            return
        except Exception as e:
            status_box.error(f"생성 오류: {e}")
            return

    # ── 리포트 표시 ───────────────────────────────────────────────────────────
    md = st.session_state.get("_daily_brief_md")
    price_data = st.session_state.get("_daily_brief_price", {})

    if not md:
        st.markdown(
            '<div style="text-align:center;padding:60px 0;color:#6e7681;font-size:13px;">'
            '버튼을 누르면 전일 포트폴리오 변동 분석이 시작됩니다.</div>',
            unsafe_allow_html=True,
        )
        return

    st.divider()

    # 스냅샷 메트릭 카드 (가격 데이터가 있을 때)
    if price_data:
        _render_snapshot_cards(price_data)
        st.divider()

    # 마크다운 본문
    st.markdown(
        '<div style="background:#0d1117;border:.5px solid #30363d;border-radius:8px;'
        'padding:24px 28px;line-height:1.8;">',
        unsafe_allow_html=True,
    )
    st.markdown(md)
    st.markdown("</div>", unsafe_allow_html=True)

    # ── 액션 버튼 ─────────────────────────────────────────────────────────────
    st.divider()
    a1, a2, a3 = st.columns(3)

    with a1:
        st.download_button(
            "⬇️ Markdown 저장",
            data=md.encode("utf-8"),
            file_name=f"daily_brief_{datetime.now().strftime('%Y%m%d')}.md",
            mime="text/markdown",
            use_container_width=True,
        )

    with a2:
        if st.button("📤 텔레그램 전송", use_container_width=True, key="btn_tg_brief"):
            _send_telegram(md)

    with a3:
        if st.button("🔄 재생성", use_container_width=True, key="btn_regen"):
            st.session_state.pop("_daily_brief_md", None)
            st.rerun()


def _render_snapshot_cards(price_data: dict):
    """종목별 전일 변동 카드 한 줄 요약."""
    stock_keys = [k for k in price_data if not k.startswith("__")]
    if not stock_keys:
        return

    st.markdown(
        '<div style="font-size:10px;color:#8b949e;font-weight:700;letter-spacing:1.5px;'
        'margin-bottom:8px;">YESTERDAY\'S MOVES</div>',
        unsafe_allow_html=True,
    )

    cols = st.columns(min(len(stock_keys), 5))
    for i, t in enumerate(
        sorted(stock_keys, key=lambda x: price_data[x]["chg_pct"], reverse=True)
    ):
        d = price_data[t]
        chg = d["chg_pct"]
        clr = "#39d353" if chg >= 0 else "#ff7b72"
        arrow = "▲" if chg >= 0 else "▼"
        with cols[i % len(cols)]:
            st.markdown(
                f'<div style="background:#0d1117;border:.5px solid #30363d;border-radius:6px;'
                f'padding:10px 12px;text-align:center;">'
                f'<div style="font-size:12px;font-weight:700;color:#e0e0e0;">{t}</div>'
                f'<div style="font-size:16px;font-weight:700;color:{clr};margin:2px 0;">'
                f'{arrow} {abs(chg):.2f}%</div>'
                f'<div style="font-size:10px;color:#6e7681;">${d["close"]:,.2f}</div>'
                f'<div style="font-size:10px;color:{clr};">'
                f'{"+" if d["day_pnl"]>=0 else ""}${d["day_pnl"]:,.0f}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )


def _send_telegram(md: str):
    """텔레그램으로 브리프 발송."""
    import os
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env", override=True)
    token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        st.error("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID가 .env에 없습니다.")
        return

    import requests
    # Telegram 메시지 길이 제한 4096자 — 초과 시 분할
    chunks = [md[i : i + 4000] for i in range(0, len(md), 4000)]
    ok = True
    for chunk in chunks:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": chunk, "parse_mode": "Markdown"},
            timeout=15,
        )
        if not r.ok:
            ok = False
    if ok:
        st.success("텔레그램 전송 완료!")
    else:
        st.error("텔레그램 전송 실패 — 봇 토큰/Chat ID를 확인하세요.")

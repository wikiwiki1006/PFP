"""pages/home.py — 홈 대시보드"""
import streamlit as st
from datetime import datetime
import pytz


def render():
    ny = datetime.now(pytz.timezone("America/New_York"))
    kr = datetime.now(pytz.timezone("Asia/Seoul"))

    st.markdown(f"""
    <div style="padding:20px 0 10px;">
        <div style="font-size:10px;color:#58a6ff;font-weight:700;letter-spacing:2px;">PERSONAL FINANCIAL PLATFORM</div>
        <div style="font-size:26px;font-weight:700;color:#00e6ff;margin:4px 0;">PERSONAL FINANCIAL PLATFORM</div>
        <div style="font-size:12px;color:#8b949e;">
            🇺🇸 NY {ny.strftime('%H:%M')} EST &nbsp;|&nbsp; 🇰🇷 KR {kr.strftime('%H:%M')} KST
            &nbsp;<span class="live-dot"></span>
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.divider()

    c1, c2, c3 = st.columns(3)

    with c1:
        st.markdown("""
        <div style="background:#0d1117;border:1px solid #30363d;border-left:3px solid #00e6ff;padding:16px;border-radius:2px;height:180px;">
          <div style="color:#8b949e;font-size:10px;font-weight:700;letter-spacing:1.5px;margin-bottom:10px;">📊 ALPHA TERMINAL</div>
          <div style="color:#e0e0e0;font-size:12px;line-height:1.8;">
            • yfinance 실시간 포트폴리오<br>
            • 에쿼티 커브 + 벤치마크<br>
            • 섹터 배분 도넛 차트<br>
            • 상관계수 히트맵<br>
            • 거래 일지 (매매 메모)
          </div>
        </div>
        """, unsafe_allow_html=True)
        if st.button("OPEN TERMINAL →", use_container_width=True, key="h_alpha"):
            st.session_state.current_page = "alpha"
            st.rerun()

    with c2:
        st.markdown("""
        <div style="background:#0d1117;border:1px solid #30363d;border-left:3px solid #9b59b6;padding:16px;border-radius:2px;height:180px;">
          <div style="color:#8b949e;font-size:10px;font-weight:700;letter-spacing:1.5px;margin-bottom:10px;">🌐 MACRO SCENARIO</div>
          <div style="color:#e0e0e0;font-size:12px;line-height:1.8;">
            • 9-에이전트 멀티 파이프라인<br>
            • 이벤트 분석 → 역사적 유사사례<br>
            • 섹터 충격 + 투자 전략<br>
            • 포트폴리오 액션 플랜 (JSON)<br>
            • Claude API 스트리밍
          </div>
        </div>
        """, unsafe_allow_html=True)
        if st.button("RUN SCENARIO →", use_container_width=True, key="h_macro"):
            st.session_state.current_page = "macro"
            st.rerun()

    with c3:
        st.markdown("""
        <div style="background:#0d1117;border:1px solid #30363d;border-left:3px solid #2e75b6;padding:16px;border-radius:2px;height:180px;">
          <div style="color:#8b949e;font-size:10px;font-weight:700;letter-spacing:1.5px;margin-bottom:10px;">📝 LENS REPORT</div>
          <div style="color:#e0e0e0;font-size:12px;line-height:1.8;">
            • 종목별 AI 리서치 레포트<br>
            • LENS_Template_v4.docx<br>
            • Claude / Gemini 집필<br>
            • DOCX 생성 + 텔레그램 전송<br>
            • 생성 이력 관리
          </div>
        </div>
        """, unsafe_allow_html=True)
        if st.button("GENERATE REPORT →", use_container_width=True, key="h_report"):
            st.session_state.current_page = "report"
            st.rerun()

    st.divider()

    # 파이프라인 현황
    st.markdown("<div style='font-size:10px;color:#8b949e;font-weight:700;letter-spacing:1.5px;margin-bottom:10px;'>PIPELINE STATUS</div>", unsafe_allow_html=True)

    p1, p2, p3 = st.columns(3)
    h_ok  = st.session_state.holdings is not None
    mc_ok = st.session_state.macro_results is not None
    rp_ok = st.session_state.last_report_path is not None

    with p1:
        st.metric("포트폴리오",
                  f"{len(st.session_state.holdings)}종목" if h_ok else "미로드",
                  delta="READY" if h_ok else None)
    with p2:
        st.metric("매크로 분석",
                  "완료" if mc_ok else "미실행",
                  delta="READY" if mc_ok else None)
    with p3:
        if rp_ok:
            from pathlib import Path
            st.metric("최근 레포트", Path(st.session_state.last_report_path).name)
        else:
            st.metric("최근 레포트", "없음")

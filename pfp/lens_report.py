"""
lens_report.py — LENS 레포트 생성기 (PDF 출력 + 최신 데이터 웹서치)
"""
import os, sys, json
from pathlib import Path
from datetime import datetime

import streamlit as st
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

OUTPUT_DIR   = BASE_DIR / "outputs"
TEMPLATE_DIR = BASE_DIR / "templates"
OUTPUT_DIR.mkdir(exist_ok=True)


def render():
    st.markdown("""
    <div style="padding:12px 0 8px;">
      <div style="font-size:10px;color:#58a6ff;font-weight:700;letter-spacing:2px;">[ L ] LENS CAPITAL RESEARCH</div>
      <div style="font-size:20px;font-weight:700;color:#2e75b6;">AI EQUITY REPORT GENERATOR</div>
      <div style="font-size:11px;color:#8b949e;">선명하게, 다르게 본다. — Insight in focus.</div>
    </div>
    """, unsafe_allow_html=True)

    has_anthropic = bool(os.getenv("ANTHROPIC_API_KEY"))
    has_gemini    = bool(os.getenv("GEMINI_API_KEY"))
    has_telegram  = bool(os.getenv("TELEGRAM_BOT_TOKEN"))

    col_s1, col_s2, col_s3 = st.columns(3)
    with col_s1:
        st.markdown(f'<div style="font-size:11px;">{"🟢" if has_anthropic else "🔴"} Anthropic API</div>', unsafe_allow_html=True)
    with col_s2:
        st.markdown(f'<div style="font-size:11px;">{"🟢" if has_gemini else "🟡"} Gemini API</div>', unsafe_allow_html=True)
    with col_s3:
        st.markdown(f'<div style="font-size:11px;">{"🟢" if has_telegram else "🟡"} Telegram Bot</div>', unsafe_allow_html=True)

    st.divider()

    tab_new, tab_industry, tab_history, tab_macro = st.tabs(["📄 새 레포트", "🏭 산업 리포트", "📁 생성 이력", "🌐 매크로 연동"])

    with tab_new:
        col_i1, col_i2 = st.columns(2)
        with col_i1:
            ticker = st.text_input("티커 심볼", placeholder="예: NVDA", max_chars=10).upper().strip()
        with col_i2:
            company = st.text_input("회사 정식명칭", placeholder="예: NVIDIA Corporation")

        # 포트폴리오 빠른 선택
        holdings = st.session_state.get("holdings") or {}
        port_tickers = [t for t in holdings if t != 'CASH']
        if port_tickers:
            st.markdown("<div style='font-size:10px;color:#8b949e;margin:4px 0 6px;'>포트폴리오에서 선택:</div>", unsafe_allow_html=True)
            p_cols = st.columns(len(port_tickers))
            for i, (col, pt) in enumerate(zip(p_cols, port_tickers)):
                with col:
                    if st.button(pt, key=f"pt_{i}", use_container_width=True):
                        st.session_state["_quick_ticker"] = pt
                        st.rerun()
            if "_quick_ticker" in st.session_state:
                ticker = st.session_state.pop("_quick_ticker")

        # 출력 형식 선택
        col_fmt, col_tg = st.columns(2)
        with col_fmt:
            output_fmt = st.radio(
                "출력 형식",
                ["PDF", "DOCX"],
                horizontal=True,
                help="PDF: LibreOffice 또는 docx2pdf 필요 / DOCX: 항상 가능"
            )
        with col_tg:
            send_tg = st.checkbox("텔레그램 자동 발송", value=has_telegram)

        if output_fmt == "PDF":
            st.caption("💡 PDF 변환은 LibreOffice 또는 `pip install docx2pdf` 필요. 변환 실패 시 DOCX로 자동 대체됩니다.")

        if st.button("📊 레포트 생성 시작", type="primary",
                     use_container_width=True, disabled=not ticker or not company):
            _run_pipeline(ticker, company, send_tg, output_fmt)

    with tab_industry:
        _render_industry_tab()

    with tab_history:
        # DOCX + PDF 모두 표시
        all_reports = sorted(
            list(OUTPUT_DIR.glob("*.docx")) + list(OUTPUT_DIR.glob("*.pdf")),
            key=lambda p: p.stat().st_mtime, reverse=True
        )
        if not all_reports:
            st.info("아직 생성된 레포트가 없습니다.")
        else:
            for fp in all_reports[:20]:
                c_name, c_dl = st.columns([4, 1])
                with c_name:
                    ext_icon = "📄" if fp.suffix == ".pdf" else "📝"
                    st.text(f"{ext_icon} {fp.name}")
                with c_dl:
                    mime = "application/pdf" if fp.suffix == ".pdf" else \
                           "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    with open(fp, "rb") as f:
                        st.download_button("⬇️", data=f.read(),
                                           file_name=fp.name, mime=mime, key=str(fp))

    with tab_macro:
        macro = st.session_state.get("macro_results")
        if not macro:
            st.info("거시경제 시나리오 페이지에서 분석을 먼저 실행하세요.")
            if st.button("→ 매크로 시나리오로 이동"):
                st.session_state.current_page = "macro"
                st.rerun()
        else:
            st.success(f"✅ 매크로 분석 결과 연동 가능: **{macro['event'][:60]}...**")
            ticker2  = st.text_input("레포트 종목 티커", key="m_ticker", placeholder="NVDA")
            company2 = st.text_input("회사명", key="m_company", placeholder="NVIDIA Corporation")
            fmt2 = st.radio("출력 형식", ["PDF", "DOCX"], horizontal=True, key="m_fmt")
            if st.button("📊 매크로 컨텍스트 포함 레포트 생성", type="primary",
                         disabled=not ticker2 or not company2):
                _run_pipeline(ticker2.upper(), company2, has_telegram, fmt2)


def _render_industry_tab():
    from industry_report_writer import INDUSTRIES

    st.markdown("""
    <div style="padding:6px 0 10px;">
      <div style="font-size:12px;color:#8b949e;">산업을 선택하면 Claude가 웹서치 + AI 집필로 산업 리서치 레포트를 PDF로 생성합니다.</div>
    </div>
    """, unsafe_allow_html=True)

    # ── 산업 선택 그리드 ──────────────────────────────────────────────────────
    industry_options = {
        kid: f"{meta['icon']} {meta['name_kr']}"
        for kid, meta in INDUSTRIES.items()
    }
    display_labels = list(industry_options.values())
    industry_ids   = list(industry_options.keys())

    selected_label = st.selectbox(
        "분석할 산업 선택",
        display_labels,
        index=0,
        help="목록에서 산업을 선택하세요. Claude가 최신 웹 데이터를 수집하여 리포트를 작성합니다.",
    )
    selected_id = industry_ids[display_labels.index(selected_label)]
    meta = INDUSTRIES[selected_id]

    # ── 선택된 산업 미리보기 카드 ─────────────────────────────────────────────
    st.markdown(f"""
    <div style="background:#0d1117;border:1px solid #30363d;border-left:4px solid #C1440D;
                padding:14px 18px;border-radius:4px;margin:10px 0;">
      <div style="font-size:11px;color:#8b949e;font-weight:700;letter-spacing:1px;margin-bottom:6px;">
        선택된 산업
      </div>
      <div style="font-size:18px;font-weight:700;color:#e0e0e0;">{meta['icon']} {meta['name_kr']}</div>
      <div style="font-size:11px;color:#8b949e;margin-top:2px;">{meta['name_en']}</div>
      <div style="font-size:12px;color:#c9d1d9;font-style:italic;margin-top:8px;">"{meta['tagline']}"</div>
      <div style="margin-top:10px;display:flex;gap:24px;flex-wrap:wrap;">
        <div>
          <span style="font-size:10px;color:#8b949e;">벤치마크 </span>
          <span style="font-size:11px;color:#58a6ff;">{meta['benchmark']}</span>
        </div>
        <div>
          <span style="font-size:10px;color:#8b949e;">커버리지 </span>
          <span style="font-size:11px;color:#58a6ff;">{meta['coverage']}</span>
        </div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    # ── 생성 버튼 ────────────────────────────────────────────────────────────
    st.caption("💡 PDF 변환은 LibreOffice 필요. 미설치 시 DOCX로 자동 대체됩니다.")

    if st.button("📊 산업 리포트 생성 (PDF)", type="primary",
                 use_container_width=True, key="btn_industry"):
        _run_industry_pipeline(selected_id, meta)


def _run_industry_pipeline(industry_id: str, meta: dict):
    status_box = st.empty()

    def log(msg, state="done"):
        icon = {"done": "✅", "active": "⟳", "error": "❌"}.get(state, "•")
        status_box.markdown(f"""
        <div style="background:#0d1117;border:1px solid #30363d;padding:12px;border-radius:2px;">
          <div style="font-size:12px;color:#c9d1d9;">{icon} {msg}</div>
        </div>""", unsafe_allow_html=True)

    try:
        log(f"1/2  {meta['name_kr']} 산업 데이터 웹서치 + AI 집필 중... (약 2~3분)", "active")
        from industry_report_writer import write_industry_report
        sections = write_industry_report(industry_id)
        log("1/2  AI 집필 완료")

        log("2/2  PDF 변환 중...", "active")
        from industry_docx_generator import generate_industry_pdf
        filepath = generate_industry_pdf(
            sections,
            meta["name_kr"],
            meta["name_en"],
            meta,
            str(OUTPUT_DIR),
        )
        is_pdf = filepath.endswith(".pdf")
        fmt_label = "PDF" if is_pdf else "DOCX (PDF 변환 실패 — LibreOffice 필요)"
        st.session_state.last_report_path = filepath
        log(f"2/2  {fmt_label} 생성 완료: {Path(filepath).name}")

        st.success(f"✅ 산업 리포트 완료: {Path(filepath).name}")
        mime = "application/pdf" if is_pdf else \
               "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        with open(filepath, "rb") as f:
            st.download_button(
                f"⬇️ {fmt_label} 다운로드",
                data=f.read(),
                file_name=Path(filepath).name,
                mime=mime,
                key="dl_industry",
            )

    except ImportError as e:
        st.error(f"모듈 임포트 실패: {e}")
    except Exception as e:
        st.error(f"산업 리포트 생성 오류: {e}")


def _run_pipeline(ticker: str, company: str, send_tg: bool, output_fmt: str = "PDF"):
    status_box = st.empty()

    def log(msg, state="done"):
        icon = {"done": "✅", "active": "⟳", "error": "❌"}.get(state, "•")
        status_box.markdown(f"""
        <div style="background:#0d1117;border:1px solid #30363d;padding:12px;border-radius:2px;">
          <div style="font-size:12px;color:#c9d1d9;">{icon} {msg}</div>
        </div>""", unsafe_allow_html=True)

    try:
        log("1/4  데이터 수집 중...", "active")
        from data_collector import collect_company_data
        collected = collect_company_data(ticker, company)
        log("1/4  데이터 수집 완료")

        log("2/4  AI 레포트 집필 + 웹서치 중... (약 2~3분)", "active")
        from report_writer import write_report
        sections = write_report(ticker, company, collected)
        log("2/4  레포트 집필 완료")

        # 3. DOCX or PDF
        if output_fmt == "PDF":
            log("3/4  PDF 변환 중...", "active")
            from docx_generator import generate_pdf
            filepath = generate_pdf(sections, ticker, company, str(OUTPUT_DIR))
            is_pdf = filepath.endswith(".pdf")
            fmt_label = "PDF" if is_pdf else "DOCX (PDF 변환 실패 — LibreOffice 필요)"
        else:
            log("3/4  DOCX 생성 중...", "active")
            from docx_generator import generate_docx
            filepath = generate_docx(sections, ticker, company, str(OUTPUT_DIR))
            is_pdf = False
            fmt_label = "DOCX"

        st.session_state.last_report_path = filepath
        log(f"3/4  {fmt_label} 생성 완료: {Path(filepath).name}")

        # 4. 텔레그램
        if send_tg:
            log("4/4  텔레그램 전송 중...", "active")
            from telegram_sender import send_report
            ok = send_report(filepath, ticker, company)
            log("4/4  텔레그램 전송 완료" if ok else "4/4  텔레그램 전송 실패", "done" if ok else "error")
        else:
            log("4/4  텔레그램 발송 스킵")

        st.success(f"✅ 레포트 생성 완료: {Path(filepath).name}")

        mime = "application/pdf" if is_pdf else \
               "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        with open(filepath, "rb") as f:
            st.download_button(
                f"⬇️ {fmt_label} 다운로드",
                data=f.read(),
                file_name=Path(filepath).name,
                mime=mime,
            )

    except ImportError as e:
        st.error(f"모듈 임포트 실패: {e}")
        st.info("data_collector.py, report_writer.py, docx_generator.py, telegram_sender.py 파일이 같은 폴더에 있는지 확인하세요.")
    except Exception as e:
        st.error(f"파이프라인 오류: {e}")

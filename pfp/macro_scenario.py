"""
pages/macro_scenario.py
━━━━━━━━━━━━━━━━━━━━━━━
9-에이전트 거시경제 시나리오 파이프라인.
비용 절감을 위해 ① 분석 모드(에이전트 수) ② 모델(Sonnet/Haiku) ③ 컨텍스트 길이
③ 에이전트별 max_tokens 를 조절할 수 있게 구성.
"""

import html as _html
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

TODAY = datetime.now().strftime("%Y년 %m월 %d일")

# 이전 결과를 다음 에이전트 프롬프트에 넘길 때 자르는 길이 (비용 절감 핵심 포인트)
CONTEXT_CHAR_LIMIT = 2000  # 기존 4000 → 2000


# ── 9개 에이전트 정의 (id, label, color, prompt_fn, max_tokens) ──────────────
def _agents(ev: str, prev_results: list[str], portfolio_str: str) -> list[dict]:
    prev = "\n\n---\n\n".join([r for r in prev_results if r])[-CONTEXT_CHAR_LIMIT:]
    return [
        {
            "id": 1, "label": "Event Analysis", "color": "#4a90e2", "max_tokens": 900,
            "use_search": True,
            "prompt": f"""You are a macro event analysis expert. Respond in Korean, concisely.
Today: {TODAY}

Use web_search to find:
1. Current Fed funds rate and latest Fed statement
2. Current S&P500, KOSPI index levels
3. Current USD/KRW exchange rate
4. Current WTI oil price and 10Y Treasury yield
5. Any recent news directly related to this event

Then analyze:
Event: {ev}

Format:
[이벤트 유형] War/Financial Crisis/Policy Change/Commodity Shock/Mixed
[현재 시장 지표] 웹서치로 확인한 금리/환율/지수/유가 현재값
[영향 범위] Regional or global, key affected countries
[핵심 변수] 4-5 key variables (oil, rates, dollar, supply chains, etc.)
[긴급도] High/Med/Low — one-line reason
[메커니즘 분석] 2-3 sentences on causal transmission to markets."""
        },
        {
            "id": 2, "label": "Historical Analogs", "color": "#9b59b6", "max_tokens": 1100,
            "prompt": f"""You are a financial history expert. Respond in Korean, concisely.
Today: {TODAY}
Event: {ev}
Prior analysis: {prev}

Find 2-3 historical analogs. For each:
[사례명 + 연도]
[유사도] X/100 — why
[배경] 1-2 sentences
[S&P500] drawdown %, recovery time
[유가/금리] % change
[교훈] one line

[결론: 가장 강한 유사 사례] 1-2 sentence explanation"""
        },
        {
            "id": 3, "label": "Market Reaction", "color": "#1abc9c", "max_tokens": 900,
            "use_search": True,
            "prompt": f"""You are a market analyst. Respond in Korean, concisely.
Today: {TODAY}
Event: {ev}
Context: {prev}

Use web_search to confirm current levels of S&P500, VIX, 10Y yield, DXY, gold before answering.

[현재 시장 베이스라인] 웹서치로 확인한 현재 S&P500/KOSPI/VIX/금리/환율
[즉시 0-4주] S&P500/KOSPI range, 10Y rates, DXY, gold, VIX expected levels
[단기 1-3개월] bounce probability and conditions, continued decline scenario
[중기 3-12개월] structural direction
[반복 패턴] 2 historically recurring patterns for this event type"""
        },
        {
            "id": 4, "label": "Sector Impact", "color": "#f39c12", "max_tokens": 1100,
            "prompt": f"""You are a sector analyst. Respond in Korean, concisely.
Today: {TODAY}
Event: {ev}
Context: {prev}

[수혜 산업 TOP 3] each: name + example tickers, return range, WHY 1-2 sentences, temporary vs structural
[피해 산업 TOP 3] each: name + tickers, decline range, WHY 1-2 sentences, recovery timeline
[투자자 흔한 실수] one pattern where investors reflexively get it wrong"""
        },
        {
            "id": 5, "label": "Now vs Then", "color": "#1abc9c", "max_tokens": 900,
            "prompt": f"""You are a macro strategist. Respond in Korean, concisely.
Today: {TODAY}
Event: {ev}
Context: {prev}

[공통점 2-3가지] 1 sentence each
[결정적 차이점 3가지]
1. Rate environment: past vs 2025
2. Supply chain: deglobalization/reshoring effects
3. Technology: AI/semiconductors new winners/losers
[결론] Why historical data cannot be applied directly — 1-2 sentences"""
        },
        {
            "id": 6, "label": "Investment Strategy", "color": "#2ecc71", "max_tokens": 1400,
            "prompt": f"""You are a hedge fund CIO. Respond in Korean, concisely.
Today: {TODAY}
Event: {ev}
Context: {prev}

[즉시 0-1개월] buy: ETF/tickers, entry conditions, size%; reduce/short: targets, exit triggers
[단기 1-3개월] 2 core positions with rationale, exit conditions
[중기 3-12개월] structural themes, tickers, target return range
[롱/숏 페어] Long x2 (ticker+reason), Short x2 (ticker+reason)
[포트폴리오 배분] Cash/Bonds/Equities/Commodities/Gold = 100%
[확신도] 0-100 per idea"""
        },
        {
            "id": 7, "label": "Risk Management", "color": "#e74c3c", "max_tokens": 1100,
            "prompt": f"""You are the Chief Risk Officer. Respond in Korean, concisely.
Today: {TODAY}
Event: {ev}
Strategy: {prev}

[전략이 틀릴 조건 2-3가지] specific scenarios that reverse the thesis
[시나리오]
Bull (prob%): conditions, S&P 6mo, optimal positioning
Base (prob%): conditions, S&P, hold positions
Bear (prob%): conditions, S&P, defense/hedge
[손절 기준] specific stop levels
[테일리스크 헤지] 1-2 specific hedge methods"""
        },
        {
            "id": 8, "label": "Portfolio Action", "color": "#e67e22", "max_tokens": 1400,
            "prompt": f"""You are a personal investment advisor. Respond in Korean.
Today: {TODAY}
Macro event: {ev}
Full analysis: {prev}

The investor holds the following portfolio:
{portfolio_str}

For EACH holding, provide a specific action recommendation.
Return ONLY a raw JSON array (no markdown, no preamble):
[
  {{
    "ticker": "AAPL",
    "action": "SELL",
    "reason": "이유 (1문장)",
    "urgency": "즉시 / 1개월 내 / 3개월 내"
  }}
]"""
        },
        {
            "id": 9, "label": "Final Verdict", "color": "#8e44ad", "max_tokens": 2200,
            "prompt": f"""You are the final synthesis agent for a fintech dashboard. Respond ONLY in Korean text values.
Today: {TODAY}
Event: {ev}
All analyses: {prev}

Synthesize everything above into EXACTLY 4 scenario cards covering different
risk levels / situations (e.g. base case, bull case, bear case, tail risk —
or whatever 4 distinct angles best fit this specific event). Each card must
be genuinely different in tone/color, not 4 variations of the same view.

CRITICAL LENGTH CONSTRAINT: Keep "details" to 2-3 short sentences MAX (under
120 Korean characters each). You MUST finish the complete JSON with the final
closing braces — running out of tokens mid-JSON is a hard failure. Prioritize
completing valid JSON over adding more detail. Brevity in each field is
required so all 4 cards fit within the token budget.

Return ONLY a raw JSON object — no markdown code fences, no ``` backticks,
no preamble, no explanation text before or after. The response must start
with {{ and end with }}. Exactly this schema:

{{
  "cards": [
    {{
      "title": "시나리오 제목 (짧게, 예: 고인플레이션 & 금리 인상)",
      "icon": "한 개의 이모지 (예: 🔺, 📉, ⚡, 🛡️)",
      "color": "danger 또는 warning 또는 success 또는 info 중 정확히 하나",
      "headline": "핵심 헤드라인 한 줄 (30자 이내)",
      "summary": "1줄 요약 (40자 이내)",
      "details": "2~3문장, 120자 이내의 간결한 상세 분석 및 포트폴리오 영향도"
    }}
  ]
}}

Rules:
- "cards" array must contain EXACTLY 4 objects.
- "color" must be exactly one of: danger, warning, success, info (lowercase, no other values).
- All text values in Korean, kept SHORT as specified above.
- No trailing commas, valid JSON only.
- The JSON MUST be syntactically complete — do not cut off mid-string or mid-object."""
        },
    ]


# ── 분석 모드 (실행할 에이전트 id 목록) ──────────────────────────────────────
ANALYSIS_MODES = {
    "⚡ 빠른 분석 (3개 에이전트)":   [1, 6, 9],
    "🔍 표준 분석 (5개 에이전트)":   [1, 3, 6, 8, 9],
    "🔬 전체 분석 (9개 에이전트)":   [1, 2, 3, 4, 5, 6, 7, 8, 9],
}

MODEL_OPTIONS = {
    "Claude Sonnet (고품질 · 비용 높음)": "claude-sonnet-4-6",
    "Claude Haiku (저비용 · 빠름)":       "claude-haiku-4-5-20251001",
}


# ── 에이전트별 UI 메타데이터 (아이콘 · 카드 색상) ──────────────────────────────
AGENT_UI_META = {
    1: {"icon": "🔍", "color": "info",    "label": "Event Analysis"},
    2: {"icon": "📚", "color": "warning",  "label": "Historical Analogs"},
    3: {"icon": "📊", "color": "danger",   "label": "Market Reaction"},
    4: {"icon": "🏭", "color": "warning",  "label": "Sector Impact"},
    5: {"icon": "⚡", "color": "info",    "label": "Now vs Then"},
    6: {"icon": "💡", "color": "success",  "label": "Investment Strategy"},
    7: {"icon": "🛡️", "color": "danger",  "label": "Risk Management"},
    8: {"icon": "💼", "color": "warning",  "label": "Portfolio Action"},
    9: {"icon": "🧭", "color": "info",    "label": "Final Verdict"},
}


def _extract_agent_summary(text: str, max_chars: int = 55) -> str:
    """에이전트 출력에서 첫 번째 의미있는 줄을 요약으로 추출."""
    if not text or text.startswith("[오류") or text.startswith("[Error"):
        return "⚠ 분석 오류"
    for line in text.split("\n"):
        line = line.strip().lstrip("#*[").strip()
        if line and len(line) > 8 and not line.startswith("{"):
            return line[:max_chars] + ("…" if len(line) > max_chars else "")
    return text[:max_chars]


def _run_parallel_agents(
    selected_ids: list[int],
    ev: str,
    portfolio_str: str,
    model: str,
) -> dict[int, str]:
    """
    선택된 에이전트들을 ThreadPoolExecutor로 동시에 실행.
    에이전트 간 컨텍스트 체이닝을 폐기하고, 각 에이전트가 독립 실행.
    """
    # prev_results=[] → 모든 에이전트가 이벤트만 단독 입력으로 실행
    all_agents = _agents(ev, [], portfolio_str)
    agent_map = {a["id"]: a for a in all_agents if a["id"] in selected_ids}

    results: dict[int, str] = {}

    def _call_single(ag: dict):
        try:
            text = _call_claude(
                ag["prompt"], model, ag["max_tokens"],
                use_search=ag.get("use_search", False),
            )
            return ag["id"], text
        except Exception as exc:
            return ag["id"], f"[오류: {exc}]"

    max_workers = min(len(agent_map), 6)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_call_single, ag) for ag in agent_map.values()]
        for future in as_completed(futures):
            ag_id, text = future.result()
            results[ag_id] = text

    return results


def _render_agent_card_grid(run_agents: list[dict], results: dict[int, str]):
    """
    모든 에이전트 결과를 3열 카드 그리드로 표시.
    카드 클릭 시 하단 다크 박스에 상세 리포트 동적 표출.
    """
    SELECTED_KEY = "_selected_agent_card"
    if SELECTED_KEY not in st.session_state:
        st.session_state[SELECTED_KEY] = None

    st.markdown("""
    <div style="margin:16px 0 10px;">
      <span style="font-size:15px;font-weight:800;color:#e0e0e0;">📋 AGENT RESULTS</span>
      <span style="font-size:11px;color:#8b949e;margin-left:8px;">카드를 클릭하면 상세 분석이 펼쳐집니다</span>
    </div>
    """, unsafe_allow_html=True)

    for row_start in range(0, len(run_agents), 3):
        row_agents = run_agents[row_start:row_start + 3]
        cols = st.columns(3)
        for i, ag in enumerate(row_agents):
            meta = AGENT_UI_META.get(ag["id"], {"icon": "📋", "color": "info", "label": ag["label"]})
            style = CARD_COLOR_STYLES[meta["color"]]
            text = results.get(ag["id"], "")
            summary = _extract_agent_summary(text)
            ok = bool(text and not text.startswith("[오류") and not text.startswith("[Error"))
            is_selected = st.session_state[SELECTED_KEY] == ag["id"]

            with cols[i]:
                border = f"border:2px solid {style['bg']};" if is_selected else "border:1px solid #30363d;"
                safe_summary = _html.escape(summary)
                st.markdown(f"""
                <div style="background:#0d1117;{border}border-left:4px solid {style['bg']};
                             border-radius:8px;padding:14px 12px;min-height:100px;
                             box-shadow:0 2px 8px rgba(0,0,0,0.3);margin-bottom:4px;">
                  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
                    <span style="font-size:20px;">{meta['icon']}</span>
                    <span style="font-size:10px;color:{'#22c55e' if ok else '#ef4444'};font-weight:700;">
                      {'✓' if ok else '✗'}
                    </span>
                  </div>
                  <div style="font-size:11px;font-weight:700;color:#e0e0e0;margin-bottom:5px;">
                    {ag['id']:02d}. {ag['label'].upper()}
                  </div>
                  <div style="font-size:10px;color:#8b949e;line-height:1.5;">{safe_summary}</div>
                </div>
                """, unsafe_allow_html=True)

                btn_label = "✓ 선택됨" if is_selected else "상세 보기 →"
                if st.button(btn_label, key=f"agcard_{ag['id']}", use_container_width=True):
                    st.session_state[SELECTED_KEY] = None if is_selected else ag["id"]
                    st.rerun()

    # ── 선택된 카드의 상세 리포트 패널 ──────────────────────────────────────
    selected_id = st.session_state.get(SELECTED_KEY)
    if selected_id is not None:
        ag = next((a for a in run_agents if a["id"] == selected_id), None)
        if ag:
            meta = AGENT_UI_META.get(ag["id"], {"icon": "📋", "color": "info"})
            style = CARD_COLOR_STYLES[meta["color"]]
            text = results.get(ag["id"], "")

            # ── 상세 패널 헤더 ─────────────────────────────────────────────
            st.markdown(
                f'<div style="background:#0d1117;border:1px solid {style["bg"]};'
                f'border-left:4px solid {style["bg"]};border-radius:8px 8px 0 0;'
                f'padding:14px 20px;margin-top:14px;'
                f'display:flex;align-items:center;gap:10px;">'
                f'<span style="font-size:26px;">{meta["icon"]}</span>'
                f'<span style="font-size:16px;font-weight:700;color:#e0e0e0;">'
                f'{ag["id"]:02d} — {_html.escape(ag["label"].upper())}'
                f'</span>'
                f'<span style="color:{style["bg"]};font-size:11px;margin-left:auto;font-weight:700;">'
                f'FULL REPORT</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

            # ── 본문 콘텐츠 ────────────────────────────────────────────────
            # st.markdown(text) 직접 사용 → ##헤더·표·볼드 모두 올바르게 렌더링
            # (HTML div 안에 raw 삽입 시 마크다운이 이중 파싱되어 공백·글 끊김 발생)
            with st.container():
                st.markdown(
                    '<div style="background:#0d1117;border:1px solid #30363d;'
                    'border-top:none;border-radius:0 0 8px 8px;'
                    'padding:16px 20px;margin-bottom:4px;">',
                    unsafe_allow_html=True,
                )
                if ag["id"] == 9:
                    # Final Verdict → 기존 카드 그리드 + 원문
                    cards = _parse_verdict_cards(text)
                    if cards:
                        _render_verdict_card_grid(cards)
                        with st.expander("📄 원문 JSON 텍스트"):
                            st.code(text, language="json")
                    else:
                        st.markdown(text)
                elif ag["id"] == 8:
                    # Portfolio Action → JSON 카드 + 원문
                    holdings = st.session_state.get("holdings") or {}
                    _render_portfolio_actions(text, holdings)
                    with st.expander("📄 원문 텍스트"):
                        st.markdown(text)
                else:
                    # 1~7번: 마크다운 직접 렌더링 (표·헤더 정상 표시)
                    st.markdown(text)
                st.markdown("</div>", unsafe_allow_html=True)

            if st.button("✕ 패널 닫기", key="close_agent_detail"):
                st.session_state[SELECTED_KEY] = None
                st.rerun()


def _format_portfolio(holdings: dict) -> str:
    if not holdings:
        return "포트폴리오 없음"
    lines = []
    for t, info in holdings.items():
        if t == 'CASH':
            lines.append(f"CASH: ${info['q']:,.0f}")
        else:
            lines.append(f"{t}: {info['q']}주 @ avg ${info['avg']:,.2f} (섹터: {info.get('sector','-')})")
    return "\n".join(lines)


def _call_claude(prompt: str, model: str, max_tokens: int, use_search: bool = False) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    kwargs = dict(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}]
    )
    if use_search:
        kwargs["tools"] = [{"type": "web_search_20250305", "name": "web_search"}]

    msg = client.messages.create(**kwargs)

    # 텍스트 블록만 추출 (tool_use / tool_result 블록 제외)
    return "".join(
        block.text for block in msg.content
        if getattr(block, "type", None) == "text"
    )


# ── Final Verdict 카드 그리드 (매거진 타일 스타일) ────────────────────────────

# 이미지 레퍼런스처럼 채도 높은 블록 컬러 + 진한 텍스트 대비
CARD_COLOR_STYLES = {
    "danger":  {"bg": "#dc2626", "bg2": "#b91c1c", "text": "#ffffff", "sub": "#fecaca", "accent": "#fca5a5"},
    "warning": {"bg": "#ea580c", "bg2": "#c2410c", "text": "#ffffff", "sub": "#fed7aa", "accent": "#fdba74"},
    "success": {"bg": "#16a34a", "bg2": "#15803d", "text": "#ffffff", "sub": "#bbf7d0", "accent": "#86efac"},
    "info":    {"bg": "#1e2530", "bg2": "#171c24", "text": "#e5e7eb", "sub": "#9ca3af", "accent": "#60a5fa"},
}


def _parse_verdict_cards(raw_text: str) -> list[dict] | None:
    """
    9번 에이전트(Final Verdict) 출력에서 카드 JSON을 파싱.

    토큰 한도로 인해 JSON이 중간에 잘리는 경우(마지막 카드의 닫는 괄호가
    누락되는 등)에도 최대한 복구를 시도한다:
      1차) 전체를 표준 JSON으로 파싱
      2차) 실패 시, "cards" 배열 안에서 완전한 형태로 닫힌 카드 객체들만
           정규식으로 추출해 부분 복구 (잘린 마지막 카드는 버림)
    """
    import re

    def _normalize(cards_raw: list[dict]) -> list[dict]:
        for c in cards_raw:
            if c.get("color") not in CARD_COLOR_STYLES:
                c["color"] = "info"
        return cards_raw

    # ── 1차: 표준 JSON 파싱 ───────────────────────────────────────────────────
    try:
        match = re.search(r'\{.*\}', raw_text, re.DOTALL)
        if match:
            data = json.loads(match.group())
            cards = data.get("cards", [])
            if isinstance(cards, list) and len(cards) > 0:
                return _normalize(cards)
    except Exception:
        pass

    # ── 2차: 잘린 JSON 부분 복구 — 완전히 닫힌 카드 객체만 개별 추출 ─────────
    try:
        # cards 배열 "안쪽"의 개별 카드 객체({ })만 골라낸다.
        # depth=1: 바깥 {"cards": [...]} 전체 / depth=2: 그 안의 카드 객체 1개.
        # 토큰이 끊겨 마지막 카드의 닫는 '}'가 없어도, depth==2에서 정상적으로
        # 닫힌 앞쪽 카드들은 그대로 살려서 반환한다.
        recovered = []
        depth = 0
        start = None
        for i, ch in enumerate(raw_text):
            if ch == "{":
                depth += 1
                if depth == 2:
                    start = i
            elif ch == "}":
                if depth == 2 and start is not None:
                    chunk = raw_text[start:i + 1]
                    if '"title"' in chunk:
                        try:
                            obj = json.loads(chunk)
                            if "title" in obj:
                                recovered.append(obj)
                        except Exception:
                            pass
                    start = None
                depth -= 1

        if recovered:
            return _normalize(recovered)
    except Exception:
        pass

    return None


def _render_verdict_card_grid(cards: list[dict]):
    """
    Final Verdict 카드를 매거진/카드뉴스 스타일 비대칭 타일 그리드로 렌더링.
    카드[0]은 좌측 큰 메인 타일, 카드[1~3]은 우측 보조 타일로 배치.
    클릭 시 하단에 상세 분석 패널이 펼쳐짐.
    """
    st.markdown("""
    <div style="margin:20px 0 4px;">
      <span style="font-size:15px;font-weight:800;color:#e0e0e0;">🧭 FINAL VERDICT</span>
      <span style="font-size:11px;color:#8b949e;margin-left:8px;">AI 9-에이전트 종합 시나리오</span>
    </div>
    """, unsafe_allow_html=True)

    if "_selected_verdict_card" not in st.session_state:
        st.session_state["_selected_verdict_card"] = None

    n = len(cards)
    main_card = cards[0] if n > 0 else None
    sub_cards = cards[1:4] if n > 1 else []

    col_main, col_sub = st.columns([1.3, 1])

    # ── 좌측: 메인 카드 (큰 타일) ────────────────────────────────────────────
    if main_card:
        idx = 0
        style = CARD_COLOR_STYLES.get(main_card.get("color", "info"), CARD_COLOR_STYLES["info"])
        with col_main:
            st.markdown(f"""
            <div style="background:linear-gradient(150deg,{style['bg']} 0%,{style['bg2']} 100%);
                         border-radius:14px;padding:22px 20px;height:228px;
                         display:flex;flex-direction:column;justify-content:space-between;
                         box-shadow:0 4px 14px rgba(0,0,0,0.35);">
              <div>
                <div style="font-size:30px;margin-bottom:8px;">{main_card.get('icon','📊')}</div>
                <div style="font-size:19px;font-weight:800;color:{style['text']};line-height:1.3;margin-bottom:8px;">
                  {main_card.get('title','')}
                </div>
                <div style="font-size:12px;color:{style['sub']};font-weight:600;line-height:1.5;">
                  {main_card.get('headline','')}
                </div>
              </div>
              <div style="font-size:11px;color:{style['sub']};opacity:0.85;">
                {main_card.get('summary','')}
              </div>
            </div>
            """, unsafe_allow_html=True)
            if st.button("상세 분석 보기 →", key=f"card_btn_{idx}", use_container_width=True):
                st.session_state["_selected_verdict_card"] = idx

    # ── 우측: 보조 카드 3개 (정사각형 타일, 2+1 배치) ────────────────────────
    with col_sub:
        sr1 = st.columns(2)
        for i, card in enumerate(sub_cards[:2]):
            idx = i + 1
            style = CARD_COLOR_STYLES.get(card.get("color", "info"), CARD_COLOR_STYLES["info"])
            with sr1[i]:
                st.markdown(f"""
                <div style="background:{style['bg']};border-radius:12px;padding:14px;height:106px;
                             display:flex;flex-direction:column;justify-content:space-between;
                             box-shadow:0 2px 8px rgba(0,0,0,0.3);">
                  <div style="font-size:20px;">{card.get('icon','📊')}</div>
                  <div style="font-size:11px;font-weight:700;color:{style['text']};line-height:1.3;">
                    {card.get('title','')}
                  </div>
                </div>
                """, unsafe_allow_html=True)
                if st.button("자세히 →", key=f"card_btn_{idx}", use_container_width=True):
                    st.session_state["_selected_verdict_card"] = idx

        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

        if len(sub_cards) >= 3:
            idx = 3
            card = sub_cards[2]
            style = CARD_COLOR_STYLES.get(card.get("color", "info"), CARD_COLOR_STYLES["info"])
            st.markdown(f"""
            <div style="background:{style['bg']};border-radius:12px;padding:14px;height:106px;
                         display:flex;align-items:center;gap:12px;
                         box-shadow:0 2px 8px rgba(0,0,0,0.3);">
              <div style="font-size:24px;">{card.get('icon','📊')}</div>
              <div style="font-size:12px;font-weight:700;color:{style['text']};line-height:1.3;">
                {card.get('title','')}
              </div>
            </div>
            """, unsafe_allow_html=True)
            if st.button("자세히 →", key=f"card_btn_{idx}", use_container_width=True):
                st.session_state["_selected_verdict_card"] = idx

    # ── 출처/안내 표시줄 ──────────────────────────────────────────────────────
    st.markdown(f"""
    <div style="display:flex;justify-content:space-between;align-items:center;
                 margin:10px 0 2px;padding:6px 2px;border-top:1px solid #21262d;">
      <span style="font-size:10px;color:#6b7280;">📋 출처: Claude AI 9-에이전트 합성 분석 · {TODAY}</span>
      <span style="font-size:10px;color:#6b7280;">⚡ Powered by Anthropic</span>
    </div>
    """, unsafe_allow_html=True)

    # ── 클릭된 카드의 상세 분석 패널 ──────────────────────────────────────────
    selected_idx = st.session_state.get("_selected_verdict_card")
    if selected_idx is not None and 0 <= selected_idx < len(cards):
        card = cards[selected_idx]
        style = CARD_COLOR_STYLES.get(card.get("color", "info"), CARD_COLOR_STYLES["info"])

        st.markdown(
            f'<div style="background:#0d1117;border:1px solid {style["bg"]};'
            f'border-left:4px solid {style["bg"]};border-radius:8px;'
            f'padding:20px;margin-top:14px;">'
            f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;">'
            f'<span style="font-size:24px;">{_html.escape(card.get("icon","📊"))}</span>'
            f'<span style="font-size:16px;font-weight:700;color:#e0e0e0;">'
            f'{_html.escape(card.get("title",""))}</span>'
            f'</div>'
            f'<div style="font-size:13px;font-weight:600;color:{style["accent"]};margin-bottom:12px;">'
            f'{_html.escape(card.get("headline",""))}'
            f'</div>'
            f'<div style="font-size:13px;color:#c9d1d9;line-height:1.9;white-space:pre-wrap;">'
            f'{_html.escape(card.get("details","상세 분석 정보가 없습니다."))}'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        if st.button("✕ 상세 패널 닫기", key="close_verdict_detail"):
            st.session_state["_selected_verdict_card"] = None
            st.rerun()


def render():
    st.markdown("""
    <div style="padding:12px 0 8px;">
      <div style="font-size:10px;color:#58a6ff;font-weight:700;letter-spacing:2px;">AGENT PIPELINE</div>
      <div style="font-size:20px;font-weight:700;color:#9b59b6;">MACRO SCENARIO ANALYSIS</div>
    </div>
    """, unsafe_allow_html=True)

    if not ANTHROPIC_API_KEY:
        st.error("⚠️ .env 파일에 ANTHROPIC_API_KEY를 설정하세요.")
        st.code("ANTHROPIC_API_KEY=sk-ant-...", language="bash")

    # ── 비용 절감 옵션 ────────────────────────────────────────────────────────
    col_mode, col_model = st.columns(2)
    with col_mode:
        mode_label = st.selectbox("분석 모드", list(ANALYSIS_MODES.keys()), index=0)
    with col_model:
        model_label = st.selectbox("모델", list(MODEL_OPTIONS.keys()), index=0)

    selected_ids = ANALYSIS_MODES[mode_label]
    model = MODEL_OPTIONS[model_label]

    # 예상 비용 가이드 (대략적인 max_tokens 합계)
    all_agents_meta = _agents("", [], "")
    est_max_tokens = sum(a["max_tokens"] for a in all_agents_meta if a["id"] in selected_ids)
    st.caption(
        f"💡 이번 실행: API 호출 **{len(selected_ids)}회** · "
        f"출력 토큰 한도 합계 **~{est_max_tokens:,} tokens** "
        f"({'Haiku — Sonnet 대비 약 1/12 비용' if 'haiku' in model else 'Sonnet'})"
    )

    # 이벤트 입력
    col_in, col_btn = st.columns([4, 1])
    with col_in:
        event_input = st.text_area(
            "거시경제 이벤트 입력",
            value=st.session_state.macro_event,
            height=80,
            placeholder="예: China blockades Taiwan — semiconductor supply chain collapse",
            label_visibility="collapsed",
        )
    with col_btn:
        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
        run_btn = st.button("▶ RUN", type="primary", use_container_width=True,
                            disabled=not ANTHROPIC_API_KEY)

    # 프리셋
    st.markdown("<div style='font-size:10px;color:#8b949e;margin:4px 0 6px;'>QUICK SCENARIOS:</div>", unsafe_allow_html=True)
    presets = [
        "Fed emergency 100bp hike — dollar spike, EM crisis",
        "China blockades Taiwan — semiconductor supply chain collapse",
        "US major bank liquidity crisis — 2008 repeat fears",
        "Saudi Arabia exits OPEC+, oil crashes 30% in one day",
        "US-China full trade war, tariffs exceed 100%",
    ]
    p_cols = st.columns(len(presets))
    for i, (col, preset) in enumerate(zip(p_cols, presets)):
        with col:
            short = preset[:30] + "..."
            if st.button(short, key=f"preset_{i}", use_container_width=True):
                st.session_state.macro_event = preset
                st.rerun()

    st.divider()

    # 분석 실행
    if run_btn and event_input.strip():
        st.session_state.macro_event = event_input
        st.session_state["_selected_verdict_card"] = None
        st.session_state["_selected_agent_card"] = None
        holdings = st.session_state.holdings or {}
        portfolio_str = _format_portfolio(holdings)

        all_agents_meta = _agents(event_input, [], portfolio_str)
        run_agents = [a for a in all_agents_meta if a["id"] in selected_ids]

        # ── 비동기 병렬 실행 ──────────────────────────────────────────────
        with st.spinner(f"⚡ {len(run_agents)}개 에이전트 병렬 실행 중... (기존 순차 방식 대비 ~3배 빠름)"):
            parallel_results = _run_parallel_agents(selected_ids, event_input, portfolio_str, model)

        # meta 리스트 구성 (저장용)
        result_meta = [
            {
                "id": ag["id"],
                "label": ag["label"],
                "color": ag["color"],
                "text": parallel_results.get(ag["id"], "[오류: 결과 없음]"),
            }
            for ag in run_agents
        ]

        st.session_state.macro_results = {
            "event":   event_input,
            "results": {m["label"]: m["text"] for m in result_meta},
            "raw":     [m["text"] for m in result_meta],
            "meta":    result_meta,
        }

        st.success(f"✅ 분석 완료 ({len(run_agents)}개 에이전트 · 병렬 처리)")

        # ── 9-카드 그리드 표시 ────────────────────────────────────────────
        _render_agent_card_grid(run_agents, parallel_results)

    # 이전 결과 표시
    elif st.session_state.macro_results:
        st.info(f"📋 마지막 분석: **{st.session_state.macro_event}**")
        meta = st.session_state.macro_results.get("meta", [])
        if meta:
            all_agents_meta = _agents(st.session_state.macro_event, [], "")
            run_agents = [a for a in all_agents_meta if a["id"] in [m["id"] for m in meta]]
            prev_results = {m["id"]: m["text"] for m in meta}
            _render_agent_card_grid(run_agents, prev_results)
        else:
            # 구버전 데이터 호환
            raw = st.session_state.macro_results.get("raw", [])
            for i, text in enumerate(raw):
                with st.expander(f"{i+1:02d}", expanded=(i == len(raw) - 1)):
                    st.markdown(
                        f'<div style="font-size:12px;color:#c9d1d9;line-height:1.8;white-space:pre-wrap;">{text}</div>',
                        unsafe_allow_html=True,
                    )


def _render_portfolio_actions(raw_text: str, holdings: dict):
    """Portfolio Action 에이전트 JSON 파싱 → 액션 테이블 렌더링"""
    st.markdown("---")
    st.markdown("<div style='font-size:14px;font-weight:700;color:#e67e22;margin-bottom:10px;'>💼 PORTFOLIO ACTION PLAN</div>", unsafe_allow_html=True)
    try:
        import re
        match = re.search(r'\[.*\]', raw_text, re.DOTALL)
        if match:
            actions = json.loads(match.group())
            action_colors = {
                "SELL": "#e74c3c", "BUY": "#2ecc71",
                "HOLD": "#8b949e", "PARTIAL SELL": "#f39c12",
            }
            cols = st.columns(min(len(actions), 4))
            for i, action in enumerate(actions):
                clr = action_colors.get(action.get("action", "HOLD").upper(), "#8b949e")
                with cols[i % len(cols)]:
                    st.markdown(f"""
                    <div style="background:#0d1117;border:1px solid #30363d;
                                 border-top:3px solid {clr};padding:10px;border-radius:2px;margin-bottom:6px;">
                      <div style="color:#58a6ff;font-weight:700;font-size:14px;">{action.get('ticker','?')}</div>
                      <div style="color:{clr};font-weight:700;font-size:12px;margin:4px 0;">{action.get('action','?')}</div>
                      <div style="color:#8b949e;font-size:11px;">{action.get('urgency','')}</div>
                      <div style="color:#c9d1d9;font-size:11px;margin-top:6px;line-height:1.6;">{action.get('reason','')}</div>
                    </div>
                    """, unsafe_allow_html=True)
    except Exception:
        st.markdown(f'<div style="font-size:12px;color:#c9d1d9;white-space:pre-wrap;">{raw_text}</div>', unsafe_allow_html=True)

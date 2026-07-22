"""
services/ai_analysis.py
────────────────────────
Claude API 호출 로직. macro_scenario.py + alpha_terminal.py에서 추출.
Streamlit 의존 없음.
"""
from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import AsyncIterator

from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent.parent.parent / "pfp" / ".env")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
TODAY = datetime.now().strftime("%Y년 %m월 %d일")
CONTEXT_CHAR_LIMIT = 4000    # Phase 1 에이전트 컨텍스트 한도
PHASE2_CONTEXT_LIMIT = 10000  # Phase 2 에이전트(8, 9)는 더 많은 컨텍스트 허용

ANALYSIS_MODES = {
    "fast":     [1, 6, 9],
    "standard": [1, 3, 6, 8, 9],
    "full":     [1, 2, 3, 4, 5, 6, 7, 8, 9],
}

MODEL_OPTIONS = {
    "sonnet": "claude-sonnet-4-6",
    "haiku":  "claude-haiku-4-5-20251001",
}


# ── 에이전트 정의 ───────────────────────────────────────────────────────────────

def _build_agents(
    ev: str,
    portfolio_str: str,
    prev_results: list[str] | None = None,
    context_limit: int = CONTEXT_CHAR_LIMIT,
) -> list[dict]:
    prev = "\n\n---\n\n".join([r for r in (prev_results or []) if r])[-context_limit:]
    return [
        {
            "id": 1, "label": "Event Analysis", "max_tokens": 900, "use_search": True,
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
            "id": 2, "label": "Historical Analogs", "max_tokens": 1100, "use_search": False,
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
            "id": 3, "label": "Market Reaction", "max_tokens": 900, "use_search": True,
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
            "id": 4, "label": "Sector Impact", "max_tokens": 1100, "use_search": False,
            "prompt": f"""You are a sector analyst. Respond in Korean, concisely.
Today: {TODAY}
Event: {ev}
Context: {prev}

[수혜 산업 TOP 3] each: name + example tickers, return range, WHY 1-2 sentences, temporary vs structural
[피해 산업 TOP 3] each: name + tickers, decline range, WHY 1-2 sentences, recovery timeline
[투자자 흔한 실수] one pattern where investors reflexively get it wrong"""
        },
        {
            "id": 5, "label": "Now vs Then", "max_tokens": 900, "use_search": False,
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
            "id": 6, "label": "Investment Strategy", "max_tokens": 1400, "use_search": False,
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
            "id": 7, "label": "Risk Management", "max_tokens": 1100, "use_search": False,
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
            "id": 8, "label": "Portfolio Action", "max_tokens": 1400, "use_search": False,
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
            "id": 9, "label": "Final Verdict", "max_tokens": 2200, "use_search": False,
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


def _format_portfolio(holdings: dict) -> str:
    if not holdings:
        return "포트폴리오 없음"
    lines = []
    for t, info in holdings.items():
        if t == "CASH":
            lines.append(f"CASH: ${info['q']:,.0f}")
        else:
            lines.append(f"{t}: {info['q']}주 @ avg ${info['avg']:,.2f} (섹터: {info.get('sector', '-')})")
    return "\n".join(lines)


# ── Claude 단건 호출 ─────────────────────────────────────────────────────────────

def call_claude(prompt: str, model: str, max_tokens: int, use_search: bool = False) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    kwargs = dict(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    if use_search:
        kwargs["tools"] = [{"type": "web_search_20250305", "name": "web_search"}]

    msg = client.messages.create(**kwargs)
    return "".join(
        block.text for block in msg.content
        if getattr(block, "type", None) == "text"
    )


# ── 병렬 에이전트 실행 (Phase 1) ─────────────────────────────────────────────────

def _run_parallel_agents(
    selected_ids: list[int],
    ev: str,
    portfolio_str: str,
    model: str,
) -> dict[int, tuple[str, float]]:
    """Phase 1: 선택된 에이전트를 컨텍스트 없이 병렬 실행."""
    all_agents = _build_agents(ev, portfolio_str, prev_results=[])
    agent_map = {a["id"]: a for a in all_agents if a["id"] in selected_ids}

    results: dict[int, tuple[str, float]] = {}

    def _call(ag: dict):
        import time
        t0 = time.time()
        try:
            text = call_claude(ag["prompt"], model, ag["max_tokens"], ag.get("use_search", False))
            return ag["id"], text, time.time() - t0
        except Exception as exc:
            return ag["id"], f"[오류: {exc}]", time.time() - t0

    with ThreadPoolExecutor(max_workers=min(len(agent_map), 6)) as executor:
        futures = [executor.submit(_call, ag) for ag in agent_map.values()]
        for f in as_completed(futures):
            ag_id, text, t = f.result()
            results[ag_id] = (text, round(t, 2))

    return results


def _run_contextual_agents(
    selected_ids: list[int],
    ev: str,
    portfolio_str: str,
    model: str,
    context_texts: list[str],
) -> dict[int, tuple[str, float]]:
    """Phase 2: Phase 1 결과를 컨텍스트로 받아 에이전트를 순차 실행 (agents 8, 9)."""
    all_agents = _build_agents(ev, portfolio_str, prev_results=context_texts, context_limit=PHASE2_CONTEXT_LIMIT)
    agent_map = {a["id"]: a for a in all_agents if a["id"] in selected_ids}

    results: dict[int, tuple[str, float]] = {}
    for ag_id in sorted(selected_ids):
        if ag_id not in agent_map:
            continue
        ag = agent_map[ag_id]
        import time
        t0 = time.time()
        try:
            text = call_claude(ag["prompt"], model, ag["max_tokens"], ag.get("use_search", False))
        except Exception as exc:
            text = f"[오류: {exc}]"
        results[ag_id] = (text, round(time.time() - t0, 2))

    return results


def run_macro_agents(
    event: str,
    portfolio: dict,
    model_key: str = "sonnet",
    mode: str = "fast",
) -> list[dict]:
    """
    2단계 파이프라인으로 에이전트 실행.
    Phase 1 (id ≤ 7): 병렬 독립 분석
    Phase 2 (id > 7): Phase 1 전체 결과를 컨텍스트로 받아 순차 종합
    반환: [{ id, name, text, elapsed, ok }, ...]
    """
    model = MODEL_OPTIONS.get(model_key, MODEL_OPTIONS["sonnet"])
    selected_ids = ANALYSIS_MODES.get(mode, ANALYSIS_MODES["fast"])
    portfolio_str = _format_portfolio(portfolio)

    phase1_ids = [i for i in selected_ids if i <= 7]
    phase2_ids = [i for i in selected_ids if i > 7]

    all_results: dict[int, tuple[str, float]] = {}

    # Phase 1: 병렬 독립 실행
    if phase1_ids:
        all_results.update(_run_parallel_agents(phase1_ids, event, portfolio_str, model))

    # Phase 2: Phase 1 컨텍스트 기반 순차 실행
    if phase2_ids:
        p1_texts = [
            all_results[i][0] for i in sorted(phase1_ids)
            if i in all_results and not all_results[i][0].startswith("[오류")
        ]
        all_results.update(_run_contextual_agents(phase2_ids, event, portfolio_str, model, p1_texts))

    # 정의된 에이전트 순서 기준으로 반환 목록 구성
    all_agents = _build_agents(event, portfolio_str)
    return [
        {
            "id":      ag["id"],
            "name":    ag["label"],
            "text":    all_results.get(ag["id"], ("[오류: 결과 없음]", 0.0))[0],
            "elapsed": all_results.get(ag["id"], ("[오류: 결과 없음]", 0.0))[1],
            "ok":      not all_results.get(ag["id"], ("[오류: 결과 없음]", 0.0))[0].startswith("[오류"),
        }
        for ag in all_agents if ag["id"] in selected_ids
    ]


# ── Final Verdict JSON 파싱 ──────────────────────────────────────────────────────

def parse_verdict_cards(raw_text: str) -> list[dict] | None:
    VALID_COLORS = {"danger", "warning", "success", "info"}

    def _normalize(cards: list[dict]) -> list[dict]:
        for c in cards:
            if c.get("color") not in VALID_COLORS:
                c["color"] = "info"
        return cards

    # 1차: 표준 JSON 파싱
    try:
        match = re.search(r"\{.*\}", raw_text, re.DOTALL)
        if match:
            data = json.loads(match.group())
            cards = data.get("cards", [])
            if isinstance(cards, list) and cards:
                return _normalize(cards)
    except Exception:
        pass

    # 2차: 잘린 JSON 부분 복구
    try:
        recovered = []
        depth, start = 0, None
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


def parse_portfolio_actions(raw_text: str) -> list[dict] | None:
    try:
        match = re.search(r"\[.*\]", raw_text, re.DOTALL)
        if match:
            actions = json.loads(match.group())
            if isinstance(actions, list):
                return actions
    except Exception:
        pass
    return None


# ── AI Analyst 실시간 피드백 (alpha_terminal에서 추출) ─────────────────────────────

def get_ai_analyst_feedback(
    vix: float,
    portfolio_beta: float,
    today_chg_pct: float,
    sector_summary: str,
    is_portfolio_sectors: bool = False,
) -> str:
    if not ANTHROPIC_API_KEY:
        return "ANTHROPIC_API_KEY 미설정"

    vix_state = "위험" if vix >= 30 else ("주의" if vix >= 20 else "정상")

    if is_portfolio_sectors:
        prompt = f"""다음 데이터를 바탕으로 투자자에게 3~4문장(120자 이내)의 포트폴리오 섹터 분석 피드백을 한국어로 작성해줘.
보유 섹터의 오늘 흐름과 리스크를 관찰 기반 코멘트 톤으로, 구체적 수치를 인용해서 작성해.

- VIX 지수: {vix:.1f} ({vix_state})
- 포트폴리오 베타: {portfolio_beta:.2f}
- 오늘 포트폴리오 변동률: {today_chg_pct:+.2f}%
- 보유 섹터 비중 및 오늘 변동: {sector_summary}

출력은 텍스트 3~4문장만, 따옴표나 마크다운 없이. 보유 섹터를 중심으로 분석할 것."""
    else:
        prompt = f"""다음 데이터를 바탕으로 투자자에게 1~2문장(80자 이내)의 간결한 매매 방향성 피드백을 한국어로 작성해줘.
조언이 아닌 관찰 기반 코멘트 톤으로, 구체적 수치를 인용해서 작성해.

- VIX 지수: {vix:.1f} ({vix_state})
- 포트폴리오 베타: {portfolio_beta:.2f}
- 오늘 포트폴리오 변동률: {today_chg_pct:+.2f}%
- 주도 섹터(1일): {sector_summary}

출력은 텍스트 1~2문장만, 따옴표나 마크다운 없이."""

    return call_claude(prompt, "claude-haiku-4-5-20251001", 200)


# ── 데일리 브리프 생성 ───────────────────────────────────────────────────────────

def generate_daily_brief(
    holdings: dict,
    price_data: dict,
    macro_data: dict,
    news_items: list[dict],
) -> str:
    """Claude Sonnet으로 월가 스타일 데일리 브리프 마크다운 생성."""
    if not ANTHROPIC_API_KEY:
        return "ANTHROPIC_API_KEY 미설정"

    holdings_summary = _format_portfolio(holdings)

    price_lines = []
    for t, d in price_data.items():
        chg = d.get("chg_pct", 0)
        price_lines.append(f"  {t}: ${d.get('price', 0):.2f} ({chg:+.2f}%) | P&L: {d.get('pnl_pct', 0):+.2f}%")
    price_block = "\n".join(price_lines) if price_lines else "  (데이터 없음)"

    top_news = "\n".join(f"  - [{n['ticker']}] {n['title']}" for n in news_items[:8])

    prompt = f"""당신은 월가 톱 헤지펀드의 포트폴리오 매니저입니다.
아래 데이터를 바탕으로 오늘의 포트폴리오 브리프를 작성하세요.

# 포트폴리오
{holdings_summary}

# 오늘의 등락
{price_block}

# 매크로 지표
- Fed Rate: {macro_data.get('fed_rate', 'N/A')}%
- 10Y/2Y: {macro_data.get('y10', 'N/A')}/{macro_data.get('y2', 'N/A')} (스프레드: {macro_data.get('spread_10_2', 'N/A')}%p)
- VIX: (시장 데이터 참조)

# 주요 뉴스
{top_news if top_news else '  (없음)'}

---

# 출력 형식 (마크다운)
**📊 데일리 브리프 — {TODAY}**

## 포트폴리오 총평
(3~4문장: 오늘 전체 등락 원인 분석)

## 주목 종목
(등락 상위/하위 2~3개, 원인 한 줄씩)

## 매크로 헤드업
(금리/달러/VIX 흐름이 포트폴리오에 미치는 영향 1~2문장)

## 내일 주시 포인트
(구체적인 1~2가지 모니터링 포인트)

---
*본 브리프는 AI 자동 생성 참고용으로, 투자 조언이 아닙니다.*"""

    return call_claude(prompt, "claude-sonnet-4-6", 1200)

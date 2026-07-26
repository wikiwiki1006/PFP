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

ANTHROPIC_API_KEY   = os.getenv("ANTHROPIC_API_KEY", "")
PERPLEXITY_API_KEY  = os.getenv("PERPLEXITY_API_KEY", "")
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
    market_snapshot: str = "",
) -> list[dict]:
    prev = "\n\n---\n\n".join([r for r in (prev_results or []) if r])[-context_limit:]
    mkt = market_snapshot if market_snapshot else "yfinance 데이터 수집 실패 — 웹 검색으로 보완 필요"
    return [
        {
            "id": 1, "label": "이벤트 분석", "max_tokens": 1200, "use_search": True,
            "search_prompt": f"""오늘({TODAY}) 다음 이벤트에 관한 최신 뉴스 3~5건을 검색해 요약해주세요: {ev}
각 뉴스의 출처·날짜·핵심 내용을 포함하세요.""",
            "prompt": f"""당신은 거시경제 분석 전문가입니다. 오늘 날짜: {TODAY}

분석할 이벤트: {ev}

아래 형식으로 한국어로 작성하세요. 전문 용어는 반드시 괄호 안에 쉬운 설명을 추가하세요.
일반 투자자도 이해할 수 있는 쉬운 말을 사용하세요.

## 🔍 이벤트 성격
어떤 종류의 충격인지 한 줄로 설명 (예: 중앙은행 정책 변화, 지정학적 위기, 원자재 공급 충격 등)

## 📊 현재 시장 상황 (실시간 데이터)
{mkt}

## 🌐 영향을 받는 나라/지역
어느 나라와 산업이 가장 먼저 타격을 받는지

## ⚠️ 핵심 변수 (3~5개)
이 이벤트에서 가장 중요하게 봐야 할 것들을 번호로 나열

## 🚨 긴급도
높음 / 보통 / 낮음 — 이유를 한 문장으로

## 🔗 어떻게 시장에 영향이 전달되나
쉬운 말로 2~3문장: 이 이벤트 → 어떤 경로로 → 주가/금리/환율에 영향"""
        },
        {
            "id": 2, "label": "역사적 유사 사례", "max_tokens": 1450, "use_search": False,
            "prompt": f"""당신은 금융 역사 전문가입니다. 오늘: {TODAY}
이벤트: {ev}
앞선 분석: {prev}

비슷한 역사적 사례 2~3개를 찾아 한국어로 설명하세요.
일반 투자자가 읽기 쉽게, 전문 용어는 풀어서 쓰세요.

각 사례마다 아래 형식:

### [사례 이름] (연도)
**유사도:** X/100점 — 왜 비슷한지 한 줄

**당시 상황:** 1~2문장으로 쉽게 설명

**시장 반응:**
- 주식시장(S&P500): 얼마나 떨어졌고 회복까지 얼마나 걸렸는지
- 유가/금리: 어떻게 변했는지

**이때 배운 교훈:** 한 줄

---

## 결론: 가장 비슷한 사례
1~2문장으로, 지금 상황에 어떻게 적용할 수 있는지"""
        },
        {
            "id": 3, "label": "시장 반응", "max_tokens": 1200, "use_search": True,
            "search_prompt": f"""오늘({TODAY}) 이벤트({ev}) 관련 시장 반응과 전문가 전망을 검색해주세요:
1. 주요 투자은행(골드만삭스, 모건스탠리 등)의 이 이벤트에 대한 최신 코멘트
2. 이 이벤트로 인한 최근 시장 변동 뉴스 2~3건""",
            "prompt": f"""당신은 시장 분석가입니다. 오늘: {TODAY}
이벤트: {ev}
앞선 분석: {prev}

한국어로 작성하세요. 어려운 용어는 쉽게 풀어서 쓰세요.

## 📈 현재 시장 출발점 (실시간 데이터)
{mkt}

## ⏱️ 단기 반응 (지금~4주)
- 주식시장이 어느 범위에서 움직일지
- 달러, 금, 금리 방향
- "공포지수(VIX)"가 얼마나 오를지 (높을수록 시장 불안)

## 📅 중기 흐름 (1~3개월)
반등 가능성과 조건, 계속 하락하는 시나리오

## 🔭 장기 방향 (3~12개월)
구조적으로 어느 방향으로 가는지

## 🔄 이런 이벤트 때 반복되는 패턴 2가지
과거에 이런 상황에서 항상 나타났던 현상을 쉽게 설명"""
        },
        {
            "id": 4, "label": "섹터 영향 분석", "max_tokens": 1450, "use_search": False,
            "prompt": f"""당신은 산업 분석가입니다. 오늘: {TODAY}
이벤트: {ev}
앞선 분석: {prev}

한국어로 작성하세요. 어려운 용어는 쉽게 풀어서 쓰세요.

## 혜택을 받는 산업 TOP 3

| 순위 | 산업 | 대표 종목 | 예상 수익률 | 이유 | 일시적/구조적 |
|------|------|-----------|------------|------|--------------|
| 1 | (산업명) | (티커) | (범위) | (한 줄) | (구분) |
| 2 | ... | ... | ... | ... | ... |
| 3 | ... | ... | ... | ... | ... |

## 피해를 받는 산업 TOP 3

| 순위 | 산업 | 대표 종목 | 예상 하락률 | 이유 | 회복까지 기간 |
|------|------|-----------|------------|------|-------------|
| 1 | (산업명) | (티커) | (범위) | (한 줄) | (기간) |
| 2 | ... | ... | ... | ... | ... |
| 3 | ... | ... | ... | ... | ... |

## 투자자들이 자주 저지르는 실수
이런 이벤트 때 본능적으로 하지만 틀린 판단 한 가지를 쉽게 설명"""
        },
        {
            "id": 5, "label": "현재 vs 과거 비교", "max_tokens": 1200, "use_search": False,
            "prompt": f"""당신은 거시경제 전략가입니다. 오늘: {TODAY}
이벤트: {ev}
앞선 분석: {prev}

한국어로 작성하세요. 쉬운 말로 비교해주세요.

## 과거 사례와 지금의 공통점 (2~3가지)
각각 한 문장씩, 왜 비슷한지

## 결정적으로 다른 점 3가지

### 1. 금리 환경
과거에는 어땠고, 지금 2025년은 어떻게 다른지

### 2. 글로벌 공급망
세계화가 약해지고 각국이 자국 중심으로 바뀐 점이 어떻게 영향을 주는지

### 3. AI·반도체
AI와 반도체가 새로운 변수로 등장한 점이 어떻게 다른지

## 결론
과거 데이터를 그대로 적용할 수 없는 이유를 쉽게 1~2문장으로"""
        },
        {
            "id": 6, "label": "투자 전략", "max_tokens": 1850, "use_search": False,
            "prompt": f"""당신은 헤지펀드 최고투자책임자(CIO)입니다. 오늘: {TODAY}
이벤트: {ev}
앞선 분석: {prev}

한국어로 작성하세요. 투자 초보자도 이해할 수 있게 쉽게 설명하세요.

## 지금 당장 (0~1개월)
**사야 할 것:** 종목/ETF, 매수 시점 조건, 포트폴리오 비중 몇 %
**줄이거나 팔아야 할 것:** 종목, 매도 시점 조건

## 단기 전략 (1~3개월)
핵심 포지션 2개, 왜 유리한지 쉬운 말로, 언제 청산할지

## 중장기 테마 (3~12개월)
앞으로 뜰 구조적 테마, 관련 종목, 목표 수익률 범위

## 사고 파는 페어 전략
**살 것 2개:** 종목 + 이유 (왜 이 이벤트에서 유리한지)
**팔 것 2개:** 종목 + 이유 (왜 이 이벤트에서 불리한지)

## 자산 배분 제안
현금 / 채권 / 주식 / 원자재 / 금 = 합계 100%로 숫자 제시, 이유 한 줄씩

## 아이디어별 확신도
0~100점으로 각 아이디어에 점수를 매기고 이유 한 줄"""
        },
        {
            "id": 7, "label": "리스크 관리", "max_tokens": 1450, "use_search": False,
            "prompt": f"""당신은 최고리스크관리책임자(CRO)입니다. 오늘: {TODAY}
이벤트: {ev}
앞선 전략 분석: {prev}

한국어로 작성하세요. 위험 요소를 쉬운 말로 설명하세요.

## 전략이 틀릴 수 있는 상황 (2~3가지)
이 분석이 완전히 빗나가는 구체적인 조건을 하나씩 설명

## 3가지 시나리오

| 시나리오 | 확률 | 조건 | 6개월 후 주식시장 | 대응 방법 |
|---------|------|------|-----------------|---------|
| 낙관 (상승) | ?% | 어떤 조건이 갖춰지면 | S&P +?% 예상 | 어떻게 포지션 |
| 기본 (중립) | ?% | 현재 흐름 지속 시 | S&P ±?% 예상 | 현재 유지 |
| 비관 (하락) | ?% | 악재가 겹치면 | S&P -?% 예상 | 방어 전략 |

## 손절 기준
구체적인 가격이나 지표 수준을 명시 (예: S&P500이 X 아래로 내려가면)

## 극단적 위험 대비 방법 (테일리스크 헤지)
최악의 상황에 대비해 1~2가지 구체적인 보험 수단을 쉽게 설명"""
        },
        {
            "id": 8, "label": "포트폴리오 액션", "max_tokens": 1850, "use_search": False,
            "prompt": f"""당신은 개인 투자 자문가입니다. 오늘: {TODAY}
매크로 이벤트: {ev}
전체 분석 내용: {prev}

투자자의 현재 보유 종목:
{portfolio_str}

위 이벤트가 각 보유 종목에 어떤 영향을 주는지 분석하고,
각 종목마다 구체적인 행동 제안을 아래 JSON 배열로만 반환하세요.
(마크다운, 설명 텍스트 없이 JSON만)

reason 필드는 반드시 한국어 1문장으로, 쉬운 말로 이유를 설명하세요.
action: "매수" / "매도" / "유지" / "일부 매도" 중 하나로 작성하세요.
urgency: "즉시" / "1개월 내" / "3개월 내" 중 하나.

[
  {{
    "ticker": "AAPL",
    "action": "유지",
    "reason": "이 이벤트의 영향이 크지 않아 현 포지션 유지가 적절합니다.",
    "urgency": "3개월 내"
  }}
]"""
        },
        {
            "id": 9, "label": "최종 판정", "max_tokens": 2600, "use_search": False,
            "prompt": f"""당신은 거시경제 종합 분석 전문가입니다. 오늘: {TODAY}
분석 이벤트: {ev}
전체 분석 요약: {prev}

아래 형식으로 한국어 마크다운으로 최종 판정을 작성하세요.
일반 투자자가 이해하기 쉽게, 전문 용어는 반드시 풀어서 설명하세요.
각 시나리오는 구체적인 수치·조건을 포함해 실질적으로 도움이 되도록 작성하세요.

## ⚖️ 기준 시나리오 (가장 가능성 높은 전망)
**발생 가능성:** 약 XX%

**핵심 내용:** 가장 일어날 법한 전개를 2~3문장으로 쉽게 설명하세요.

**포트폴리오 영향:** 보유 종목에 미치는 구체적 영향을 1~2문장으로 설명하세요.

**대응 전략:** 지금 당장 또는 단기적으로 취해야 할 행동을 1~2문장으로 제안하세요.

---

## 🔴 위험 시나리오 (하락 위험)
**발생 가능성:** 약 XX%

**핵심 내용:** 최악의 경우 어떤 일이 발생할 수 있는지 2~3문장으로 설명하세요.

**포트폴리오 영향:** 손실 규모와 가장 타격 받는 종목을 1~2문장으로 설명하세요.

**대응 전략:** 손실을 줄이기 위한 리스크 관리 방법을 1~2문장으로 제안하세요.

---

## 🟢 낙관 시나리오 (상승 기회)
**발생 가능성:** 약 XX%

**핵심 내용:** 시장이 예상보다 잘 흘러갈 경우의 전개를 2~3문장으로 설명하세요.

**포트폴리오 영향:** 수혜 받는 종목과 기대할 수 있는 상승 폭을 1~2문장으로 설명하세요.

**대응 전략:** 이 기회를 잡기 위해 취할 수 있는 행동을 1~2문장으로 제안하세요.

---

## 📌 최종 결론
지금 이 이벤트에서 투자자가 가장 중요하게 챙겨야 할 한 가지 메시지를 2~3문장으로 작성하세요."""
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

def _fetch_market_snapshot() -> str:
    """yfinance로 현재 시장 지표를 실시간 수집. 실패 시 빈 문자열 반환."""
    try:
        import yfinance as yf
        import pandas as pd
        symbols = {
            "S&P500":          "^GSPC",
            "KOSPI":           "^KS11",
            "USD/KRW 환율":    "USDKRW=X",
            "WTI 원유":        "CL=F",
            "10년물 국채금리": "^TNX",
            "VIX 공포지수":    "^VIX",
            "달러인덱스(DXY)": "DX-Y.NYB",
            "금(Gold)":        "GC=F",
        }
        tickers = list(symbols.values())
        df = yf.download(tickers, period="2d", auto_adjust=True, progress=False)
        if df.empty:
            return ""
        close = (df["Close"] if isinstance(df.columns, pd.MultiIndex) else df).ffill()
        latest = close.iloc[-1]
        lines = []
        for label, sym in symbols.items():
            if sym not in latest.index:
                continue
            val = latest[sym]
            if pd.isna(val):
                continue
            if "국채금리" in label or "VIX" in label or "DXY" in label:
                lines.append(f"- {label}: {val:.2f}")
            elif "환율" in label:
                lines.append(f"- {label}: {val:,.0f}원")
            elif "원유" in label or "Gold" in label:
                lines.append(f"- {label}: ${val:.1f}")
            else:
                lines.append(f"- {label}: {val:,.0f}")
        return "\n".join(lines) if lines else ""
    except Exception:
        return ""


def _call_perplexity_search(prompt: str) -> str:
    """Perplexity sonar로 실시간 웹 검색 전담. PERPLEXITY_API_KEY 없으면 빈 문자열 반환."""
    if not PERPLEXITY_API_KEY:
        return ""
    try:
        import requests as _req
        resp = _req.post(
            "https://api.perplexity.ai/chat/completions",
            headers={
                "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "sonar",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 1200,
                "temperature": 0.0,
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception:
        return ""


def call_claude(prompt: str, model: str, max_tokens: int,
                use_search: bool = False, web_context: str = "") -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    full_prompt = (
        f"[Perplexity 실시간 웹 검색 결과]\n{web_context}\n\n---\n\n{prompt}"
        if web_context else prompt
    )
    kwargs = dict(
        model=model,
        max_tokens=max_tokens,
        system=(
            "각 섹션을 완전하게 작성하되, 토큰 한도 내에서 자연스럽게 마무리하세요. "
            "글이 도중에 끊기지 않도록 마지막 섹션은 간결하게 압축해서라도 완결된 문장으로 끝내세요."
        ),
        messages=[{"role": "user", "content": full_prompt}],
    )
    # Perplexity가 웹 데이터를 제공했으면 Claude 검색 툴 불필요 (비용 절감)
    if use_search and not web_context:
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
    market_snapshot: str = "",
) -> dict[int, tuple[str, float]]:
    """Phase 1: 선택된 에이전트를 컨텍스트 없이 병렬 실행."""
    all_agents = _build_agents(ev, portfolio_str, prev_results=[], market_snapshot=market_snapshot)
    agent_map = {a["id"]: a for a in all_agents if a["id"] in selected_ids}

    results: dict[int, tuple[str, float]] = {}

    def _call(ag: dict):
        import time
        t0 = time.time()
        try:
            web_ctx = ""
            if ag.get("use_search") and ag.get("search_prompt"):
                web_ctx = _call_perplexity_search(ag["search_prompt"])
            text = call_claude(
                ag["prompt"], model, ag["max_tokens"],
                use_search=ag.get("use_search", False) and not web_ctx,
                web_context=web_ctx,
            )
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
    market_snapshot: str = "",
) -> dict[int, tuple[str, float]]:
    """Phase 2: Phase 1 결과를 컨텍스트로 받아 에이전트를 순차 실행 (agents 8, 9)."""
    all_agents = _build_agents(ev, portfolio_str, prev_results=context_texts,
                               context_limit=PHASE2_CONTEXT_LIMIT, market_snapshot=market_snapshot)
    agent_map = {a["id"]: a for a in all_agents if a["id"] in selected_ids}

    results: dict[int, tuple[str, float]] = {}
    for ag_id in sorted(selected_ids):
        if ag_id not in agent_map:
            continue
        ag = agent_map[ag_id]
        import time
        t0 = time.time()
        try:
            web_ctx = ""
            if ag.get("use_search") and ag.get("search_prompt"):
                web_ctx = _call_perplexity_search(ag["search_prompt"])
            text = call_claude(
                ag["prompt"], model, ag["max_tokens"],
                use_search=ag.get("use_search", False) and not web_ctx,
                web_context=web_ctx,
            )
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

    # yfinance 실시간 시장 데이터 사전 수집 (에이전트 1·3에 주입)
    market_snapshot = _fetch_market_snapshot()

    phase1_ids = [i for i in selected_ids if i <= 7]
    phase2_ids = [i for i in selected_ids if i > 7]

    all_results: dict[int, tuple[str, float]] = {}

    # Phase 1: 병렬 독립 실행
    if phase1_ids:
        all_results.update(_run_parallel_agents(phase1_ids, event, portfolio_str, model, market_snapshot))

    # Phase 2: Phase 1 컨텍스트 기반 순차 실행
    if phase2_ids:
        p1_texts = [
            all_results[i][0] for i in sorted(phase1_ids)
            if i in all_results and not all_results[i][0].startswith("[오류")
        ]
        all_results.update(_run_contextual_agents(phase2_ids, event, portfolio_str, model, p1_texts, market_snapshot))

    # 정의된 에이전트 순서 기준으로 반환 목록 구성
    all_agents = _build_agents(event, portfolio_str, market_snapshot=market_snapshot)
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

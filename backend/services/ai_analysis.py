"""
services/ai_analysis.py
────────────────────────
시장 지표 수집: yfinance + FRED (신뢰성 있는 실제 수치)
뉴스·서사 수집: Perplexity sonar (최신 뉴스·전문가 코멘트)
단순 분류·사실 나열: Claude Haiku (에이전트 1·2·4·5)
심층 분석·종합 판단: Claude Sonnet (에이전트 3·6·7·8·9)
"""
from __future__ import annotations

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import anthropic
import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY", "")
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY", "")
TODAY = datetime.now().strftime("%Y년 %m월 %d일")
CONTEXT_CHAR_LIMIT   = 3200   # 20% 절감
PHASE2_CONTEXT_LIMIT = 8000   # 20% 절감

ANALYSIS_MODES = {
    "fast":     [1, 6, 9],
    "standard": [1, 3, 6, 8, 9],
    "full":     [1, 2, 3, 4, 5, 6, 7, 8, 9],
}

MODEL_OPTIONS = {
    "sonnet": "claude-sonnet-4-6",
    "haiku":  "claude-haiku-4-5-20251001",
}

# 에이전트별 기본 모델 티어
# "haiku": 단순 분류·사실 나열 / "sonnet": 심층 분석·전략·종합
# user가 "haiku"를 선택하면 전체 haiku로 오버라이드됨
_AGENT_MODEL_TIER: dict[int, str] = {
    1: "haiku",   # 이벤트 분석 — 사실 정리
    2: "haiku",   # 역사적 유사 사례 — 단순 검색
    3: "sonnet",  # 시장 반응 전망 — 복합 예측
    4: "haiku",   # 섹터 영향 분석 — 분류 작업
    5: "haiku",   # 현재 vs 과거 비교 — 단순 비교
    6: "sonnet",  # 투자 전략 — 핵심 전략 판단
    7: "sonnet",  # 리스크 관리 — 시나리오 분석
    8: "sonnet",  # 포트폴리오 액션 — 개인화 판단
    9: "sonnet",  # 최종 판정 — 종합 결론
}


# ── yfinance + FRED: 시장 지표 수집 ──────────────────────────────────────────

def gather_yfinance_market_data() -> str:
    """DB 캐시 우선, 핵심 지수 누락 시 직접 yfinance 다운로드로 시장 지표 수집.
    배경 스레드에서 실행되므로 블로킹 다운로드 가능."""
    try:
        import pandas as pd
        from backend.services.market_data import get_fred_macro

        PRICE_TICKERS: list[tuple[str, str, str, str]] = [
            ("^GSPC",    "S&P 500",          ",.0f",  ""),
            ("^DJI",     "다우존스",           ",.0f",  ""),
            ("^IXIC",    "Nasdaq",            ",.0f",  ""),
            ("^RUT",     "Russell 2000",      ",.0f",  ""),
            ("^KS11",    "KOSPI",             ",.0f",  ""),
            ("^N225",    "Nikkei 225",        ",.0f",  ""),
            ("^VIX",     "VIX 공포지수",      ".2f",   ""),
            ("^TNX",     "미국 10년물 금리",   ".3f",   "%"),
            ("^IRX",     "미국 3개월물 금리",  ".3f",   "%"),
            ("DX-Y.NYB", "달러인덱스(DXY)",   ".2f",   ""),
            ("USDKRW=X", "USD/KRW",           ",.0f",  "원"),
            ("USDJPY=X", "USD/JPY",           ".2f",   "엔"),
            ("CL=F",     "WTI 원유",          ".2f",   "$/bbl"),
            ("GC=F",     "금(Gold)",          ",.0f",  "$/oz"),
            ("BTC-USD",  "Bitcoin",           ",.0f",  "$"),
        ]
        SECTOR_TICKERS: list[tuple[str, str]] = [
            ("XLK", "기술(XLK)"),
            ("XLF", "금융(XLF)"),
            ("XLE", "에너지(XLE)"),
            ("XLV", "헬스케어(XLV)"),
            ("XLI", "산업재(XLI)"),
            ("XLU", "유틸리티(XLU)"),
        ]
        all_price_tickers  = [t for t, *_ in PRICE_TICKERS]
        all_sector_tickers = [t for t, _ in SECTOR_TICKERS]
        all_tickers = all_price_tickers + all_sector_tickers

        lines = [f"【현재 시장 지표 — yfinance】  기준: {TODAY}"]
        cur_price:  dict[str, float] = {}
        prev_price: dict[str, float] = {}
        data_source = ""

        # 1) DB 캐시 시도
        try:
            from backend.db import is_available
            from backend.db.market_cache import get_prices_from_db
            from backend.services.market_data import ALWAYS_FETCH
            if is_available():
                df_cache = get_prices_from_db(ALWAYS_FETCH, "5d")
                if df_cache is not None and not df_cache.empty:
                    # 주말 행 제거: 장이 열리지 않는 날은 ffill 값과 동일해 0% 변동률 오류 발생
                    df_cache = df_cache[df_cache.index.dayofweek < 5]
                if df_cache is not None and not df_cache.empty and len(df_cache) >= 2:
                    cur_row  = df_cache.iloc[-1]
                    prev_row = df_cache.iloc[-2]
                    for t in all_tickers:
                        if t in cur_row.index:
                            c = cur_row.get(t)
                            p = prev_row.get(t)
                            if c is not None and not pd.isna(c):
                                cur_price[t] = float(c)
                            if p is not None and not pd.isna(p):
                                prev_price[t] = float(p)
                    if cur_price:
                        data_source = "DB 캐시"
        except Exception:
            pass

        # 2) 직접 yfinance 다운로드 (핵심 지수 누락 시)
        core_ok = all(t in cur_price for t in ("^GSPC", "^IXIC", "^KS11"))
        if not core_ok:
            try:
                import yfinance as yf
                raw = yf.download(
                    all_tickers, period="5d", auto_adjust=True,
                    progress=False, threads=True,
                )
                if raw is not None and not raw.empty and "Close" in raw.columns:
                    closes = raw["Close"]
                    # 주말(토·일) 행 제거 — yfinance가 NaN 행을 반환하거나 ffill 시 0% 변동률 오류 방지
                    closes = closes[closes.index.dayofweek < 5]
                    if len(closes) >= 2:
                        cur_row  = closes.iloc[-1]
                        prev_row = closes.iloc[-2]
                        for t in all_tickers:
                            if t in closes.columns:
                                c = cur_row.get(t)
                                p = prev_row.get(t)
                                if c is not None and not pd.isna(c):
                                    cur_price[t] = float(c)
                                if p is not None and not pd.isna(p):
                                    prev_price[t] = float(p)
                        if cur_price:
                            data_source = "yfinance 직접"
            except Exception:
                pass

        if not cur_price:
            lines.append("  ⚠️ 시장 가격 데이터 수집 실패 — 네트워크 오류 또는 DB 미초기화")
        else:
            lines.append(f"  (데이터 출처: {data_source})")
            lines.append("")
            lines.append("  [주요 지수 및 자산]")
            for t, name, fmt, unit in PRICE_TICKERS:
                c = cur_price.get(t)
                p = prev_price.get(t)
                if c is None:
                    continue
                try:
                    chg = (c / p - 1) * 100 if p else 0.0
                    lines.append(f"  {name}: {c:{fmt}}{unit} (전일비 {chg:+.2f}%)")
                except Exception:
                    lines.append(f"  {name}: {c:{fmt}}{unit}")

            lines.append("")
            lines.append("  [섹터 ETF 전일 등락률]")
            for t, name in SECTOR_TICKERS:
                c = cur_price.get(t)
                p = prev_price.get(t)
                if c is None or p is None:
                    continue
                try:
                    chg = (c / p - 1) * 100
                    lines.append(f"  {name}: {chg:+.2f}%")
                except Exception:
                    pass

        # FRED 거시 지표
        try:
            fred = get_fred_macro(ttl=3600)
            if fred.get("source") != "fallback":
                lines.append("")
                lines.append("【FRED 거시경제 지표】")
                lines.append(f"  연방기금금리: {fred['fed_rate']:.2f}%")
                lines.append(f"  실업률: {fred['unemployment']:.1f}%")
                lines.append(f"  CPI(전년비): {fred['cpi']:.1f}%")
                lines.append(f"  GDP 성장률(최근분기): {fred['gdp']:.1f}%")
                lines.append(f"  10Y-2Y 금리차: {fred['t10y2y']:+.3f}%p")
                lines.append(f"  HY 스프레드: {fred['bamlh0a0hym2']:.0f}bp")
        except Exception:
            pass

        return "\n".join(lines)

    except Exception as exc:
        return f"(시장 데이터 수집 오류: {exc})"


# ── Perplexity: 뉴스·서사 수집 전담 ─────────────────────────────────────────

def _call_perplexity(prompt: str, max_tokens: int = 1200) -> str:
    """Perplexity sonar로 실시간 웹 검색. 실패 시 빈 문자열 반환."""
    if not PERPLEXITY_API_KEY:
        return ""
    try:
        resp = requests.post(
            "https://api.perplexity.ai/chat/completions",
            headers={
                "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "sonar",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": 0.0,
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception:
        return ""


def gather_perplexity_context(ev: str) -> str:
    """Perplexity로 이벤트 관련 최신 뉴스·전문가 코멘트만 수집.
    시장 수치는 yfinance에서 별도로 가져오므로 여기서는 서사·뉴스만 요청한다."""
    prompt = f"""오늘 날짜: {TODAY}
분석 이벤트: {ev}

아래 정보를 한국어로 수집해주세요. 수치(지수 레벨·금리)는 적지 말고 뉴스와 코멘트 위주로 작성하세요.

【이벤트 관련 최신 뉴스 (최근 48시간)】
- 주요 언론 보도 3~5건 (제목·출처·날짜·핵심 요약 각 1문장)
- 골드만삭스·모건스탠리 등 투자은행·분석가 코멘트 (있을 경우)
- 이 이벤트로 직접 영향을 받은 자산·국가·기업 (시장 서사 위주)"""
    return _call_perplexity(prompt, max_tokens=1200)


def gather_context(ev: str) -> str:
    """yfinance(시장 지표) + Perplexity(뉴스)를 합쳐 에이전트 컨텍스트 반환."""
    market_data = gather_yfinance_market_data()
    news_data   = gather_perplexity_context(ev)

    parts = [market_data]
    if news_data.strip():
        parts.append("")
        parts.append("【이벤트 관련 최신 뉴스·전문가 코멘트 (Perplexity)】")
        parts.append(news_data)

    return "\n".join(parts)


# ── Claude: 분석·출력 전담 ────────────────────────────────────────────────────

_CLAUDE_SYSTEM = (
    "각 섹션을 완전하게 작성하되 토큰 한도 내에서 자연스럽게 마무리하세요. "
    "전체 분량을 섹션별로 고르게 배분해 마지막 섹션도 완결된 문장으로 끝내세요. "
    "⚠️ 사용자가 입력한 이벤트는 실제로 발생한 사실이 아닌 가상 시나리오(Virtual Scenario)입니다. "
    "'만약 이러한 상황이 발생한다면'의 조건부 관점에서 분석하고, "
    "실제 시장 수치(지수·금리 등)는 제공된 yfinance 데이터를 사용하세요."
)


def call_claude(prompt: str, model: str, max_tokens: int, perplexity_ctx: str = "") -> str:
    """Claude 호출. 토큰 한도로 잘린 경우 후속 호출로 마무리 문장을 복구한다."""
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    full_prompt = (
        f"[시장 데이터·뉴스 — 분석에 활용하세요]\n{perplexity_ctx}\n\n---\n\n{prompt}"
        if perplexity_ctx else prompt
    )
    msg = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=_CLAUDE_SYSTEM,
        messages=[{"role": "user", "content": full_prompt}],
    )
    text = "".join(
        block.text for block in msg.content
        if getattr(block, "type", None) == "text"
    )

    # 토큰 한도로 잘린 경우: 끊긴 마지막 문장만 완성
    if msg.stop_reason == "max_tokens" and text.strip():
        try:
            fix = client.messages.create(
                model=model,
                max_tokens=200,
                system=_CLAUDE_SYSTEM,
                messages=[
                    {"role": "user",      "content": full_prompt},
                    {"role": "assistant", "content": text},
                    {"role": "user",      "content": (
                        "위 텍스트가 토큰 한도로 중간에 끊겼습니다. "
                        "끊긴 마지막 문장만 한두 문장으로 자연스럽게 완성해 주세요. "
                        "새 섹션이나 추가 내용은 쓰지 마세요."
                    )},
                ],
            )
            tail = "".join(
                block.text for block in fix.content
                if getattr(block, "type", None) == "text"
            )
            if tail.strip():
                text += tail
        except Exception:
            pass  # 복구 실패 시 원본 텍스트 반환

    return text


# ── 에이전트 프롬프트 정의 ────────────────────────────────────────────────────

def _build_agents(
    ev: str,
    portfolio_str: str,
    prev_results: list[str] | None = None,
    context_limit: int = CONTEXT_CHAR_LIMIT,
) -> list[dict]:
    prev = "\n\n---\n\n".join([r for r in (prev_results or []) if r])[-context_limit:]
    ev = f"[가상 시나리오] {ev}"  # 사용자 입력은 가상 시나리오임을 명시
    return [
        {
            "id": 1, "label": "이벤트 분석", "max_tokens": 1200, "inject_perplexity": True,
            "prompt": f"""당신은 거시경제 분석 전문가입니다. 오늘 날짜: {TODAY}

분석할 이벤트: {ev}

위에 제공된 시장 지표와 뉴스 데이터를 바탕으로 아래 형식으로 한국어 분석을 작성하세요.
전문 용어는 반드시 괄호 안에 용어 설명을 추가하세요.

## 🔍 이벤트 성격
어떤 종류의 충격인지 한 줄로 설명 (예: 중앙은행 정책 변화, 지정학적 위기, 원자재 공급 충격 등)

## 📊 현재 시장 상황
제공된 시장 지표(S&P500·KOSPI·VIX·금리·환율 등)의 현재 레벨 정리

## 🌐 영향을 받는 나라/지역
어느 나라와 산업이 가장 먼저 타격을 받는지

## ⚠️ 핵심 변수 (3~5개)
이 이벤트에서 가장 중요하게 봐야 할 것들을 번호로 나열

## 🚨 긴급도
높음 / 보통 / 낮음 — 이유를 한 문장으로

## 🔗 시장 전달 경로
이 이벤트가 어떤 경로로 주가/금리/환율에 영향을 미치는지 2~3문장으로""",
        },
        {
            "id": 2, "label": "역사적 유사 사례", "max_tokens": 1150, "inject_perplexity": False,
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
1~2문장으로, 지금 상황에 어떻게 적용할 수 있는지""",
        },
        {
            "id": 3, "label": "시장 반응 전망", "max_tokens": 950, "inject_perplexity": True,
            "prompt": f"""당신은 시장 분석가입니다. 오늘: {TODAY}
이벤트: {ev}
앞선 분석: {prev}

위에 제공된 시장 지표와 뉴스 데이터를 바탕으로 한국어로 분석하세요.

## 📈 현재 시장 출발점
제공된 데이터에서 S&P500, KOSPI, VIX, 금리, 환율 현재값 정리

## ⏱️ 단기 반응 (지금~4주)
- 주식시장이 어느 범위에서 움직일지
- 달러, 금, 금리 방향
- VIX(공포지수)가 얼마나 오를지

## 📅 중기 흐름 (1~3개월)
반등 가능성과 조건, 계속 하락하는 시나리오

## 🔭 장기 방향 (3~12개월)
구조적으로 어느 방향으로 가는지

## 🔄 이런 이벤트 때 반복되는 패턴 2가지
과거에 이런 상황에서 항상 나타났던 현상을 쉽게 설명""",
        },
        {
            "id": 4, "label": "섹터 영향 분석", "max_tokens": 1150, "inject_perplexity": False,
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
이런 이벤트 때 본능적으로 하지만 틀린 판단 한 가지를 쉽게 설명""",
        },
        {
            "id": 5, "label": "현재 vs 과거 비교", "max_tokens": 950, "inject_perplexity": False,
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
과거 데이터를 그대로 적용할 수 없는 이유를 1~2문장으로""",
        },
        {
            "id": 6, "label": "투자 전략", "max_tokens": 2000, "inject_perplexity": False,
            "prompt": f"""당신은 헤지펀드 최고투자책임자(CIO)입니다. 오늘: {TODAY}
이벤트: {ev}
앞선 분석: {prev}

한국어로 작성하세요. 일반 투자자가 이해할 수 있도록 명확하게 설명하세요. 2000토큰 내에서 글이 잘리지 않도록 작성하세요.

## 지금 당장 (0~1개월)
**사야 할 것:** 종목/ETF, 매수 시점 조건, 포트폴리오 비중 전체 100%대비 각각 몇 %
**줄이거나 팔아야 할 것:** 종목, 매도 시점 조건

## 단기 전략 (1~3개월)
핵심 포지션 2개, 왜 유리한지, 언제 청산할지

## 중장기 테마 (3~12개월)
앞으로 뜰 구조적 테마, 관련 종목, 목표 수익률 범위

## 사고 파는 페어 전략
**살 것 2개:** 종목 + 이유
**팔 것 2개:** 종목 + 이유

## 자산 배분 제안
현금 / 채권 / 주식 / 원자재 / 금 = 합계 100%로 숫자 제시, 이유 한 줄씩

## 아이디어별 확신도
0~100점으로 각 아이디어에 점수를 매기고 이유 한 줄""",
        },
        {
            "id": 7, "label": "리스크 관리", "max_tokens": 1150, "inject_perplexity": False,
            "prompt": f"""당신은 최고리스크관리책임자(CRO)입니다. 오늘: {TODAY}
이벤트: {ev}
앞선 전략 분석: {prev}

한국어로 작성하세요. 위험 요소를 설명하세요.

## 전략이 틀릴 수 있는 상황 (2~3가지)
이 분석이 완전히 빗나가는 구체적인 조건을 하나씩 설명

## 3가지 시나리오

| 시나리오 | 확률 | 조건 | 6개월 후 주식시장 | 대응 방법 |
|---------|------|------|-----------------|---------|
| 낙관 (상승) | ?% | 어떤 조건이 갖춰지면 | S&P +?% 예상 | 어떻게 포지션 |
| 기본 (중립) | ?% | 현재 흐름 지속 시 | S&P ±?% 예상 | 현재 유지 |
| 비관 (하락) | ?% | 악재가 겹치면 | S&P -?% 예상 | 방어 전략 |

## 손절 기준
구체적인 가격이나 지표 수준을 명시

## 극단적 위험 대비 방법 (테일리스크 헤지)
최악의 상황에 대비해 1~2가지 구체적인 보험 수단을 명확하게 설명""",
        },
        {
            "id": 8, "label": "포트폴리오 액션", "max_tokens": 1500, "inject_perplexity": False,
            "prompt": f"""당신은 개인 투자 자문가입니다. 오늘: {TODAY}
매크로 이벤트: {ev}
전체 분석 내용: {prev}

투자자의 현재 보유 종목:
{portfolio_str}

위 이벤트가 각 보유 종목에 어떤 영향을 주는지 분석하고,
각 종목마다 구체적인 행동 제안을 아래 JSON 배열로만 반환하세요.
(마크다운, 설명 텍스트 없이 JSON만)

action: "매수" / "매도" / "유지" / "일부 매도" 중 하나.
urgency: "즉시" / "1개월 내" / "3개월 내" 중 하나.
reason: 한국어 1문장.

[
  {{
    "ticker": "AAPL",
    "action": "유지",
    "reason": "이 이벤트의 영향이 제한적이므로 현 포지션 유지가 적절합니다.",
    "urgency": "3개월 내"
  }}
]""",
        },
        {
            "id": 9, "label": "최종 판정", "max_tokens": 2100, "inject_perplexity": False,
            "prompt": f"""당신은 거시경제 종합 분석 전문가입니다. 오늘: {TODAY}
분석 이벤트: {ev}
전체 분석 요약: {prev}

아래 형식의 JSON만 반환하세요. 마크다운, 설명 텍스트 없이 JSON만.
한국어로 작성하되, 일반 투자자가 이해하기 쉽게 쓰세요.

각 카드는 서로 다른 시나리오를 다뤄야 합니다 (기본·낙관·비관·테일리스크).
"details"는 2~3문장(120자 이내)으로 간결하게.

{{
  "cards": [
    {{
      "title": "시나리오 제목 (짧게, 예: 기본 시나리오)",
      "icon": "이모지 1개 (예: ⚖️, 📉, 🟢, ⚡)",
      "color": "danger 또는 warning 또는 success 또는 info 중 하나",
      "headline": "핵심 한 줄 (30자 이내)",
      "summary": "1줄 요약 (40자 이내)",
      "details": "2~3문장, 120자 이내. 포트폴리오에 미치는 구체적 영향 포함."
    }}
  ]
}}

규칙:
- "cards"는 정확히 4개.
- "color"는 danger / warning / success / info 중 하나 (소문자).
- JSON은 반드시 {{ 로 시작하고 }} 로 끝나야 합니다. 중간에 끊기면 안 됩니다.""",
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


# ── 2단계 파이프라인 실행 ─────────────────────────────────────────────────────

def _resolve_model(ag_id: int, user_model_key: str) -> str:
    """에이전트 ID와 사용자 선택 모델로 실제 모델 ID 결정.
    user_model_key == "haiku" → 전체 haiku (비용 절감 오버라이드)
    user_model_key == "sonnet" → 에이전트 티어에 따라 haiku/sonnet 분기
    """
    if user_model_key == "haiku":
        return MODEL_OPTIONS["haiku"]
    tier = _AGENT_MODEL_TIER.get(ag_id, "sonnet")
    return MODEL_OPTIONS[tier]


def _run_parallel_agents(
    selected_ids: list[int],
    ev: str,
    portfolio_str: str,
    model_key: str,
    perplexity_ctx: str,
) -> dict[int, tuple[str, float]]:
    """Phase 1: 선택된 에이전트를 컨텍스트 없이 병렬 실행."""
    all_agents = _build_agents(ev, portfolio_str, prev_results=[])
    agent_map = {a["id"]: a for a in all_agents if a["id"] in selected_ids}

    results: dict[int, tuple[str, float]] = {}

    def _call(ag: dict):
        t0 = time.time()
        try:
            model = _resolve_model(ag["id"], model_key)
            ctx = perplexity_ctx if ag.get("inject_perplexity") else ""
            text = call_claude(ag["prompt"], model, ag["max_tokens"], perplexity_ctx=ctx)
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
    model_key: str,
    context_texts: list[str],
    perplexity_ctx: str,
) -> dict[int, tuple[str, float]]:
    """Phase 2: Phase 1 결과를 컨텍스트로 받아 순차 실행 (agents 8, 9)."""
    all_agents = _build_agents(ev, portfolio_str, prev_results=context_texts,
                               context_limit=PHASE2_CONTEXT_LIMIT)
    agent_map = {a["id"]: a for a in all_agents if a["id"] in selected_ids}

    results: dict[int, tuple[str, float]] = {}
    for ag_id in sorted(selected_ids):
        if ag_id not in agent_map:
            continue
        ag = agent_map[ag_id]
        t0 = time.time()
        try:
            model = _resolve_model(ag_id, model_key)
            ctx = perplexity_ctx if ag.get("inject_perplexity") else ""
            text = call_claude(ag["prompt"], model, ag["max_tokens"], perplexity_ctx=ctx)
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
    Perplexity로 실시간 정보 수집 → Claude로 2단계 분석.
    Phase 1 (id ≤ 7): 병렬 독립 분석
    Phase 2 (id > 7): Phase 1 전체 결과를 컨텍스트로 순차 종합
    """
    selected_ids = ANALYSIS_MODES.get(mode, ANALYSIS_MODES["fast"])
    portfolio_str = _format_portfolio(portfolio)

    # Pre-phase: yfinance로 시장 지표 + Perplexity로 뉴스 수집
    perplexity_ctx = gather_context(event)

    phase1_ids = [i for i in selected_ids if i <= 7]
    phase2_ids = [i for i in selected_ids if i > 7]
    all_results: dict[int, tuple[str, float]] = {}

    if phase1_ids:
        all_results.update(
            _run_parallel_agents(phase1_ids, event, portfolio_str, model_key, perplexity_ctx)
        )

    if phase2_ids:
        p1_texts = [
            all_results[i][0] for i in sorted(phase1_ids)
            if i in all_results and not all_results[i][0].startswith("[오류")
        ]
        all_results.update(
            _run_contextual_agents(phase2_ids, event, portfolio_str, model_key, p1_texts, perplexity_ctx)
        )

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


# ── Final Verdict JSON 파싱 ───────────────────────────────────────────────────

def parse_verdict_cards(raw_text: str) -> list[dict] | None:
    VALID_COLORS = {"danger", "warning", "success", "info"}

    def _normalize(cards: list[dict]) -> list[dict]:
        for c in cards:
            if c.get("color") not in VALID_COLORS:
                c["color"] = "info"
        return cards

    try:
        match = re.search(r"\{.*\}", raw_text, re.DOTALL)
        if match:
            data = json.loads(match.group())
            cards = data.get("cards", [])
            if isinstance(cards, list) and cards:
                return _normalize(cards)
    except Exception:
        pass

    # 잘린 JSON 부분 복구
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


# ── AI Analyst 실시간 피드백 ──────────────────────────────────────────────────

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


# ── 데일리 브리프 생성 ────────────────────────────────────────────────────────

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

    return call_claude(prompt, "claude-haiku-4-5-20251001", 1200)

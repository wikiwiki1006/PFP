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
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import anthropic
import requests
from dotenv import load_dotenv

from backend.services.job_store import JobCancelled

load_dotenv(Path(__file__).parent.parent / ".env")

logger = logging.getLogger(__name__)

ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY", "")
GPT_API_KEY        = os.getenv("GPT_API_KEY", "")
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

def build_macro_block(market: str) -> str:
    """그 시장의 거시지표를 프롬프트에 넣을 블록으로 만든다.

    한 곳에만 둔다. 이전에는 시장 분기가 `gather_yfinance_market_data` 에만
    있고 `/daily-brief` 경로에는 없어서, 한국 브리프가 연준 금리와 미 국채
    스프레드를 근거로 쓰였다 — 통화 포맷이 두 곳에 갈라져 한쪽만 고쳐졌던
    것과 같은 형태다.

    값이 없는 항목은 수치로 넣지 않는다. 'N/A' 를 넣으면 모델이 그걸 수치처럼
    인용한다 (`korea_macro.format_for_prompt` 와 같은 이유). 대신 **무엇이
    없는지는 적는다** — 이름 없이 빼면 모델은 그 지표를 물어보지 않은 것과
    구별하지 못하고 사전지식으로 메운다.

    **빈 문자열을 돌려주지 않는다.** 돌려주면 매크로 섹션이 프롬프트에서 통째로
    사라지는데, 생략은 모델에게 '데이터 없음' 이 아니라 지시가 아예 없는 것이라
    빈 자리를 기억으로 채운다 (§1.3·C2). 호출자가 각자 보완하게 두면 한쪽만
    보완한다 — 실제로 `gather_yfinance_market_data` 는 조용히 건너뛰고
    `generate_daily_brief` 는 실패 줄을 넣고 있었다.
    """
    from backend.services.markets import normalize

    is_kr = normalize(market) == "KR"
    title = "[한국 거시지표]" if is_kr else "[US macro indicators]"

    def _failed(why: str) -> str:
        note = (f"  (수집 실패 — {why}. 거시지표 수치를 인용하거나 추정하지 마세요.)"
                if is_kr else
                f"  (unavailable — {why}. Do not cite or infer macro figures.)")
        return f"{title}\n{note}"

    try:
        if is_kr:
            from backend.services.korea_macro import get_korea_macro, format_for_prompt
            block = format_for_prompt(get_korea_macro(ttl=3600))
            if not block.strip():
                logger.warning("한국 거시지표를 하나도 받지 못했다")
                return _failed("한국은행 지표를 하나도 받지 못했습니다")
            return block

        from backend.services.market_data import get_fred_macro
        fred = get_fred_macro(ttl=3600)
        # 폴백은 FRED 를 못 읽었을 때 쓰는 하드코딩 값이다. 실제 관측치인 척
        # 프롬프트에 넣지 않는다.
        if fred.get("source") == "fallback":
            logger.warning("FRED 거시지표 폴백 — 실측값 없이 진행")
            return _failed("FRED lookup failed")

        rows = [
            ("Fed funds",               fred.get("fed_rate"),        "%",  "{:.2f}"),
            ("Unemployment",            fred.get("unemployment"),    "%",  "{:.1f}"),
            ("CPI YoY",                 fred.get("cpi"),             "%",  "{:.1f}"),
            ("GDP growth (latest qtr)", fred.get("gdp"),             "%",  "{:.1f}"),
            ("10Y-2Y spread",           fred.get("t10y2y"),          "pp", "{:+.3f}"),
            ("HY spread",               fred.get("bamlh0a0hym2"),    "bp", "{:.0f}"),
        ]
        lines = [f"  {label}: {fmt.format(value)}{unit}"
                 for label, value, unit, fmt in rows if value is not None]
        if not lines:
            logger.warning("FRED 거시지표를 하나도 받지 못했다")
            return _failed("no FRED figures came back")

        block = f"{title}\n" + "\n".join(lines)
        # 일부만 받았으면 그 사실을 적는다. 행을 조용히 빼면 모델은 '못 받은
        # 지표' 와 '애초에 안 넣은 지표' 를 구별하지 못해 기억으로 메운다.
        #
        # 빠진 지표의 **이름은 적지 않는다.** 이름만 적힌 줄이 값처럼 읽힐 여지를
        # 두지 않으려는 것이고(B2), 모델에게 필요한 지시는 "여기 있는 것만
        # 인용하라" 라서 이름이 없어도 성립한다.
        #
        # 생산자의 `missing` 을 믿지 않고 행 값에서 직접 본다 — 한때 부분 폴백이
        # `source: "FRED"` 로 나가 위 가드를 통과했다.
        if any(value is None for _, value, *_ in rows):
            logger.warning("FRED 거시지표 일부 없음 (market=%s)", market)
            block += ("\n  (some indicators were unavailable and are omitted;"
                      " cite only what is listed above, do not infer the rest.)")
        return block
    except Exception:
        logger.warning("거시지표 수집 실패 (market=%s)", market, exc_info=True)
        return _failed("한국은행 지표 조회가 오류로 끝났습니다" if is_kr
                       else "collection raised")


def gather_yfinance_market_data(market: str = "US") -> str:
    """DB 캐시 우선, 핵심 지수 누락 시 직접 yfinance 다운로드로 시장 지표 수집.
    배경 스레드에서 실행되므로 블로킹 다운로드 가능."""
    try:
        import pandas as pd

        PRICE_TICKERS: list[tuple[str, str, str, str]] = [
            ("^GSPC",    "S&P 500",          ",.0f",  ""),
            ("^DJI",     "Dow Jones",        ",.0f",  ""),
            ("^IXIC",    "Nasdaq",            ",.0f",  ""),
            ("^RUT",     "Russell 2000",      ",.0f",  ""),
            ("^KS11",    "KOSPI",             ",.0f",  ""),
            ("^N225",    "Nikkei 225",        ",.0f",  ""),
            ("^VIX",     "VIX",              ".2f",   ""),
            ("^TNX",     "미국 10년물 금리",   ".3f",   "%"),
            ("^IRX",     "미국 3개월물 금리",  ".3f",   "%"),
            ("DX-Y.NYB", "DXY",              ".2f",   ""),
            ("USDKRW=X", "USD/KRW",           ",.0f",  "원"),
            ("USDJPY=X", "USD/JPY",           ".2f",   "엔"),
            ("CL=F",     "WTI Crude",        ".2f",   "$/bbl"),
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

        # 시장에 맞게 재구성한다.
        #
        # 프롬프트로 "한국 관점에서 쓰라"고 해도, 건네주는 데이터가 S&P500 을
        # 맨 위에 두고 섹터가 전부 미국 ETF 면 모델은 그 숫자를 근거로 답한다.
        # 실제로 한국 시나리오 결과에 KOSPI 는 0회, S&P·NVDA 만 나왔다.
        # 근거 데이터부터 그 시장 것으로 바꿔야 한다.
        from backend.services.markets import get_market as _gm, normalize as _nz
        if _nz(market) == "KR":
            _spec = _gm("KR")
            _kr_first = [
                ("^KS11",    "KOSPI",            ",.0f", ""),
                ("^KQ11",    "KOSDAQ",           ",.2f", ""),
                ("USDKRW=X", "원/달러 환율",       ",.0f", "원"),
                ("^KS200",   "KOSPI200",         ",.2f", ""),
            ]
            # 해외 지표는 참고용으로 뒤에 남긴다 — 한국 증시는 미국장·환율에
            # 크게 연동되므로 아예 빼면 인과를 설명할 수 없다.
            _kept = {"^GSPC", "^IXIC", "^VIX", "^TNX", "CL=F", "USDJPY=X"}
            PRICE_TICKERS = _kr_first + [r for r in PRICE_TICKERS if r[0] in _kept]
            SECTOR_TICKERS = [(etf, f"{label}({etf})") for label, etf in _spec.sector_etfs[:6]]
        all_price_tickers  = [t for t, *_ in PRICE_TICKERS]
        all_sector_tickers = [t for t, _ in SECTOR_TICKERS]
        all_tickers = all_price_tickers + all_sector_tickers

        lines = [f"[Current market data — yfinance] as of {TODAY}"]
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
            lines.append("  (market price data unavailable — network error or DB not ready)")
        else:
            lines.append(f"  (source: {data_source})")
            lines.append("")
            lines.append("  [Indices & assets]")
            for t, name, fmt, unit in PRICE_TICKERS:
                c = cur_price.get(t)
                p = prev_price.get(t)
                if c is None:
                    continue
                # 전일가가 없으면 등락을 쓰지 않는다. '+0.00% d/d' 는 모델에게
                # '보합' 이지 '모름' 이 아니다 — 그 지수가 안 움직였다는 근거로
                # 답을 쓰게 된다 (§1.3a).
                try:
                    if p:
                        chg = (c / p - 1) * 100
                        lines.append(f"  {name}: {c:{fmt}}{unit} ({chg:+.2f}% d/d)")
                    else:
                        lines.append(f"  {name}: {c:{fmt}}{unit} (d/d 불명 — 전일 종가 없음)")
                except Exception:
                    lines.append(f"  {name}: {c:{fmt}}{unit}")

            lines.append("")
            lines.append("  [Sector ETF 1d change]")
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

        # 거시 지표 — 시장에 맞는 것을 넣는다.
        # 한국 시나리오에 Fed 금리·미국 실업률을 넣으면 모델이 그걸 근거로
        # 한국 시장을 논하게 된다.
        macro_block = build_macro_block(market)
        if macro_block:
            lines.append("")
            lines.append(macro_block)

        return "\n".join(lines)

    except Exception as exc:
        return f"(시장 데이터 수집 오류: {exc})"


# ── Perplexity: 뉴스·서사 수집 전담 ─────────────────────────────────────────

def _call_perplexity(prompt: str, max_tokens: int = 1200, market: str = "US") -> str:
    """Perplexity sonar로 실시간 웹 검색. 실패 시 빈 문자열 반환.

    한국이면 검색 범위를 국내 경제지로 좁힌다 — 출처가 미국 매체뿐이면
    프롬프트로 관점만 바꿔 봐야 한국 이야기가 나오지 않는다.
    """
    from backend.services import perplexity
    return perplexity.search(prompt, market=market, max_tokens=max_tokens,
                             label="macro-news")


def gather_perplexity_context(ev: str, market: str = "US") -> str:
    """Perplexity로 이벤트 관련 최신 뉴스·전문가 코멘트만 수집.
    시장 수치는 yfinance에서 별도로 가져오므로 여기서는 서사·뉴스만 요청한다.

    검색 방향도 시장을 따른다. 그러지 않으면 로이터·골드만 같은 미국 매체
    기사만 모이고, 모델은 그 재료로 답을 쓴다 — 프롬프트에 "한국 관점으로
    쓰라"고 해도 근거가 미국뿐이면 한국 이야기가 나올 수 없다.
    """
    from backend.services.markets import normalize
    is_kr = normalize(market) == "KR"

    # 영어로 수집 — 이 텍스트는 그대로 Claude 입력이 되며, 같은 내용도
    # 한국어는 영어의 약 3배 토큰을 쓴다 (실측 2,308 → 791).
    focus = (
        """
[Focus: SOUTH KOREA]
- Prioritise Korean market impact: KOSPI/KOSDAQ, KRW/USD, exports, foreign investor flows
- Name **Korea-listed companies** affected (Samsung Electronics, SK Hynix, Hyundai Motor, etc.)
- Include Korean press and analyst views (Korea Economic Daily, Maeil Business, local brokerages)
- Mention US/global items only where they transmit into the Korean market"""
        if is_kr else
        """
[Focus: UNITED STATES]
- Prioritise US market impact and US-listed companies"""
    )
    prompt = f"""Today: {TODAY}
Event to analyze: {ev}

Collect the following **in English**, concise bullet points.
Do NOT quote index levels or rates — narrative and commentary only.
{focus}

[Latest news on this event, last 48 hours]
- 3-5 major press items (title, source, date, one-sentence summary each)
- Sell-side commentary if available
- Assets, countries and companies directly affected (market narrative)"""
    return _call_perplexity(prompt, max_tokens=1200, market=market)


def gather_context(ev: str, market: str = "US") -> str:
    """yfinance(시장 지표) + Perplexity(뉴스)를 합쳐 에이전트 컨텍스트 반환."""
    market_data = gather_yfinance_market_data(market)
    news_data   = gather_perplexity_context(ev, market)

    parts = [market_data]
    if news_data.strip():
        parts.append("")
        parts.append("[Latest news & expert commentary — Perplexity]")
        parts.append(news_data)
    else:
        # 뉴스 블록을 조용히 빼면, 받아 본 모델은 뉴스가 없다는 사실 자체를
        # 알 수 없어 "최근 보도에 따르면" 같은 서술을 그대로 쓴다. 없다는
        # 것을 적어 두면 모델도 사용자도 그 리포트의 근거 범위를 안다.
        parts.append("")
        parts.append("[News unavailable]")
        parts.append(
            "  News collection failed or returned nothing for this run. "
            "Base the analysis on the market data above only. "
            "Do NOT cite recent news, press coverage or analyst commentary, "
            "and state plainly that current news was unavailable."
        )

    return "\n".join(parts)


# ── Claude: 분석·출력 전담 ────────────────────────────────────────────────────

# 지시는 영어, 출력은 한국어 — 토큰을 아끼면서 사용자 화면은 그대로 유지한다.
_CLAUDE_SYSTEM = (
    "**Write your entire answer in Korean.** These instructions are in English only.\n"
    "Complete every section, finishing naturally within the token limit. "
    "Spread the length evenly across sections so the last one also ends in a complete sentence.\n"
    "IMPORTANT: the user's event is a HYPOTHETICAL scenario, not a reported fact. "
    "Analyze it conditionally (\"if this were to happen\"). "
    "For real market figures (index levels, rates), use the provided yfinance data."
)


_GPT_URL = "https://api.openai.com/v1/chat/completions"
_GPT_HEADERS = lambda: {
    "Authorization": f"Bearer {GPT_API_KEY}",
    "Content-Type": "application/json",
}


def call_gpt(prompt: str, max_tokens: int, perplexity_ctx: str = "") -> str:
    """OpenAI GPT REST API 호출. openai 패키지 불필요."""
    if not GPT_API_KEY:
        return "[GPT API 키 없음 — .env에 GPT_API_KEY 추가 필요]"
    full_prompt = (
        f"[시장 데이터·뉴스 — 분석에 활용하세요]\n{perplexity_ctx}\n\n---\n\n{prompt}"
        if perplexity_ctx else prompt
    )
    messages = [
        {"role": "system", "content": _CLAUDE_SYSTEM},
        {"role": "user",   "content": full_prompt},
    ]
    payload = {
        "model": "gpt-5-mini",
        "messages": messages,
        "max_completion_tokens": max_tokens,
        "temperature": 1,
    }
    try:
        resp = requests.post(_GPT_URL, json=payload, headers=_GPT_HEADERS(), timeout=180)
        if not resp.ok:
            return f"[GPT 오류: {resp.status_code} — {resp.text[:300]}]"
        data = resp.json()
        choices = data.get("choices", [])
        if not choices:
            return "[GPT 응답 없음]"
        msg_obj = choices[0].get("message", {})
        text = msg_obj.get("content", "") or ""
        if not text:
            import json as _json
            fr = choices[0].get("finish_reason", "unknown")
            refusal = msg_obj.get("refusal") or ""
            print(f"[GPT DEBUG] content empty. finish_reason={fr} "
                  f"refusal={refusal[:200]} message_keys={list(msg_obj.keys())} "
                  f"usage={data.get('usage')} raw={_json.dumps(choices[0], ensure_ascii=False)[:600]}")
            if refusal:
                return f"[GPT 거부 응답: {refusal[:300]}]"
            return f"[GPT 빈 응답 — finish_reason: {fr}]"
        # 토큰 한도로 잘린 경우: 끊긴 마지막 문장만 완성
        if choices[0].get("finish_reason") == "length" and text.strip():
            try:
                fix_payload = {**payload, "messages": messages + [
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": (
                        "위 텍스트가 토큰 한도로 중간에 끊겼습니다. "
                        "끊긴 마지막 문장만 한두 문장으로 자연스럽게 완성해 주세요. "
                        "새 섹션이나 추가 내용은 쓰지 마세요."
                    )},
                ], "max_completion_tokens": 200}
                fix_resp = requests.post(_GPT_URL, json=fix_payload, headers=_GPT_HEADERS(), timeout=60)
                if fix_resp.ok:
                    fix_choices = fix_resp.json().get("choices", [])
                    if fix_choices:
                        tail = fix_choices[0].get("message", {}).get("content", "") or ""
                        if tail.strip():
                            text += tail
            except Exception:
                pass
        return text
    except Exception as exc:
        return f"[GPT 오류: {exc}]"


def call_claude(prompt: str, model: str, max_tokens: int, perplexity_ctx: str = "",
                should_cancel: Optional[Callable[[], bool]] = None) -> str:
    """Claude 호출. 토큰 한도로 잘린 경우 후속 호출로 마무리 문장을 복구한다.

    스트리밍으로 받는다 — 응답을 통째로 기다리면 사용자가 중단을 눌러도
    그 호출이 끝날 때까지 멈출 수 없다. 조각마다 취소를 확인하고,
    취소면 JobCancelled 를 올려 연결을 끊는다.
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    full_prompt = (
        f"[시장 데이터·뉴스 — 분석에 활용하세요]\n{perplexity_ctx}\n\n---\n\n{prompt}"
        if perplexity_ctx else prompt
    )

    def _check() -> None:
        if should_cancel is not None and should_cancel():
            raise JobCancelled()

    def _stream(**kw) -> tuple[str, Optional[str]]:
        parts: list[str] = []
        _check()
        with client.messages.stream(**kw) as st:
            for piece in st.text_stream:
                _check()
                parts.append(piece)
            stop_reason = st.get_final_message().stop_reason
        return "".join(parts), stop_reason

    text, stop_reason = _stream(
        model=model,
        max_tokens=max_tokens,
        system=_CLAUDE_SYSTEM,
        messages=[{"role": "user", "content": full_prompt}],
    )

    # 토큰 한도로 잘린 경우: 끊긴 마지막 문장만 완성
    if stop_reason == "max_tokens" and text.strip():
        # 끊긴 문장을 잇는 데 원본 프롬프트 전체를 되보낼 이유가 없다.
        # 예전 방식은 출력 80~200 토큰을 얻으려고 입력 3~4천 토큰을 썼다.
        try:
            tail, _ = _stream(
                model=model,
                max_tokens=200,
                messages=[
                    {"role": "user", "content": (
                        "Below is the tail of a Korean analysis that was cut off mid-sentence "
                        "by a token limit. Write ONLY the few words or one sentence needed to "
                        "finish that last incomplete sentence naturally, in Korean. "
                        "Do not repeat the text, do not add new sections or headings.\n\n"
                        f"---\n{text[-600:]}"
                    )},
                ],
            )
            if tail.strip():
                text += tail
        except JobCancelled:
            raise
        except Exception:
            pass  # 복구 실패 시 원본 텍스트 반환

    return text


# ── 에이전트 프롬프트 정의 ────────────────────────────────────────────────────

def _build_agents(
    ev: str,
    portfolio_str: str,
    prev_results: list[str] | None = None,
    context_limit: int = CONTEXT_CHAR_LIMIT,
    market: str = "US",
) -> list[dict]:
    prev = "\n\n---\n\n".join([r for r in (prev_results or []) if r])[-context_limit:]
    ev = f"[가상 시나리오] {ev}"  # 사용자 입력은 가상 시나리오임을 명시

    # 시장 관점. 프롬프트에 S&P500 을 박아 두면 한국 시나리오인데도 모델이
    # 미국 지수를 기준으로 답하고, 종목 예시도 미국 기업을 든다.
    from backend.services.markets import get_market
    spec = get_market(market)
    is_kr = spec.code == "KR"
    idx_main   = "KOSPI" if is_kr else "S&P500"
    idx_list   = "KOSPI·KOSDAQ·원/달러 환율·국고채 금리" if is_kr else "S&P500·NASDAQ·VIX·미 국채 금리"
    stance = (
        "**분석 관점: 한국 주식시장.** 지수는 KOSPI/KOSDAQ 기준으로 말하고, "
        "금액은 원화로 씁니다. 종목을 예로 들 때는 **국내 상장 종목만** 사용하세요 "
        "(삼성전자·SK하이닉스·현대차 등). 미국 종목이나 달러 금액을 예시로 들지 마세요. "
        "환율·수출·외국인 수급처럼 한국 시장에 실제로 작동하는 경로를 우선 다루세요."
        if is_kr else
        "**분석 관점: 미국 주식시장.** 지수는 S&P500/NASDAQ 기준으로, 금액은 달러로 씁니다. "
        "종목 예시는 미국 상장 종목을 사용하세요."
    )
    return [
        {
            "id": 1, "label": "이벤트 분석", "max_tokens": 1200, "inject_perplexity": True,
            "prompt": f"""{stance}\n\n당신은 거시경제 분석 전문가입니다. 오늘 날짜: {TODAY}

분석할 이벤트: {ev}

위에 제공된 시장 지표와 뉴스 데이터를 바탕으로 아래 형식으로 한국어 분석을 작성하세요.
전문 용어는 반드시 괄호 안에 용어 설명을 추가하세요.

## 🔍 이벤트 성격
어떤 종류의 충격인지 한 줄로 설명 (예: 중앙은행 정책 변화, 지정학적 위기, 원자재 공급 충격 등)

## 📊 현재 시장 상황
제공된 시장 지표({idx_list} 등)의 현재 레벨 정리

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
            "prompt": f"""{stance}\n\n당신은 금융 역사 전문가입니다. 오늘: {TODAY}
이벤트: {ev}
앞선 분석: {prev}

비슷한 역사적 사례 2~3개를 찾아 한국어로 설명하세요.
일반 투자자가 읽기 쉽게, 전문 용어는 풀어서 쓰세요.

각 사례마다 아래 형식:

### [사례 이름] (연도)
**유사도:** X/100점 — 왜 비슷한지 한 줄

**당시 상황:** 1~2문장으로 쉽게 설명

**시장 반응:**
- 주식시장({idx_main}): 얼마나 떨어졌고 회복까지 얼마나 걸렸는지
- 유가/금리: 어떻게 변했는지

**이때 배운 교훈:** 한 줄

---

## 결론: 가장 비슷한 사례
1~2문장으로, 지금 상황에 어떻게 적용할 수 있는지""",
        },
        {
            "id": 3, "label": "시장 반응 전망", "max_tokens": 1500, "inject_perplexity": True,
            "prompt": f"""{stance}\n\n당신은 거시경제 리서치 전문가입니다. 오늘: {TODAY}
이벤트: {ev}
앞선 분석: {prev}

제공된 시장 지표와 뉴스 데이터를 참고해, 이 시나리오가 발생할 경우 시장에 어떤 영향을 줄 수 있는지 한국어로 분석하세요.

## 📈 현재 시장 출발점
제공된 데이터 기준 {idx_list} 현황 정리

## ⏱️ 단기 영향 분석 (이벤트 후 4주)
- 주식시장에 미칠 수 있는 영향 및 리스크 요인
- 달러·금·금리 등 안전자산 수요 변화 가능성
- 시장 변동성(VIX) 변화 요인 분석

## 📅 중기 시나리오 (1~3개월)
안정화 조건과 추가 하락 요인을 시나리오별로 구분해 분석

## 🔭 장기 구조적 영향 (3~12개월)
이 이벤트가 거시경제 구조에 미치는 중장기 함의

## 🔄 유사 과거 사례 패턴 2가지
역사적으로 비슷한 상황에서 반복된 시장 패턴을 쉽게 설명""",
        },
        {
            "id": 4, "label": "섹터 영향 분석", "max_tokens": 1150, "inject_perplexity": False,
            "prompt": f"""{stance}\n\n당신은 산업 분석가입니다. 오늘: {TODAY}
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
            "prompt": f"""{stance}\n\n당신은 거시경제 전략가입니다. 오늘: {TODAY}
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
            "prompt": f"""{stance}\n\n당신은 헤지펀드 최고투자책임자(CIO)입니다. 오늘: {TODAY}
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
            "prompt": f"""{stance}\n\n당신은 최고리스크관리책임자(CRO)입니다. 오늘: {TODAY}
이벤트: {ev}
앞선 전략 분석: {prev}

한국어로 작성하세요. 위험 요소를 설명하세요.

## 전략이 틀릴 수 있는 상황 (2~3가지)
이 분석이 완전히 빗나가는 구체적인 조건을 하나씩 설명

## 3가지 시나리오

| 시나리오 | 확률 | 조건 | 6개월 후 주식시장 | 대응 방법 |
|---------|------|------|-----------------|---------|
| 낙관 (상승) | ?% | 어떤 조건이 갖춰지면 | {idx_main} +?% 예상 | 어떻게 포지션 |
| 기본 (중립) | ?% | 현재 흐름 지속 시 | {idx_main} ±?% 예상 | 현재 유지 |
| 비관 (하락) | ?% | 악재가 겹치면 | {idx_main} -?% 예상 | 방어 전략 |

## 손절 기준
구체적인 가격이나 지표 수준을 명시

## 극단적 위험 대비 방법 (테일리스크 헤지)
최악의 상황에 대비해 1~2가지 구체적인 보험 수단을 명확하게 설명""",
        },
        {
            "id": 8, "label": "포트폴리오 액션", "max_tokens": 1500, "inject_perplexity": False,
            "prompt": f"""{stance}\n\n당신은 개인 투자 자문가입니다. 오늘: {TODAY}
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
            "prompt": f"""{stance}\n\n당신은 거시경제 종합 분석 전문가입니다. 오늘: {TODAY}
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


def _format_portfolio(holdings: dict, market: str) -> str:
    """보유 종목을 프롬프트용 텍스트로 적는다.

    `market` 에 기본값을 두지 않는다. 통화를 빠뜨린 호출부가 조용히 달러가
    되는 것이 이 함수에서 실제로 일어난 일이다 — 원화 금액에 `$` 가 붙어
    나갔고 모델은 그 숫자를 달러로 읽었다. 인자를 빠뜨리면 TypeError 로
    즉시 드러나는 편이 낫다.

    포맷은 `report_writer` 의 것을 그대로 쓴다. 같은 규칙을 두 곳에 따로
    구현한 탓에 한쪽만 고쳐지고 이쪽이 남아 있었다 (§1.4 의 '$333605.94B').
    """
    from backend.services.markets import get_market
    from backend.services.report_writer import _fmt_price

    cur = get_market(market).currency

    if not holdings:
        return "포트폴리오 없음"
    lines = []
    for t, info in holdings.items():
        if t == "CASH":
            lines.append(f"CASH: {_fmt_price(info.get('q'), cur)}")
        else:
            lines.append(
                f"{t}: {info['q']} sh @ avg {_fmt_price(info.get('avg'), cur)} "
                f"(sector: {info.get('sector', '-')})"
            )
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
    provider: str = "claude",
    should_cancel: Optional[Callable[[], bool]] = None,
    market: str = "US",
) -> dict[int, tuple[str, float]]:
    """Phase 1: 선택된 에이전트를 컨텍스트 없이 병렬 실행.

    취소되면 아직 시작하지 않은 에이전트는 건너뛰고, 진행 중인 것은
    스트리밍 도중 스스로 멈춘다.
    """
    all_agents = _build_agents(ev, portfolio_str, prev_results=[], market=market)
    agent_map = {a["id"]: a for a in all_agents if a["id"] in selected_ids}

    results: dict[int, tuple[str, float]] = {}

    def _call(ag: dict):
        t0 = time.time()
        if should_cancel is not None and should_cancel():
            # 큐에서 대기하다 취소된 에이전트 — LLM 을 부르지 않고 끝낸다.
            return ag["id"], "[취소됨]", 0.0
        try:
            if provider == "gpt":
                # GPT는 항상 전체 컨텍스트(yfinance+뉴스)를 주입해 데이터 누락 방지
                # GPT-5.6 Sol은 reasoning 모델이라 내부 추론 토큰이 max_completion_tokens에 포함됨
                # → 에이전트 토큰 예산을 4× 확장해 출력 공간을 확보 (최소 4000)
                # gpt-5-mini도 reasoning 토큰 소비 — 3× (min 4000)으로 출력 여유 확보.
                # Agent 6은 4000 캡 (전략 에이전트, 간결한 출력 선호).
                gpt_tokens = 4000 if ag["id"] == 6 else max(4000, ag["max_tokens"] * 3)
                text = call_gpt(ag["prompt"], gpt_tokens, perplexity_ctx=perplexity_ctx)
            else:
                ctx = perplexity_ctx if ag.get("inject_perplexity") else ""
                model = _resolve_model(ag["id"], model_key)
                text = call_claude(ag["prompt"], model, ag["max_tokens"], perplexity_ctx=ctx,
                                   should_cancel=should_cancel)
            return ag["id"], text, time.time() - t0
        except JobCancelled:
            return ag["id"], "[취소됨]", time.time() - t0
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
    provider: str = "claude",
    should_cancel: Optional[Callable[[], bool]] = None,
    market: str = "US",
) -> dict[int, tuple[str, float]]:
    """Phase 2: Phase 1 결과를 컨텍스트로 받아 순차 실행 (agents 8, 9)."""
    all_agents = _build_agents(ev, portfolio_str, prev_results=context_texts,
                               context_limit=PHASE2_CONTEXT_LIMIT, market=market)
    agent_map = {a["id"]: a for a in all_agents if a["id"] in selected_ids}

    results: dict[int, tuple[str, float]] = {}
    for ag_id in sorted(selected_ids):
        if ag_id not in agent_map:
            continue
        ag = agent_map[ag_id]
        t0 = time.time()
        # 순차 실행이라 남은 에이전트를 그냥 건너뛰면 된다.
        if should_cancel is not None and should_cancel():
            raise JobCancelled()
        try:
            if provider == "gpt":
                gpt_tokens = max(4000, ag["max_tokens"] * 3)
                text = call_gpt(ag["prompt"], gpt_tokens, perplexity_ctx=perplexity_ctx)
            else:
                ctx = perplexity_ctx if ag.get("inject_perplexity") else ""
                model = _resolve_model(ag_id, model_key)
                text = call_claude(ag["prompt"], model, ag["max_tokens"], perplexity_ctx=ctx,
                                   should_cancel=should_cancel)
        except JobCancelled:
            raise
        except Exception as exc:
            text = f"[오류: {exc}]"
        results[ag_id] = (text, round(time.time() - t0, 2))

    return results


def run_macro_agents(
    event: str,
    portfolio: dict,
    model_key: str = "sonnet",
    mode: str = "fast",
    provider: str = "claude",  # 무시됨 — 항상 Claude 사용
    should_cancel: Optional[Callable[[], bool]] = None,
    market: str = "US",
) -> list[dict]:
    """
    기본 분석(model_key=haiku): Claude Haiku (전체 에이전트)
    심층 분석(model_key=sonnet): 에이전트 티어에 따라 Haiku/Sonnet 자동 분기 (_AGENT_MODEL_TIER)
    Phase 1 (id ≤ 7): 병렬 독립 분석
    Phase 2 (id > 7): Phase 1 전체 결과를 컨텍스트로 순차 종합
    """
    effective_provider  = "claude"
    effective_model_key = model_key  # haiku → 전체 Haiku; sonnet → _AGENT_MODEL_TIER 분기

    selected_ids = ANALYSIS_MODES.get(mode, ANALYSIS_MODES["fast"])
    portfolio_str = _format_portfolio(portfolio, market)

    def _check() -> None:
        if should_cancel is not None and should_cancel():
            raise JobCancelled()

    # Pre-phase: yfinance로 시장 지표 + Perplexity로 뉴스 수집
    _check()
    perplexity_ctx = gather_context(event, market)
    _check()

    phase1_ids = [i for i in selected_ids if i <= 7]
    phase2_ids = [i for i in selected_ids if i > 7]
    all_results: dict[int, tuple[str, float]] = {}

    if phase1_ids:
        all_results.update(
            _run_parallel_agents(phase1_ids, event, portfolio_str, effective_model_key,
                                 perplexity_ctx, effective_provider, should_cancel, market)
        )
    _check()

    if phase2_ids:
        p1_texts = [
            all_results[i][0] for i in sorted(phase1_ids)
            if i in all_results and not all_results[i][0].startswith("[오류")
        ]
        all_results.update(
            _run_contextual_agents(phase2_ids, event, portfolio_str, effective_model_key,
                                   p1_texts, perplexity_ctx, effective_provider, should_cancel, market)
        )
    _check()

    all_agents = _build_agents(event, portfolio_str, market=market)
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
    vix: "float | None",
    portfolio_beta: "float | None",
    today_chg_pct: float,
    sector_summary: str,
    is_portfolio_sectors: bool = False,
) -> str:
    if not ANTHROPIC_API_KEY:
        return "ANTHROPIC_API_KEY 미설정"

    # VIX 도 베타와 같은 규칙을 받는다. 예전에는 `float` 만 받아서, 호출부가
    # 조회 실패를 18.0(장기 평균)이나 20.0 같은 상수로 메워 넘겨야 했다.
    # 그 값이 프롬프트에 실측처럼 실리면 모델은 '변동성 정상' 을 근거로
    # 리스크를 서술한다 — 아무도 VIX 를 못 읽었는데도 (§1.3a·B4).
    vix_line = (
        f"- VIX 지수: {vix:.1f} "
        f"({'위험' if vix >= 30 else ('주의' if vix >= 20 else '정상')})"
        if vix is not None else
        "- VIX 지수: 산출 불가 (VIX 수준이나 변동성 국면은 언급하지 말 것)"
    )
    # 베타를 못 구했으면 '1.00' 이라고 단정하지 않는다. 1.0 은 '시장과 동일하게
    # 움직인다'는 판단이라, 모르는 것을 아는 것처럼 적으면 모델이 그 전제로
    # 리스크를 서술한다.
    beta_line = (f"- 포트폴리오 베타: {portfolio_beta:.2f}"
                 if portfolio_beta is not None else
                 "- 포트폴리오 베타: 산출 불가 (베타는 언급하지 말 것)")

    if is_portfolio_sectors:
        prompt = f"""다음 데이터를 바탕으로 투자자에게 3~4문장(120자 이내)의 포트폴리오 섹터 분석 피드백을 한국어로 작성해줘.
보유 섹터의 오늘 흐름과 리스크를 관찰 기반 코멘트 톤으로, 구체적 수치를 인용해서 작성해.

{vix_line}
{beta_line}
- 오늘 포트폴리오 변동률: {today_chg_pct:+.2f}%
- 보유 섹터 비중 및 오늘 변동: {sector_summary}

출력은 텍스트 3~4문장만, 따옴표나 마크다운 없이. 보유 섹터를 중심으로 분석할 것."""
    else:
        prompt = f"""다음 데이터를 바탕으로 투자자에게 1~2문장(80자 이내)의 간결한 매매 방향성 피드백을 한국어로 작성해줘.
조언이 아닌 관찰 기반 코멘트 톤으로, 구체적 수치를 인용해서 작성해.

{vix_line}
{beta_line}
- 오늘 포트폴리오 변동률: {today_chg_pct:+.2f}%
- 주도 섹터(1일): {sector_summary}

출력은 텍스트 1~2문장만, 따옴표나 마크다운 없이."""

    return call_claude(prompt, MODEL_OPTIONS["haiku"], 1200)


# ── 데일리 브리프 생성 ────────────────────────────────────────────────────────

def generate_daily_brief(
    holdings: dict,
    price_data: dict,
    news_items: list[dict],
    market: str,
) -> str:
    """Claude Haiku로 월가 스타일 데일리 브리프 마크다운 생성.

    `market` 은 필수다 — 이 브리프의 금액 표기와 거시지표가 여기서 갈린다.
    거시지표는 `build_macro_block` 이 시장에 맞는 것을 준다. 호출자가
    FRED 를 직접 넘기던 때에는 한국 브리프도 연준 금리를 근거로 받았다.
    """
    from backend.services.markets import get_market
    from backend.services.report_writer import _fmt_price

    if not ANTHROPIC_API_KEY:
        return "ANTHROPIC_API_KEY 미설정"

    cur = get_market(market).currency
    holdings_summary = _format_portfolio(holdings, market)

    # 금액은 전부 계산해서 넘긴다. 비율만 주면 모델이 수량을 곱해 금액을
    # 지어내는데 그 산술이 틀린다 — 실측으로 1일 손익 +₩10,239 를
    # +₩688,000 으로, 총자산 ₩24,480,000 을 ₩13,350,000 으로 썼다.
    price_lines = []
    for t, d in price_data.items():
        # 값이 없으면 0 을 적지 않는다. '0.00%' 는 '보합' 이지 '모름' 이 아니고,
        # 모델은 그 차이를 알 수 없다 (§1.3).
        chg = d.get("chg_pct")
        pnl = d.get("pnl_pct")
        day_pnl = d.get("day_pnl")
        chg_str = f"{chg:+.2f}%" if chg is not None else "전일 대비 불명"
        pnl_str = f"{pnl:+.2f}%" if pnl is not None else "불명"
        day_str = _fmt_price(day_pnl, cur) if day_pnl is not None else "불명"
        price_lines.append(
            f"  {t}: {_fmt_price(d.get('price'), cur)} ({chg_str})"
            f" | 평가액 {_fmt_price(d.get('pos_val'), cur)}"
            f" | 1일 손익 {day_str}"
            f" | 누적 P&L {pnl_str}"
        )
    price_block = "\n".join(price_lines) if price_lines else "  (데이터 없음)"

    # 합계의 기준은 `price_data` 가 아니라 **보유 종목**이다. 가격을 못 받은
    # 종목은 조립부에서 통째로 빠지므로, price_data 만 보고 더하면 그 종목이
    # 없었던 것처럼 총자산이 줄어든다 — 빠졌다는 사실은 프롬프트 어디에도
    # 없어서 모델이 알 방법이 없다 (§1.3·B3).
    held     = [t for t in holdings if t != "CASH"]
    cash_val = float(holdings.get("CASH", {}).get("q") or 0)

    def _missing(field: str) -> list[str]:
        return [t for t in held if (price_data.get(t) or {}).get(field) is None]

    def _sum(field: str) -> float:
        return sum(float(price_data[t][field]) for t in held)

    no_val = _missing("pos_val")
    no_pnl = _missing("day_pnl")

    if no_val:
        # 종목 줄은 `평가액 —` 로 나간다. 여기서 남은 것만 더해 '총자산' 이라고
        # 적으면 같은 프롬프트 안에서 두 줄이 서로 다른 말을 하게 된다.
        total_line = (
            f"  주식 평가액·총자산: {', '.join(no_val)} 의 평가액이 없어 합산 불가"
            f"  ·  현금 {_fmt_price(cash_val, cur)}"
        )
    else:
        stock_val = _sum("pos_val")
        total_line = (
            f"  주식 평가액 {_fmt_price(stock_val, cur)}"
            f" + 현금 {_fmt_price(cash_val, cur)}"
            f" = 총자산 {_fmt_price(stock_val + cash_val, cur)}"
        )

    if not held:
        pass
    elif no_pnl:
        total_line += f"  ·  오늘 손익 합계: {', '.join(no_pnl)} 데이터 없음 — 합산 불가"
    else:
        total_line += f"  ·  오늘 손익 합계 {_fmt_price(_sum('day_pnl'), cur)}"

    top_news = "\n".join(f"  - [{n['ticker']}] {n['title']}" for n in news_items[:8])

    # `build_macro_block` 은 이제 빈 문자열을 돌려주지 않으므로 이 `or` 는
    # 도달하지 않는다. **죽은 코드지만 일부러 남긴다** — 그 보장이 깨지는
    # 순간(새 반환 경로가 생기거나 누가 조용히 "" 를 돌려주면) 매크로 섹션이
    # 흔적 없이 사라지고, 모델은 그 빈자리를 기억으로 메운다. 한 줄로 사는
    # 방어선이라 지우지 않는다. `test_daily_brief_fills_in_when_the_block_is_empty`
    # 가 이 동작을 고정한다.
    macro_block = build_macro_block(market) or "  (거시지표 수집 실패 — 인용하지 마세요)"

    prompt = f"""당신은 월가 톱 헤지펀드의 포트폴리오 매니저입니다.
아래 데이터를 바탕으로 오늘의 포트폴리오 브리프를 작성하세요.

# 포트폴리오
{holdings_summary}

# 오늘의 등락
{price_block}
{total_line}

금액은 위에 계산해 두었습니다. 직접 곱하거나 더해서 새 금액을 만들지 말고
그대로 인용하세요. 없는 값은 '불명' 으로 적혀 있으니 추정하지 마세요.

# 매크로 지표
{macro_block}

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

    return call_claude(prompt, MODEL_OPTIONS["haiku"], 1200)

"""
backend/services/report_writer.py
────────────────────────────────────
LENS 종목·산업 리서치 레포트 AI 집필 서비스
yfinance 실제 데이터 + Perplexity 뉴스 + Haiku 구조화 + Sonnet 분석
"""
from __future__ import annotations

import logging
import math
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import anthropic
import yfinance as yf
from dotenv import load_dotenv

from backend.services.job_store import JobCancelled

load_dotenv(Path(__file__).parent.parent / ".env")

logger = logging.getLogger(__name__)

ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY", "")
TODAY = datetime.now().strftime("%Y년 %m월 %d일")

# ── 시스템 프롬프트 ──────────────────────────────────────────────────────────────

EQUITY_SYSTEM_PROMPT = """You are the lead analyst at LENS CAPITAL RESEARCH.

**Write the entire report in Korean.** Only these instructions are in English.

Rules — follow strictly:
1. Write every specified section in order, none omitted.
2. Start each section with `## <section title>`. Never merge or skip sections.
3. Tables must be markdown (`| h | h |\n| --- | --- |\n| v | v |`).
4. Tag every figure with [A] (disclosed) or [E] (estimated).
5. Quote the provided yfinance figures verbatim; tag only your own estimates with [E].
6. Finish through the last section. If tokens run short, shorten the content but keep every section.
"""

INDUSTRY_SYSTEM_PROMPT = """You are the lead industry analyst at LENS CAPITAL RESEARCH.

**Write the entire report in Korean.** Only these instructions are in English.

Rules — follow strictly:
1. Write every specified section in order, none omitted.
2. Start each section with `## <section title>`. Never merge or skip sections.
3. Tables must be markdown (`| h | h |\n| --- | --- |\n| v | v |`).
4. Tag every figure with [A] (disclosed) or [E] (estimated).
5. Quote the provided yfinance figures verbatim; tag only your own estimates with [E].
6. Finish through the last section. If tokens run short, shorten the content but keep every section.
"""

# ── 산업 목록 ────────────────────────────────────────────────────────────────────

INDUSTRIES: dict[str, dict] = {
    "ai_infra":            {"name_kr": "AI 데이터센터 연결 인프라", "name_en": "AI Connectivity Infrastructure", "tagline": "GPU 클러스터가 커질수록, 병목은 '연결'에서 터진다", "benchmark": "SOX / NASDAQ100", "coverage": "ALAB, CRDO, NVDA, AVGO, MRVL", "icon": "🔗"},
    "space":               {"name_kr": "상업 우주 인프라", "name_en": "Commercial Space Infrastructure", "tagline": "지구 저궤도가 새로운 통신 고속도로가 된다", "benchmark": "UFO ETF", "coverage": "ASTS, RKLB, LUNR, IRDM, PL", "icon": "🚀"},
    "semiconductor_memory":{"name_kr": "메모리 반도체 & HBM", "name_en": "Memory Semiconductor & HBM", "tagline": "AI HBM 수요가 DRAM 업사이클의 새 법칙을 쓴다", "benchmark": "SOX ETF", "coverage": "MU, SMCI, LRCX, KLAC, AMAT", "icon": "💾"},
    "semiconductor_logic": {"name_kr": "파운드리 & 로직 반도체", "name_en": "Foundry & Logic Semiconductor", "tagline": "첨단 공정 독점이 미래 AI 인프라의 열쇠다", "benchmark": "SOX ETF", "coverage": "TSM, INTC, ASML, AMAT, KLAC", "icon": "⚙️"},
    "defense":             {"name_kr": "방산 & 항공우주", "name_en": "Defense & Aerospace", "tagline": "지정학 리스크가 방산 Capex 확장의 구조적 동력이 된다", "benchmark": "ITA ETF", "coverage": "LMT, NOC, RTX, HII, KTOS", "icon": "🛡️"},
    "ev_battery":          {"name_kr": "전기차 & 배터리", "name_en": "EV & Battery", "tagline": "배터리 밀도 경쟁이 EV 보급 속도를 결정한다", "benchmark": "LIT ETF", "coverage": "TSLA, RIVN, NIO, QS, CHPT", "icon": "⚡"},
    "clean_energy":        {"name_kr": "클린 에너지 & 태양광", "name_en": "Clean Energy & Solar", "tagline": "AI 전력 수요가 재생에너지 Capex의 새 엔진이 된다", "benchmark": "ICLN ETF", "coverage": "FSLR, ENPH, NEE, SEDG, ARRY", "icon": "☀️"},
    "cloud_saas":          {"name_kr": "클라우드 & SaaS", "name_en": "Cloud & Enterprise SaaS", "tagline": "AI 인프라 위에서 소프트웨어 마진이 다시 확장된다", "benchmark": "BVP Nasdaq Emerging Cloud", "coverage": "MSFT, AMZN, GOOGL, NOW, SNOW", "icon": "☁️"},
    "cybersecurity":       {"name_kr": "사이버보안", "name_en": "Cybersecurity", "tagline": "AI 시대의 공격 고도화가 보안 예산 확대를 강제한다", "benchmark": "HACK ETF", "coverage": "CRWD, ZS, PANW, FTNT, S", "icon": "🔐"},
    "biotech":             {"name_kr": "바이오텍 & 유전자 치료", "name_en": "Biotech & Gene Therapy", "tagline": "GLP-1·유전자 편집이 의료 패러다임의 전환점을 열다", "benchmark": "XBI ETF", "coverage": "MRNA, REGN, VRTX, BEAM, CRSP", "icon": "🧬"},
    "nuclear":             {"name_kr": "원자력 에너지 & SMR", "name_en": "Nuclear Energy & SMR", "tagline": "SMR과 AI 전력 수요가 원전 르네상스의 방아쇠를 당긴다", "benchmark": "URA ETF", "coverage": "CCJ, NNE, LEU, OKLO, SMR", "icon": "⚛️"},
    "robotics":            {"name_kr": "로보틱스 & 산업 자동화", "name_en": "Robotics & Industrial Automation", "tagline": "휴머노이드 로봇이 제조업 노동 방정식을 바꾼다", "benchmark": "ROBO ETF", "coverage": "ABB, FANUC, IRBT, BRZE, NVDA", "icon": "🤖"},
    "fintech":             {"name_kr": "핀테크 & 디지털 결제", "name_en": "Fintech & Digital Payments", "tagline": "글로벌 결제 인프라의 디지털 전환이 새 수익 엔진이 된다", "benchmark": "FINX ETF", "coverage": "V, MA, SQ, PYPL, SOFI", "icon": "💳"},
    "datacenter_reit":     {"name_kr": "데이터센터 REIT", "name_en": "Data Center REIT", "tagline": "AI 데이터센터 임대 수요가 REIT 배당 성장을 뒷받침한다", "benchmark": "XLRE / VNQ ETF", "coverage": "EQIX, DLR, AMT, CCI, CONE", "icon": "🏢"},
    "healthcare_tech":     {"name_kr": "헬스케어 테크 & AI 진단", "name_en": "Healthcare Technology & AI Diagnostics", "tagline": "AI 진단과 디지털 치료가 의료 효율성 혁명을 주도한다", "benchmark": "IHF ETF", "coverage": "UNH, HCA, ISRG, VEEV, DOCS", "icon": "🏥"},
}

# ── 한국 산업 목록 ───────────────────────────────────────────────────────────────
#
# 미국 산업군을 그대로 쓰면 한국 시장 리포트인데 대표 종목이 NVDA·TSLA 가 된다.
# 산업 구성도 시장마다 다르다 — 조선·엔터처럼 한국 증시의 큰 축이 미국 목록에는
# 아예 없고, 반대로 데이터센터 REIT 은 한국에 사실상 없다.
#
# coverage 의 티커는 실제 상장 목록에서 존재를 확인한 것만 넣었다.
KR_INDUSTRIES: dict[str, dict] = {
    "kr_semiconductor":  {"name_kr": "반도체 & HBM", "name_en": "Semiconductor & HBM", "tagline": "HBM 공급이 AI 서버 증설 속도를 좌우한다", "benchmark": "KOSPI 반도체", "coverage": "005930.KS, 000660.KS, 042700.KS, 039030.KQ, 036930.KQ", "icon": "💾"},
    "kr_battery":        {"name_kr": "2차전지 & 소재", "name_en": "Secondary Battery & Materials", "tagline": "북미 증설과 캐즘 사이에서 갈리는 수익성", "benchmark": "KRX 2차전지", "coverage": "373220.KS, 006400.KS, 247540.KQ, 066970.KS, 005490.KS", "icon": "🔋"},
    "kr_defense":        {"name_kr": "방산 & 항공우주", "name_en": "Defense & Aerospace", "tagline": "수출 수주가 실적의 기울기를 바꾼다", "benchmark": "KRX 방산", "coverage": "012450.KS, 064350.KS, 047810.KS, 272210.KS", "icon": "🛡️"},
    "kr_shipbuilding":   {"name_kr": "조선 & 기계", "name_en": "Shipbuilding & Machinery", "tagline": "고선가 수주잔고가 몇 년치 실적을 미리 정한다", "benchmark": "KRX 조선", "coverage": "009540.KS, 042660.KS, 010140.KS, 267260.KS, 034020.KS", "icon": "🚢"},
    "kr_auto":           {"name_kr": "자동차 & 부품", "name_en": "Auto & Components", "tagline": "환율과 믹스가 마진을 가른다", "benchmark": "KRX 자동차", "coverage": "000270.KS, 012330.KS, 018880.KS, 204320.KS", "icon": "🚗"},
    "kr_bio":            {"name_kr": "바이오 & 제약", "name_en": "Biotech & Pharma", "tagline": "위탁생산 증설과 신약 파이프라인의 두 축", "benchmark": "KRX 헬스케어", "coverage": "207940.KS, 068270.KS, 000100.KS, 128940.KS", "icon": "🧬"},
    "kr_internet":       {"name_kr": "인터넷 & 게임", "name_en": "Internet & Gaming", "tagline": "광고 회복과 신작 성과가 실적을 좌우한다", "benchmark": "KRX 미디어", "coverage": "035420.KS, 035720.KS, 259960.KS, 251270.KS", "icon": "🌐"},
    "kr_finance":        {"name_kr": "은행 & 금융지주", "name_en": "Banks & Financial Holdings", "tagline": "금리 방향과 주주환원 정책이 밸류를 정한다", "benchmark": "KRX 은행", "coverage": "105560.KS, 055550.KS, 086790.KS, 138040.KS", "icon": "🏦"},
    "kr_chemical":       {"name_kr": "화학 & 소재", "name_en": "Chemicals & Materials", "tagline": "중국 증설과 스프레드 회복 시점이 관건", "benchmark": "KRX 화학", "coverage": "051910.KS, 011170.KS, 014680.KS, 010060.KS", "icon": "⚗️"},
    "kr_steel":          {"name_kr": "철강 & 비철금속", "name_en": "Steel & Non-ferrous Metals", "tagline": "중국 감산과 전방 수요가 가격을 결정한다", "benchmark": "KRX 철강", "coverage": "005490.KS, 004020.KS, 010130.KS, 103140.KS", "icon": "🏗️"},
    "kr_power":          {"name_kr": "전력기기 & 원자력", "name_en": "Power Equipment & Nuclear", "tagline": "글로벌 전력망 교체 수요가 구조적 성장을 만든다", "benchmark": "KRX 유틸리티", "coverage": "267260.KS, 034020.KS, 052690.KS, 032820.KQ", "icon": "⚡"},
    "kr_consumer":       {"name_kr": "유통 & 소비재", "name_en": "Retail & Consumer", "tagline": "내수 소비와 해외 진출 성과가 갈린다", "benchmark": "KRX 필수소비재", "coverage": "097950.KS, 271560.KS, 090430.KS, 051900.KS, 282330.KS", "icon": "🛒"},
    "kr_entertainment":  {"name_kr": "엔터테인먼트 & K-콘텐츠", "name_en": "Entertainment & K-Content", "tagline": "아티스트 활동 주기가 실적 변동을 만든다", "benchmark": "KRX 미디어", "coverage": "352820.KS, 035900.KQ, 041510.KQ, 037270.KS", "icon": "🎤"},
    "kr_construction":   {"name_kr": "건설 & 부동산", "name_en": "Construction & Real Estate", "tagline": "분양 경기와 해외 수주가 실적을 나눈다", "benchmark": "KRX 건설", "coverage": "000720.KS, 006360.KS, 047040.KS, 375500.KS", "icon": "🏢"},
    "kr_telecom":        {"name_kr": "통신 & 인프라", "name_en": "Telecom & Infrastructure", "tagline": "배당 매력과 신사업 전환의 균형", "benchmark": "KRX 통신", "coverage": "017670.KS, 032640.KS", "icon": "📡"},
}


def industries_for(market: str = "US") -> dict[str, dict]:
    """시장에 맞는 산업 목록.

    산업 구성은 시장마다 다르다. 조선·엔터는 한국 증시의 큰 축이지만 미국
    목록에는 없고, 데이터센터 REIT 은 한국에 사실상 없다. 목록을 공유하면
    한국 리포트인데 대표 종목이 미국 기업이 된다.
    """
    from backend.services.markets import normalize
    return KR_INDUSTRIES if normalize(market) == "KR" else INDUSTRIES


def industry_meta(industry_id: str) -> Optional[dict]:
    """산업 id 로 정의를 찾는다 (시장 구분 없이)."""
    return INDUSTRIES.get(industry_id) or KR_INDUSTRIES.get(industry_id)


# ── ETF 벤치마크 매핑 ─────────────────────────────────────────────────────────────

_BENCHMARK_ETF_MAP: dict[str, str] = {
    "SOX": "SOXX",
    "NASDAQ100": "QQQ",
    "UFO": "UFO",
    "ITA": "ITA",
    "LIT": "LIT",
    "ICLN": "ICLN",
    "XBI": "XBI",
    "URA": "URA",
    "HACK": "HACK",
    "ROBO": "ROBO",
    "FINX": "FINX",
    "XLRE": "XLRE",
    "VNQ": "VNQ",
    "IHF": "IHF",
    "BVP": "WCLD",
}


# ── Claude 호출 ───────────────────────────────────────────────────────────────────

def _call_claude(
    model: str, prompt: str, system: str, max_tokens: int,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> str:
    """Claude 호출. 토큰 한도로 잘린 경우 후속 호출로 마무리 문장 복구.

    스트리밍으로 받는다. 응답을 한 번에 기다리면 사용자가 중단을 눌러도
    그 호출이 끝날 때까지(수십 초) 아무것도 멈출 수 없기 때문이다.
    조각을 받을 때마다 취소를 확인하고, 취소면 JobCancelled 를 올려
    with 블록을 빠져나가며 연결을 끊는다.
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

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
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )

    if stop_reason == "max_tokens" and text.strip():
        # 끊긴 마지막 문장만 이어붙이는 호출이다.
        # 예전에는 원본 프롬프트(수천 토큰) + 잘린 응답(4096 토큰)을 통째로 재전송해,
        # 출력 200 토큰을 얻는 데 입력 9,000 토큰을 썼다 (전체 입력의 63%).
        # 문장을 잇는 데 필요한 것은 **끝부분 몇 줄뿐**이므로 꼬리만 보낸다.
        try:
            tail_ctx = text[-600:]
            tail, _ = _stream(
                model=model,
                max_tokens=200,
                messages=[
                    {"role": "user", "content": (
                        "Below is the tail of a Korean report that was cut off mid-sentence "
                        "by a token limit. Write ONLY the few words or one sentence needed to "
                        "finish that last incomplete sentence naturally, in Korean. "
                        "Do not repeat the text, do not add new sections or headings.\n\n"
                        f"---\n{tail_ctx}"
                    )},
                ],
            )
            if tail.strip():
                text += tail
        except JobCancelled:
            raise
        except Exception:
            pass

    return text


def _call_haiku(prompt: str, system: str = "", max_tokens: int = 2000,
                should_cancel: Optional[Callable[[], bool]] = None) -> str:
    return _call_claude("claude-haiku-4-5-20251001", prompt, system, max_tokens, should_cancel)


def _call_sonnet(prompt: str, system: str = "", max_tokens: int = 4096,
                 should_cancel: Optional[Callable[[], bool]] = None) -> str:
    return _call_claude("claude-sonnet-4-6", prompt, system, max_tokens, should_cancel)




# ── yfinance 데이터 수집 ──────────────────────────────────────────────────────────

# 뉴스 수집이 실패했을 때 프롬프트에 넣는 문구.
#
# 예전에는 "(뉴스 데이터 없음 — 학습 지식 활용)" 이었다. 없다는 사실을 적는
# 데까지는 맞았는데 마지막에 반대로 갔다 — 학습 지식으로 채우라는 지시다.
# 뉴스는 본질적으로 시점 정보라 기억으로 대체하면 그건 뉴스가 아니고, 리포트는
# 그 구분을 독자에게 알려주지 않는다. 오늘 날짜가 박힌 리서치 리포트에 낡은
# 사실이 최신 동향으로 실린다.
NO_NEWS_NOTICE = (
    "(뉴스를 수집하지 못했습니다. 뉴스·최근 동향에 근거한 서술을 하지 말고, "
    "기억으로 채우지 마세요. 해당 섹션에는 뉴스를 확보하지 못했다고 밝히세요.)"
)


def _fmt_amount(value, currency: str) -> str:
    """금액을 그 통화의 단위 체계로 적는다.

    LLM 에게 넘기는 텍스트라 **단위를 틀리면 그대로 리포트에 실린다.** 실제로
    한국 종목의 연간 실적을 '$333605.94B' 로 적어 보내고 있었다 — 삼성전자
    매출 333조원(KRW)을 10억으로 나눈 뒤 달러를 붙인 값이다. 받아 본 모델은
    이걸 달러로 해석해 기업 규모를 1,300배 부풀려 서술한다.

    원화는 조·억·만으로 끊는다. 'B'(십억)는 원화에 쓰지 않는 단위라, 숫자가
    맞더라도 모델이 달러로 오해하기 쉽다.

    구간과 자릿수는 프론트의 `frontend/src/lib/market.ts::formatCompact` 와
    **같아야 한다.** 같은 금액이 화면과 리포트에서 다르게 적히면 어느 쪽이
    맞는지 사용자가 알 수 없다. CLAUDE.md §1.4 의 표가 그 함수에서 나왔다.

    여기가 갈라져 있었다. 억을 소수점 없이 적어서 1.49억이 `₩1억` 으로
    나갔다 — **33% 어긋난 금액이 리포트에 실린다.** 0 을 적는 것과 달리
    그럴듯해서 아무도 의심하지 않는다 (§1.3b).
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    sign = "-" if v < 0 else ""
    a = abs(v)

    def _round_half_up(x: float) -> int:
        # JS 의 Math.round 는 .5 를 올린다. 파이썬 round()·format 은 짝수로
        # 내린다(bankers rounding). 프론트와 같은 값을 적으려면 여기서 맞춘다.
        return int(math.floor(x + 0.5))

    if currency == "KRW":
        if a >= 1e12:
            return f"{sign}₩{a / 1e12:,.2f}조"
        if a >= 1e8:
            return f"{sign}₩{a / 1e8:,.2f}억"
        if a >= 1e4:
            return f"{sign}₩{_round_half_up(a / 1e4):,}만"
        return f"{sign}₩{_round_half_up(a):,}"
    if a >= 1e12:
        return f"{sign}${a / 1e12:,.2f}T"
    if a >= 1e9:
        return f"{sign}${a / 1e9:,.2f}B"
    if a >= 1e6:
        return f"{sign}${a / 1e6:,.2f}M"
    if a >= 1e3:
        return f"{sign}${a / 1e3:,.1f}K"
    return f"{sign}${a:,.2f}"


def _fmt_price(value, currency: str) -> str:
    """주가·EPS 처럼 단위를 줄이지 않고 그대로 적는 금액."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    # 원화는 호가 단위가 1원이라 소수점이 없다. ₩71,900.00 은 없는 정밀도다.
    return f"₩{v:,.0f}" if currency == "KRW" else f"${v:,.2f}"


def gather_equity_yfinance(ticker: str, market: str = "US") -> tuple[str, str, dict]:
    """yfinance로 종목 데이터 수집.
    Returns (company_name, formatted_text, raw_dict).
    """
    from backend.services.markets import get_market as _get_market
    # 통화는 시장이 정한다. 한국 종목에 $ 를 붙이면 25만원짜리 주식이
    # 25만 달러로 읽히고, LLM 도 그 숫자를 달러로 해석해 리포트를 쓴다.
    spec = _get_market(market)
    cur = spec.currency

    try:
        t = yf.Ticker(ticker)
        info = t.info

        # 시세와 재무제표의 통화는 다를 수 있다(해외 상장·ADR). yfinance 가
        # 알려 주면 그 값을 쓰고, 없을 때만 시장 기본 통화로 돌아간다.
        cur = info.get("currency") or cur
        fin_cur = info.get("financialCurrency") or cur

        company_name = info.get("longName") or info.get("shortName") or ticker
        # 한국 종목은 yfinance 가 영문명을 준다("Samsung Electronics Co., Ltd.").
        # 한국어 리포트에 영문 상호가 섞이면 읽기 불편하고, LLM 도 그 이름으로
        # 해외 기사를 찾아 국내 맥락을 놓친다.
        if ticker.upper().endswith((".KS", ".KQ")):
            try:
                from backend.services.korea_universe import lookup as _kr
                kr_row = _kr(ticker)
                if kr_row and kr_row.get("name"):
                    company_name = kr_row["name"]
            except Exception:
                pass

        raw_dict: dict = {
            "company_name":             company_name,
            "ticker":                   ticker,
            "currentPrice":             info.get("currentPrice") or info.get("regularMarketPrice"),
            "marketCap":                info.get("marketCap"),
            "trailingPE":               info.get("trailingPE"),
            "forwardPE":                info.get("forwardPE"),
            "trailingEps":              info.get("trailingEps"),
            "totalRevenue":             info.get("totalRevenue"),
            "revenueGrowth":            info.get("revenueGrowth"),
            "grossMargins":             info.get("grossMargins"),
            "operatingMargins":         info.get("operatingMargins"),
            "profitMargins":            info.get("profitMargins"),
            "beta":                     info.get("beta"),
            "fiftyTwoWeekHigh":         info.get("fiftyTwoWeekHigh"),
            "fiftyTwoWeekLow":          info.get("fiftyTwoWeekLow"),
            "dividendYield":            info.get("dividendYield"),
            "sector":                   info.get("sector"),
            "industry":                 info.get("industry"),
            "exchange":                 info.get("exchange"),
            "fullTimeEmployees":        info.get("fullTimeEmployees"),
            "targetMeanPrice":          info.get("targetMeanPrice"),
            "targetHighPrice":          info.get("targetHighPrice"),
            "targetLowPrice":           info.get("targetLowPrice"),
            "numberOfAnalystOpinions":  info.get("numberOfAnalystOpinions"),
            "recommendationKey":        info.get("recommendationKey"),
        }

        # 3개년 연간 실적
        annual_data: dict = {}
        try:
            fin = t.financials
            if fin is not None and not fin.empty:
                for col in list(fin.columns)[:3]:
                    year = str(col.year) if hasattr(col, "year") else str(col)[:4]
                    def _safe_get(row_name: str):
                        try:
                            return float(fin.loc[row_name, col]) if row_name in fin.index else None
                        except Exception:
                            return None
                    annual_data[year] = {
                        "total_revenue":   _safe_get("Total Revenue"),
                        "gross_profit":    _safe_get("Gross Profit"),
                        "operating_income":_safe_get("Operating Income"),
                        "net_income":      _safe_get("Net Income"),
                    }
        except Exception:
            pass

        # 텍스트 포매팅
        lines = [f"【{company_name} ({ticker}) yfinance 실제 데이터】  기준: {TODAY}"]

        price = raw_dict["currentPrice"]
        if price:
            lines.append(f"현재주가: {_fmt_price(price, cur)} [A]")

        mktcap = raw_dict["marketCap"]
        if mktcap:
            lines.append(f"시가총액: {_fmt_amount(mktcap, cur)} [A]")

        if raw_dict["trailingPE"]:
            lines.append(f"Trailing P/E: {float(raw_dict['trailingPE']):.1f}x [A]")
        if raw_dict["forwardPE"]:
            lines.append(f"Forward P/E: {float(raw_dict['forwardPE']):.1f}x [E]")
        if raw_dict["trailingEps"]:
            lines.append(f"EPS (TTM): {_fmt_price(raw_dict['trailingEps'], cur)} [A]")
        if raw_dict["totalRevenue"]:
            lines.append(f"연매출: {_fmt_amount(raw_dict['totalRevenue'], cur)} [A]")
        if raw_dict["revenueGrowth"] is not None:
            lines.append(f"매출성장률 (YoY): {float(raw_dict['revenueGrowth'])*100:.1f}% [A]")
        if raw_dict["grossMargins"] is not None:
            lines.append(f"매출총이익률: {float(raw_dict['grossMargins'])*100:.1f}% [A]")
        if raw_dict["operatingMargins"] is not None:
            lines.append(f"영업이익률: {float(raw_dict['operatingMargins'])*100:.1f}% [A]")
        if raw_dict["profitMargins"] is not None:
            lines.append(f"순이익률: {float(raw_dict['profitMargins'])*100:.1f}% [A]")
        if raw_dict["beta"] is not None:
            lines.append(f"베타: {float(raw_dict['beta']):.2f}")
        if raw_dict["fiftyTwoWeekHigh"]:
            lines.append(f"52주 최고: {_fmt_price(raw_dict['fiftyTwoWeekHigh'], cur)}")
        if raw_dict["fiftyTwoWeekLow"]:
            lines.append(f"52주 최저: {_fmt_price(raw_dict['fiftyTwoWeekLow'], cur)}")
        if raw_dict["dividendYield"] is not None:
            lines.append(f"배당수익률: {float(raw_dict['dividendYield'])*100:.2f}%")
        if raw_dict["sector"]:
            lines.append(f"섹터: {raw_dict['sector']}")
        if raw_dict["industry"]:
            lines.append(f"산업: {raw_dict['industry']}")
        if raw_dict["exchange"]:
            lines.append(f"거래소: {raw_dict['exchange']}")
        if raw_dict["fullTimeEmployees"]:
            lines.append(f"직원수: {int(raw_dict['fullTimeEmployees']):,}명")
        if raw_dict["targetMeanPrice"]:
            lines.append(f"애널리스트 평균목표주가: {_fmt_price(raw_dict['targetMeanPrice'], cur)} [E]")
        if raw_dict["targetHighPrice"]:
            lines.append(f"애널리스트 최고목표주가: {_fmt_price(raw_dict['targetHighPrice'], cur)} [E]")
        if raw_dict["targetLowPrice"]:
            lines.append(f"애널리스트 최저목표주가: {_fmt_price(raw_dict['targetLowPrice'], cur)} [E]")
        if raw_dict["numberOfAnalystOpinions"]:
            lines.append(f"커버리지 애널리스트: {raw_dict['numberOfAnalystOpinions']}명")
        if raw_dict["recommendationKey"]:
            lines.append(f"컨센서스 의견: {str(raw_dict['recommendationKey']).upper()}")

        if annual_data:
            lines.append("")
            lines.append(f"【연간 실적 (최근 3개년, yfinance) · 표기 통화 {fin_cur}】")
            for year, data in sorted(annual_data.items(), reverse=True):
                parts = [f"  {year}년:"]
                if data.get("total_revenue"):
                    parts.append(f"매출 {_fmt_amount(data['total_revenue'], fin_cur)}")
                if data.get("gross_profit"):
                    parts.append(f"매출총이익 {_fmt_amount(data['gross_profit'], fin_cur)}")
                if data.get("operating_income"):
                    parts.append(f"영업이익 {_fmt_amount(data['operating_income'], fin_cur)}")
                if data.get("net_income"):
                    parts.append(f"순이익 {_fmt_amount(data['net_income'], fin_cur)}")
                lines.append(" | ".join(parts))

        raw_dict["annual_data"] = annual_data
        return company_name, "\n".join(lines), raw_dict

    except Exception as exc:
        # 프롬프트에는 이미 실패가 적혀 나간다(아래 반환값). 로그가 없으면
        # 그 리포트가 왜 얇은지 나중에 알 수 없다.
        logger.warning("yfinance 종목 데이터 수집 실패 (%s, market=%s)",
                       ticker, market, exc_info=True)
        return ticker, f"(yfinance 데이터 수집 오류: {exc})", {}


def gather_industry_yfinance(meta: dict, market: str = "US") -> tuple[str, dict]:
    """산업 ETF + 커버리지 종목 yfinance 데이터 수집.
    Returns (formatted_text, raw_dict).
    """
    lines = [f"【{meta['name_kr']} 산업 yfinance 실제 데이터】  기준: {TODAY}"]
    raw: dict = {}

    # 벤치마크 ETF 1년 수익률
    benchmark_str = meta.get("benchmark", "")
    words = re.findall(r"\b[A-Z]{2,6}\b", benchmark_str.upper())
    etf_tickers: list[str] = []
    # KRX·KOSPI 는 거래소 이름이지 조회 가능한 티커가 아니다. 한국 벤치마크는
    # "KRX 조선"처럼 한글이 섞여 있어 여기서 'KRX' 만 뽑히는데, 그걸 그대로
    # 조회하면 상장폐지 경고만 남고 벤치마크가 빈칸이 된다.
    skip_words = {"ETF", "AND", "THE", "BVP", "KRX", "KOSPI", "KOSDAQ"}
    for w in words:
        if w in skip_words:
            continue
        mapped = _BENCHMARK_ETF_MAP.get(w)
        if mapped and mapped not in etf_tickers:
            etf_tickers.append(mapped)
        elif len(w) <= 5 and w not in etf_tickers:
            etf_tickers.append(w)

    if not etf_tickers:
        # 벤치마크가 없으면 그 시장의 대표 지수를 쓴다.
        # 한국 종목에 SPY 를 붙이면 통화·시장이 다른 것과 비교하게 된다.
        etf_tickers = ["SPY"] if market == "US" else ["^KS11"]

    lines.append("")
    lines.append("【벤치마크 ETF 1년 수익률】")
    for etf in etf_tickers[:2]:
        try:
            hist = yf.Ticker(etf).history(period="1y")
            if not hist.empty and len(hist) > 1:
                start_px = float(hist["Close"].iloc[0])
                end_px   = float(hist["Close"].iloc[-1])
                ret_1y   = (end_px / start_px - 1) * 100
                raw[f"{etf}_1y_return"] = ret_1y
                lines.append(f"  {etf}: {ret_1y:+.1f}% (최근 1년) [A]")
        except Exception:
            lines.append(f"  {etf}: 데이터 없음")

    # 커버리지 종목
    coverage_str = meta.get("coverage", "")
    coverage_tickers = [t.strip() for t in coverage_str.split(",") if t.strip()]
    # 시장 기본 통화는 폴백일 뿐이다. 실제 통화는 종목마다 yfinance 응답에서
    # 읽는다 (§1.4) — 커버리지는 ADR·해외 상장이 섞이는 자리라 시장으로
    # 일괄 판단하면 달러 값에 ₩ 가, 원화 값에 $ 가 붙는다.
    from backend.services.markets import get_market as _get_market
    market_cur = _get_market(market).currency

    lines.append("")
    lines.append("【커버리지 종목 핵심 지표】")
    coverage_data: dict = {}
    for ct in coverage_tickers:
        try:
            info = yf.Ticker(ct).info
            # 주가·시가총액은 거래 통화(`currency`) 기준이다. 재무제표 통화
            # (`financialCurrency`)와 다를 수 있으나 여기서는 쓰지 않는다.
            cur        = info.get("currency") or market_cur
            price      = info.get("currentPrice") or info.get("regularMarketPrice")
            mktcap     = info.get("marketCap")
            pe         = info.get("trailingPE")
            rev_growth = info.get("revenueGrowth")
            name       = info.get("shortName") or ct
            # 한국 종목은 코드만 보면 어느 회사인지 알 수 없다. yfinance 가
            # 한글 이름을 주지 않는 경우가 많아 상장 목록에서 보완한다 —
            # 이름이 없으면 LLM 도 '009540' 을 그대로 쓴 리포트를 낸다.
            if ct.endswith((".KS", ".KQ")):
                try:
                    from backend.services.korea_universe import lookup as _kr
                    kr_row = _kr(ct)
                    if kr_row and kr_row.get("name"):
                        name = kr_row["name"]
                except Exception:
                    pass

            parts = [f"  {ct} ({name})" if name and name != ct else f"  {ct}"]
            if price:
                parts.append(f"주가 {_fmt_price(price, cur)}")
            if mktcap:
                parts.append(f"시총 {_fmt_amount(mktcap, cur)}")
            if pe:
                parts.append(f"P/E {float(pe):.1f}x")
            if rev_growth is not None:
                parts.append(f"매출성장 {float(rev_growth)*100:.1f}%")
            lines.append(" | ".join(parts))

            coverage_data[ct] = {
                "name": name,
                "price": price,
                "marketCap": mktcap,
                "trailingPE": pe,
                "revenueGrowth": rev_growth,
            }
        except Exception:
            logger.warning("커버리지 종목 데이터 수집 실패 (%s, market=%s)",
                           ct, market, exc_info=True)
            lines.append(f"  {ct}: 데이터 없음")

    raw["coverage_data"] = coverage_data
    return "\n".join(lines), raw


# ── Perplexity 뉴스 수집 ──────────────────────────────────────────────────────────

def gather_equity_perplexity(ticker: str, company_name: str, market: str = "US") -> str:
    """Perplexity sonar로 종목 최신 뉴스·애널리스트 동향 수집.

    한국 종목이면 국내 경제지에서, 한국 투자자 관점으로 모은다. 예전에는
    시장 구분이 없어 삼성전자 리포트도 미국 매체 기사로 썼다.
    """
    from backend.services.news_sources import focus_block
    from backend.services import perplexity

    # 응답을 **영어로** 받는다. 이 결과는 그대로 Claude 입력이 되는데,
    # 같은 내용이라도 한국어는 글자당 약 1토큰, 영어는 약 0.23토큰이라
    # 실측상 Claude 입력이 66% 줄어든다 (2,308 → 791 토큰).
    # 최종 리포트는 Claude 가 한국어로 쓰므로 사용자 화면은 영향받지 않는다.
    prompt = f"""Today: {TODAY}
Stock: {company_name} ({ticker})

Collect the following **in English**, concise bullet points. Favor news and narrative over raw figures.

[Key news & events, last 30 days]
- 3-5 major press items (title, source, date, one-sentence summary each)

[Analyst actions, last 30 days]
- Price-target raises/cuts, rating changes

[Competitor moves]
- 1-2 recent issues at key competitors

[Industry trends]
- 1-2 current trends relevant to {company_name}
{focus_block(market, company_name)}"""

    return perplexity.search(prompt, market=market, max_tokens=1500,
                             label=f"equity:{ticker}")


def gather_industry_perplexity(meta: dict, market: str = "US") -> str:
    """Perplexity sonar로 산업 최신 뉴스·트렌드·규제 동향 수집."""
    from backend.services.news_sources import focus_block
    from backend.services import perplexity

    # 영어로 수집 — Claude 입력 토큰을 크게 줄인다 (한국어 대비 약 1/3)
    prompt = f"""Today: {TODAY}
Industry: {meta['name_en']}
Key names: {meta['coverage']}

Collect the following **in English**, concise bullet points.

[Industry news, last 30 days]
- 3-5 major press items (title, source, date, one-sentence summary each)

[Trends & regulation]
- Recent policy shifts, regulatory issues, technology moves

[KPI updates]
- Recently reported market size, growth rate, demand indicators
{focus_block(market, meta['name_en'])}"""

    return perplexity.search(prompt, market=market, max_tokens=1500,
                             label=f"industry:{meta.get('name_en', '?')}")


# ── 프롬프트 빌더 ─────────────────────────────────────────────────────────────────

def _equity_prompt(ticker: str, company_name: str, market: str = "US") -> str:
    """Sonnet 단일 호출용 (8000 토큰)."""
    from backend.services.markets import get_market
    spec = get_market(market)
    exchange_hint = "NYSE/NASDAQ" if market == "US" else "KOSPI/KOSDAQ"
    cur = spec.currency_symbol
    # 금액 예시도 시장의 표기 관습을 따라야 한다. '₩XXXB' 라고 적어 두면
    # 모델이 원화를 10억 단위로 쓰거나 아예 달러로 바꿔 적는다. 주가도
    # 원화는 소수점이 없다(호가 단위 1원).
    cap_ex   = "₩XXX조" if cur == "₩" else "$XXXB"
    price_ex = "₩XXX,XXX" if cur == "₩" else "$XXX.XX"
    today = datetime.now().strftime("%Y년 %m월 %d일")
    return f"""{_market_stance(market)}
오늘은 {today}입니다. {company_name} ({ticker}) 종목 리서치 레포트를 아래 10개 섹션 순서대로 빠짐없이 작성하세요.
yfinance 수치는 그대로 인용[A], 추정치는 [E] 표시. 마지막 섹션(X)까지 반드시 완성하세요.

## HEADER
투자의견: [BUY/HOLD/SELL]
현재주가: {price_ex} [A]
시가총액: {cap_ex} [A]
Bull 목표주가: {price_ex}
Bear 목표주가: {price_ex}
슬로건: [핵심 투자포인트 한 줄 — 수치 포함]
KEY_HIGHLIGHT_1: [수치 포함 핵심 지표 1]
KEY_HIGHLIGHT_2: [수치 포함 핵심 지표 2]
KEY_HIGHLIGHT_3: [수치 포함 핵심 지표 3]
KEY_HIGHLIGHT_4: [수치 포함 핵심 지표 4]
KEY_HIGHLIGHT_5: [수치 포함 핵심 지표 5]
거래소: [{exchange_hint}]
업종: [업종명]

## II. 투자의견 요약 (Executive Summary)
| 구분 | 🐂 Bull Case | 🐻 Bear Case |
| --- | --- | --- |
| 투자의견 | BUY | HOLD |
| 목표주가 | {price_ex} | {price_ex} |
| 현재주가 | {price_ex} [A] | {price_ex} [A] |

**핵심 요약**
• [핵심 지표 1]
• [핵심 지표 2]
• [핵심 지표 3]

## III. 기업 개요 (Company Overview)
[설립연도·본사·사업모델·매출 규모 — 3~4문장]

| 사업부 | 매출비중 | 핵심 제품/서비스 | 주요 고객 |
| --- | --- | --- | --- |
| [사업부 1] | XX% [E] | [제품] | [고객] |

## IV. 시황·업종·산업 분석 (Top-down Analysis)
[산업 트렌드 5개 항목 — 각 2~3문장]

## V. 투자 근거 (Investment Points)
[투자 포인트 6개 항목 — 각 3~4문장]

## VI. 재무제표 & KPI 분석 (Financials)
[연간 실적 3개년 테이블 + 분기 실적 테이블 + KPI 6개]

## VII. 동종업체 비교 분석 (Peer Analysis)
[핵심 지표 비교 테이블 + 밸류에이션 멀티플 테이블]

## VIII. 리스크 요인 (Risk Factors)
[HIGH 2개 / MED 3개 / LOW 1개]

## IX. 적정주가 산출 (Valuation)
[DCF 가정 테이블 + 민감도 분석 5×5 테이블]

## X. 종합 결론 (Conclusion)
[결론 3~4문장]
**최종 투자의견: [BUY/HOLD/SELL] | 목표주가: {price_ex} (Bull) / {price_ex} (Bear)**
"""


def _equity_prompt_part1(ticker: str, company_name: str, market: str = "US") -> str:
    """Haiku Phase-1: HEADER + II~V."""
    from backend.services.markets import get_market
    spec = get_market(market)
    exchange_hint = "NYSE/NASDAQ" if market == "US" else "KOSPI/KOSDAQ"
    cur = spec.currency_symbol
    # 금액 예시도 시장의 표기 관습을 따라야 한다. '₩XXXB' 라고 적어 두면
    # 모델이 원화를 10억 단위로 쓰거나 아예 달러로 바꿔 적는다. 주가도
    # 원화는 소수점이 없다(호가 단위 1원).
    cap_ex   = "₩XXX조" if cur == "₩" else "$XXXB"
    price_ex = "₩XXX,XXX" if cur == "₩" else "$XXX.XX"
    today = datetime.now().strftime("%Y년 %m월 %d일")
    return f"""{_market_stance(market)}
오늘은 {today}입니다. {company_name} ({ticker}) 레포트의 **HEADER와 섹션 II~V만** 작성하세요.
아래 5개 블록을 모두 완성해야 합니다. 섹션 VI 이후는 쓰지 마세요.

## HEADER
투자의견: [BUY/HOLD/SELL]
현재주가: {price_ex} [A]
시가총액: {cap_ex} [A]
Bull 목표주가: {price_ex}
Bear 목표주가: {price_ex}
슬로건: [핵심 투자포인트 한 줄 — 수치 포함]
KEY_HIGHLIGHT_1: [수치 포함 핵심 지표 1]
KEY_HIGHLIGHT_2: [수치 포함 핵심 지표 2]
KEY_HIGHLIGHT_3: [수치 포함 핵심 지표 3]
KEY_HIGHLIGHT_4: [수치 포함 핵심 지표 4]
KEY_HIGHLIGHT_5: [수치 포함 핵심 지표 5]
거래소: [{exchange_hint}]
업종: [업종명]

## II. 투자의견 요약 (Executive Summary)
| 구분 | 🐂 Bull Case | 🐻 Bear Case |
| --- | --- | --- |
| 투자의견 | BUY | HOLD |
| 목표주가 | {price_ex} | {price_ex} |
| 현재주가 | {price_ex} [A] | {price_ex} [A] |

**핵심 요약**
• [핵심 지표 1]
• [핵심 지표 2]
• [핵심 지표 3]

## III. 기업 개요 (Company Overview)
[설립연도·본사·사업모델·매출 규모 — 3~4문장]

| 사업부 | 매출비중 | 핵심 제품/서비스 | 주요 고객 |
| --- | --- | --- | --- |
| [사업부 1] | XX% [E] | [제품] | [고객] |

## IV. 시황·업종·산업 분석 (Top-down Analysis)
[산업 트렌드 5개 항목 — 각 2~3문장]

## V. 투자 근거 (Investment Points)
[투자 포인트 6개 항목 — 각 2~3문장]
"""


def _equity_prompt_part2(ticker: str, company_name: str, market: str = "US") -> str:
    """Haiku Phase-2: VI~X."""
    from backend.services.markets import get_market
    spec = get_market(market)
    exchange_hint = "NYSE/NASDAQ" if market == "US" else "KOSPI/KOSDAQ"
    cur = spec.currency_symbol
    # 금액 예시도 시장의 표기 관습을 따라야 한다. '₩XXXB' 라고 적어 두면
    # 모델이 원화를 10억 단위로 쓰거나 아예 달러로 바꿔 적는다. 주가도
    # 원화는 소수점이 없다(호가 단위 1원).
    cap_ex   = "₩XXX조" if cur == "₩" else "$XXXB"
    price_ex = "₩XXX,XXX" if cur == "₩" else "$XXX.XX"
    today = datetime.now().strftime("%Y년 %m월 %d일")
    return f"""{_market_stance(market)}
오늘은 {today}입니다. {company_name} ({ticker}) 레포트의 **섹션 VI~X만** 작성하세요.
아래 5개 블록을 모두 완성해야 합니다. 섹션 I~V는 이미 작성됐으므로 반복하지 마세요.

## VI. 재무제표 & KPI 분석 (Financials)
[yfinance 연간 실적 3개년 테이블 + 분기 실적 테이블 + 업종 KPI 6개]

## VII. 동종업체 비교 분석 (Peer Analysis)
[핵심 지표 비교 테이블 + 밸류에이션 멀티플 테이블]

## VIII. 리스크 요인 (Risk Factors)
[HIGH 2개 / MED 3개 / LOW 1개]

## IX. 적정주가 산출 (Valuation)
[DCF 가정 테이블 + 민감도 분석 5×5 테이블]

## X. 종합 결론 (Conclusion)
[결론 3~4문장]
**최종 투자의견: [BUY/HOLD/SELL] | 목표주가: {price_ex} (Bull) / {price_ex} (Bear)**
"""


def _market_stance(market: str) -> str:
    """리포트 프롬프트 앞머리에 붙일 시장 관점.

    이걸 붙이지 않으면 모델이 기본값처럼 미국 시장을 전제로 쓴다 — 한국 산업
    리포트인데 비교 대상이 미국 기업이 되고, 금액도 달러로 나온다.
    """
    from backend.services.markets import normalize
    if normalize(market) == "KR":
        return (
            "**분석 관점: 한국 주식시장.** 상장 시장은 KOSPI/KOSDAQ 기준이고 금액은 원화(₩)로 씁니다.\n"
            "비교 대상·경쟁사·예시는 **국내 상장 종목만** 사용하세요. 미국 기업을 예로 들지 마세요.\n"
            "환율(원/달러), 수출 실적, 외국인·기관 수급처럼 한국 시장에서 실제로 작동하는 변수를 우선 다루세요.\n"
        )
    return (
        "**분석 관점: 미국 주식시장.** 상장 시장은 NYSE/NASDAQ 기준이고 금액은 달러($)로 씁니다.\n"
        "비교 대상·경쟁사·예시는 미국 상장 종목을 사용하세요.\n"
    )


def _industry_prompt(meta: dict, market: str = "US") -> str:
    """Sonnet 단일 호출용 (8000 토큰)."""
    from backend.services.markets import get_market
    spec = get_market(market)
    exchange_hint = "NYSE/NASDAQ" if market == "US" else "KOSPI/KOSDAQ"
    cur = spec.currency_symbol
    today = datetime.now().strftime("%Y년 %m월 %d일")
    return f"""{_market_stance(market)}
오늘은 {today}입니다. **{meta['name_kr']} ({meta['name_en']})** 산업 레포트를 아래 9개 섹션 순서대로 빠짐없이 작성하세요.
yfinance 수치는 그대로 인용[A], 추정치는 [E] 표시. 마지막 섹션(IX)까지 반드시 완성하세요.

## HEADER
의견: [OVERWEIGHT/NEUTRAL/UNDERWEIGHT]
12M수익률: [+XX.X% or −XX.X%] [E]
Bull: [+XX%]
Bear: [−XX%]
슬로건: {meta['tagline']}
벤치마크: {meta['benchmark']}
커버리지: {meta['coverage']}
KEY_HIGHLIGHT_1: [최신 수치 포함 핵심 지표 1]
KEY_HIGHLIGHT_2: [최신 수치 포함 핵심 지표 2]
KEY_HIGHLIGHT_3: [최신 수치 포함 핵심 지표 3]
KEY_HIGHLIGHT_4: [최신 수치 포함 핵심 지표 4]
KEY_HIGHLIGHT_5: [최신 수치 포함 핵심 지표 5]

## II. Executive Summary (투자요약)
[산업 현황 2~3문장]

| 구분 | 수혜 | 중립 | 소외 |
| --- | --- | --- | --- |
| 기업 | [수혜 기업] | [중립 기업] | [소외 기업] |

## III. 현재 시황 & 산업 동향 (Market Context)
[산업 지수 흐름 및 최근 주요 이벤트 — 2~3문단]

## IV. 밸류체인 맵 & 수혜/소외 매트릭스
[업스트림/미드스트림/다운스트림 테이블 + 수혜/소외 매트릭스 테이블]

## V. 핵심 동인 분석 (Key Drivers)
[수요/공급/정책 3가지 동인 — 각 2~3문단]

## VI. 산업 KPI 대시보드
[KPI 5개 항목 테이블 — 현재값/추세/해석]

## VII. Bull/Bear 시나리오 & 종목 워치리스트
[Bull/Bear 비교 테이블 + 종목 워치리스트 테이블]

## VIII. 리스크 요인 (Risk Factors)
[HIGH/MED/LOW 리스크 — 각 2~3문장]

## IX. 투자 결론 (Conclusion)
[종합 결론 3~4문장 + 최종 의견]
"""


def _industry_prompt_part1(meta: dict, market: str = "US") -> str:
    """Haiku Phase-1: HEADER + II~V."""
    from backend.services.markets import get_market
    spec = get_market(market)
    exchange_hint = "NYSE/NASDAQ" if market == "US" else "KOSPI/KOSDAQ"
    cur = spec.currency_symbol
    today = datetime.now().strftime("%Y년 %m월 %d일")
    return f"""{_market_stance(market)}
오늘은 {today}입니다. **{meta['name_kr']}** 산업 레포트의 **HEADER와 섹션 II~V만** 작성하세요.
아래 5개 블록을 모두 완성해야 합니다. 섹션 VI 이후는 쓰지 마세요.

## HEADER
의견: [OVERWEIGHT/NEUTRAL/UNDERWEIGHT]
12M수익률: [+XX.X% or −XX.X%] [E]
Bull: [+XX%]
Bear: [−XX%]
슬로건: {meta['tagline']}
벤치마크: {meta['benchmark']}
커버리지: {meta['coverage']}
KEY_HIGHLIGHT_1: [최신 수치 포함 핵심 지표 1]
KEY_HIGHLIGHT_2: [최신 수치 포함 핵심 지표 2]
KEY_HIGHLIGHT_3: [최신 수치 포함 핵심 지표 3]
KEY_HIGHLIGHT_4: [최신 수치 포함 핵심 지표 4]
KEY_HIGHLIGHT_5: [최신 수치 포함 핵심 지표 5]

## II. Executive Summary (투자요약)
[산업 현황 2~3문장]

| 구분 | 수혜 | 중립 | 소외 |
| --- | --- | --- | --- |
| 기업 | [수혜 기업] | [중립 기업] | [소외 기업] |

## III. 현재 시황 & 산업 동향 (Market Context)
[산업 지수 흐름 및 최근 주요 이벤트 — 2~3문단]

## IV. 밸류체인 맵 & 수혜/소외 매트릭스
[업스트림/미드스트림/다운스트림 테이블 + 수혜/소외 매트릭스 테이블]

## V. 핵심 동인 분석 (Key Drivers)
[수요/공급/정책 3가지 동인 — 각 2~3문단]
"""


def _industry_prompt_part2(meta: dict, market: str = "US") -> str:
    """Haiku Phase-2: VI~IX."""
    from backend.services.markets import get_market
    spec = get_market(market)
    exchange_hint = "NYSE/NASDAQ" if market == "US" else "KOSPI/KOSDAQ"
    cur = spec.currency_symbol
    today = datetime.now().strftime("%Y년 %m월 %d일")
    return f"""{_market_stance(market)}
오늘은 {today}입니다. **{meta['name_kr']}** 산업 레포트의 **섹션 VI~IX만** 작성하세요.
아래 4개 블록을 모두 완성해야 합니다. 섹션 I~V는 이미 작성됐으므로 반복하지 마세요.

## VI. 산업 KPI 대시보드
[KPI 5개 항목 테이블 — 현재값/추세/해석]

## VII. Bull/Bear 시나리오 & 종목 워치리스트
[Bull/Bear 비교 테이블 + 종목 워치리스트 테이블]

## VIII. 리스크 요인 (Risk Factors)
[HIGH/MED/LOW 리스크 — 각 2~3문장]

## IX. 투자 결론 (Conclusion)
[종합 결론 3~4문장 + 최종 의견]
"""


# ── 레포트 파이프라인 ─────────────────────────────────────────────────────────────

def write_equity_report(
    ticker: str, model_tier: str = "basic",
    should_cancel: Optional[Callable[[], bool]] = None,
    market: str = "US",
) -> dict:
    """종목 리서치 레포트 생성.
    model_tier: "basic" → GPT-5.6 Sol 2-phase (각 4096 토큰)
                "deep"  → Claude Haiku 2-phase (각 4096 토큰)

    should_cancel 이 주어지면 각 단계 사이와 LLM 스트리밍 도중에 확인해,
    사용자가 중단하면 JobCancelled 를 올리고 즉시 빠져나온다.
    """
    ticker = ticker.upper()

    def _check() -> None:
        if should_cancel is not None and should_cancel():
            raise JobCancelled()

    # 1) yfinance 데이터
    _check()
    company_name, yf_text, raw_dict = gather_equity_yfinance(ticker, market)

    # 2) Perplexity 뉴스
    _check()
    news_text = gather_equity_perplexity(ticker, company_name, market)

    _check()

    # 예전에는 Haiku 로 yf_text 를 한 번 더 요약한 뒤, 원본과 요약본을 **둘 다**
    # 컨텍스트에 넣었다. 같은 숫자가 두 형태로 중복되는 데다 요약 호출 자체가
    # 추가 비용이었다. yf_text 는 이미 정형화된 지표 목록이라 요약이 정보를 늘리지
    # 않으므로 원본만 전달한다.
    context_deep = (
        f"[Market data — yfinance]\n{yf_text}\n\n"
        f"[Recent news & analyst view — Perplexity]\n"
        f"{news_text if news_text else NO_NEWS_NOTICE}"
    )

    _write = _call_haiku if model_tier == "basic" else _call_sonnet
    p1 = _write(
        f"{context_deep}\n\n{_equity_prompt_part1(ticker, company_name, market)}",
        EQUITY_SYSTEM_PROMPT, max_tokens=4096, should_cancel=should_cancel,
    )
    p2 = _write(
        f"{context_deep}\n\n{_equity_prompt_part2(ticker, company_name, market)}",
        EQUITY_SYSTEM_PROMPT, max_tokens=4096, should_cancel=should_cancel,
    )
    raw = p1.strip() + "\n\n" + p2.strip()

    sections = _parse_sections(raw)
    return {
        "ticker":       ticker,
        "company_name": company_name,
        "raw":          raw,
        "sections":     {k: v for k, v in sections.items() if not k.startswith("_")},
        "market_data":  raw_dict,
        "model_tier":   model_tier,
        "market":       market,
    }


def write_industry_report(
    industry_id: str, model_tier: str = "basic",
    should_cancel: Optional[Callable[[], bool]] = None,
    market: str = "US",
) -> dict:
    """산업 리서치 레포트 생성.
    model_tier: "basic" → GPT-5.6 Sol 2-phase (각 4096 토큰)
                "deep"  → Claude Haiku 2-phase (각 4096 토큰)

    should_cancel 은 write_equity_report 와 같은 역할이다.
    """
    if industry_meta(industry_id) is None:
        raise ValueError(f"지원하지 않는 산업: {industry_id}")
    meta = industry_meta(industry_id)

    def _check() -> None:
        if should_cancel is not None and should_cancel():
            raise JobCancelled()

    # 1) yfinance 데이터
    _check()
    yf_text, raw_dict = gather_industry_yfinance(meta, market)

    # 2) Perplexity 뉴스
    _check()
    news_text = gather_industry_perplexity(meta, market)

    _check()

    context = (
        f"【yfinance 실제 데이터】\n{yf_text}\n\n"
        f"【최신 뉴스·트렌드·규제 (Perplexity)】\n"
        f"{news_text if news_text else NO_NEWS_NOTICE}"
    )

    _write = _call_haiku if model_tier == "basic" else _call_sonnet
    p1 = _write(
        f"{context}\n\n{_industry_prompt_part1(meta, market)}",
        INDUSTRY_SYSTEM_PROMPT, max_tokens=4096, should_cancel=should_cancel,
    )
    p2 = _write(
        f"{context}\n\n{_industry_prompt_part2(meta, market)}",
        INDUSTRY_SYSTEM_PROMPT, max_tokens=4096, should_cancel=should_cancel,
    )
    raw = p1.strip() + "\n\n" + p2.strip()

    sections = _parse_sections(raw)
    return {
        "industry_id":      industry_id,
        "industry_name_kr": meta["name_kr"],
        "industry_name_en": meta["name_en"],
        "raw":              raw,
        "sections":         {k: v for k, v in sections.items() if not k.startswith("_")},
        "market_data":      raw_dict,
        "model_tier":       model_tier,
    }


# ── 공통 유틸 ────────────────────────────────────────────────────────────────────

def _parse_sections(raw: str) -> dict:
    sections: dict = {}
    cur = "header"
    buf: list[str] = []
    for line in raw.split("\n"):
        if line.startswith("## "):
            if buf:
                content = "\n".join(buf).strip()
                # 이미 내용이 있는 섹션은 빈 내용으로 덮어쓰지 않음
                if cur not in sections or (content and not sections[cur]):
                    sections[cur] = content
            cur = line[3:].strip().lower().replace(" ", "_")
            buf = []
        else:
            buf.append(line)
    if buf:
        content = "\n".join(buf).strip()
        if cur not in sections or (content and not sections[cur]):
            sections[cur] = content
    sections["_raw"] = raw
    return sections

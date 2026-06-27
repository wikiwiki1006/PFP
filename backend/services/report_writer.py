"""
backend/services/report_writer.py
────────────────────────────────────
LENS 종목·산업 리서치 레포트 AI 집필 서비스
(pfp/report_writer.py + pfp/industry_report_writer.py 이식)
"""
from __future__ import annotations

import os
import requests
from datetime import datetime

# ── 시스템 프롬프트 ──────────────────────────────────────────────────────────────

EQUITY_SYSTEM_PROMPT = """당신은 LENS CAPITAL RESEARCH의 수석 애널리스트입니다.
반드시 아래 형식을 정확히 지켜서 레포트를 작성하세요.
- 각 섹션은 반드시 ## 섹션키 로 시작
- 표는 반드시 마크다운 표 형식
- 수치는 [A] 공시확인 / [E] 추정 표시
- 절대로 섹션 순서를 바꾸거나 섹션을 합치지 마세요
- 반드시 웹서치를 통해 오늘 날짜 기준 최신 데이터를 사용하세요
"""

INDUSTRY_SYSTEM_PROMPT = """당신은 LENS CAPITAL RESEARCH의 수석 산업 애널리스트입니다.
다음 형식을 정확히 지켜서 산업 리서치 레포트를 작성하세요.
- 각 섹션은 반드시 ## 섹션명 으로 시작
- 표는 반드시 마크다운 표 형식 (| 헤더 | ... |)
- 수치는 [A] 공시확인 / [E] 추정 표시
- 섹션 순서를 바꾸거나 섹션을 합치지 마세요
- 반드시 웹서치를 통해 오늘 날짜 기준 최신 데이터를 사용하세요
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


# ── 종목 레포트 ──────────────────────────────────────────────────────────────────

def write_equity_report(ticker: str, company_name: str) -> dict:
    """종목 리서치 레포트 생성. Returns parsed sections dict with '_raw' key."""
    ak = os.getenv("ANTHROPIC_API_KEY", "")
    gk = os.getenv("GEMINI_API_KEY", "")
    if ak:
        return _equity_claude(ticker, company_name, ak)
    elif gk:
        return _equity_gemini(ticker, company_name, gk)
    else:
        raise RuntimeError("API 키가 없습니다. ANTHROPIC_API_KEY 또는 GEMINI_API_KEY를 .env에 설정하세요.")


def _equity_prompt(ticker: str, company_name: str) -> str:
    today = datetime.now().strftime("%Y년 %m월 %d일")
    return f"""오늘은 {today}입니다. 웹서치를 적극 활용해서 {company_name} ({ticker})의 최신 데이터를 수집한 후 아래 형식으로 레포트를 작성하세요.

반드시 다음을 웹서치로 확인하세요:
- 현재 주가 및 시가총액 (오늘 기준)
- 최근 분기 실적 (가장 최근 어닝 발표)
- 최신 애널리스트 목표주가 컨센서스
- 최근 3개월 내 주요 뉴스 및 이벤트
- 최신 연간 재무제표 수치

## HEADER
투자의견: [BUY/HOLD/SELL]
현재주가: $XXX.XX [A]
시가총액: $XXXB [A]
Bull 목표주가: $XXX
Bear 목표주가: $XXX
슬로건: [기업 핵심 투자포인트 한 줄 — 구체적 수치 포함]
KEY_HIGHLIGHT_1: [수치 포함 핵심 지표 1]
KEY_HIGHLIGHT_2: [수치 포함 핵심 지표 2]
KEY_HIGHLIGHT_3: [수치 포함 핵심 지표 3]
KEY_HIGHLIGHT_4: [수치 포함 핵심 지표 4]
KEY_HIGHLIGHT_5: [수치 포함 핵심 지표 5]
거래소: [NYSE/NASDAQ]
업종: [업종명]
CEO: [CEO 이름]
본사: [도시, 국가]

## II. 투자의견 요약 (Executive Summary)
| 구분 | 🐂 Bull Case | 🐻 Bear Case |
| --- | --- | --- |
| 투자의견 | BUY | HOLD |
| 목표주가 | $XXX (범위: $XXX~$XXX) | $XXX (범위: $XXX~$XXX) |
| 현재주가 | $XXX.XX | $XXX.XX |

**핵심 요약 (Key Highlights)**
• [수치 포함 핵심 지표 1]
• [수치 포함 핵심 지표 2]
• [수치 포함 핵심 지표 3]

## III. 기업 개요 (Company Overview)
[설립연도, 본사, 핵심 사업모델, 매출 규모, 주요 고객 — 3~4문장]

| 구분 | 매출비중(추정) | 핵심 제품/서비스 | 주요 고객 |
| --- | --- | --- | --- |
| [사업부 1] | XX% | [핵심 제품] | [주요 고객] |

## IV. 시황·업종·산업 분석 (Top-down Analysis)
[산업 트렌드 5개 항목 — 각 2~3문장]

## V. 투자 근거 (Investment Points)
[투자 포인트 6개 항목 — 각 3~4문장]

## VI. 재무제표 & KPI 분석 (Financials)
[연간 실적 3개년 테이블 + 분기 실적 4분기 테이블 + 업종 특화 KPI 6개]

## VII. 동종업체 비교 분석 (Peer Analysis)
[핵심 지표 비교 + 밸류에이션 멀티플 비교]

## VIII. 리스크 요인 (Risk Factors)
[HIGH 2개 / MED 3개 / LOW 1개]

## IX. 적정주가 산출 (Valuation)
[DCF 분석 가정 테이블 + 민감도 분석 5×5 테이블]

## X. 종합 결론 (Conclusion)
[종합 결론 3~4문장]
**최종 투자의견: [BUY/HOLD/SELL] | 목표주가: $XXX (Bull) / $XXX (Bear)**
"""


def _equity_claude(ticker: str, company_name: str, api_key: str) -> dict:
    from anthropic import Anthropic
    client = Anthropic(api_key=api_key)

    search_resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{"role": "user", "content":
            f"다음 정보를 웹서치해서 수집해줘 (오늘 날짜 기준):\n"
            f"1. {ticker} 현재 주가, 시가총액\n"
            f"2. {company_name} 가장 최근 분기 실적\n"
            f"3. 최근 3개년 연간 재무 요약\n"
            f"4. 애널리스트 컨센서스 목표주가\n"
            f"5. 최근 3개월 주요 뉴스/이벤트\n"
            f"6. 주요 경쟁사 및 밸류에이션 멀티플\n"
            f"수집한 데이터를 구조화해서 정리해줘."}],
    )
    search_ctx = "".join(b.text for b in search_resp.content if hasattr(b, "text"))

    write_resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=16000,
        system=EQUITY_SYSTEM_PROMPT,
        messages=[{"role": "user", "content":
            f"아래는 방금 웹서치로 수집한 {company_name} ({ticker}) 최신 데이터야.\n"
            f"=== 수집된 최신 데이터 ===\n{search_ctx}\n========================\n\n"
            f"{_equity_prompt(ticker, company_name)}"}],
    )
    return _parse_sections(write_resp.content[0].text)


def _equity_gemini(ticker: str, company_name: str, api_key: str) -> dict:
    resp = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}",
        json={
            "contents": [{"parts": [{"text": _equity_prompt(ticker, company_name)}]}],
            "systemInstruction": {"parts": [{"text": EQUITY_SYSTEM_PROMPT}]},
            "generationConfig": {"maxOutputTokens": 16000, "temperature": 0.3},
        },
        timeout=180,
    )
    resp.raise_for_status()
    return _parse_sections(resp.json()["candidates"][0]["content"]["parts"][0]["text"])


# ── 산업 레포트 ──────────────────────────────────────────────────────────────────

def write_industry_report(industry_id: str) -> dict:
    """산업 리서치 레포트 생성."""
    if industry_id not in INDUSTRIES:
        raise ValueError(f"지원하지 않는 산업: {industry_id}")
    meta = INDUSTRIES[industry_id]
    ak = os.getenv("ANTHROPIC_API_KEY", "")
    gk = os.getenv("GEMINI_API_KEY", "")
    if ak:
        return _industry_claude(meta, ak)
    elif gk:
        return _industry_gemini(meta, gk)
    else:
        raise RuntimeError("API 키가 없습니다.")


def _industry_prompt(meta: dict) -> str:
    today = datetime.now().strftime("%Y년 %m월 %d일")
    return f"""오늘은 {today}입니다. 웹서치로 최신 데이터를 수집한 후 **{meta['name_kr']} ({meta['name_en']})** 산업 리포트를 작성하세요.

반드시 다음을 웹서치로 확인하세요:
- 관련 ETF/지수({meta['benchmark']}) 최근 12개월 수익률
- 주요 기업({meta['coverage']}) 최근 실적 및 주가 동향
- 산업 핵심 KPI (시장규모, 성장률, 가동률 등)
- 최근 3개월 주요 뉴스 및 이벤트

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
[산업 현황 2~3문장 + 수혜/소외 매트릭스 테이블]

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


def _industry_claude(meta: dict, api_key: str) -> dict:
    from anthropic import Anthropic
    client = Anthropic(api_key=api_key)

    search_resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{"role": "user", "content":
            f"다음 정보를 웹서치해서 수집해줘 ({meta['name_kr']} 산업, 오늘 날짜 기준):\n"
            f"1. {meta['benchmark']} ETF 최근 12개월 수익률\n"
            f"2. 주요 기업 ({meta['coverage']}) 최근 실적 및 주가 동향\n"
            f"3. 산업 시장규모, CAGR, 주요 성장 동인\n"
            f"4. 최근 3개월 주요 뉴스 및 이벤트\n"
            f"5. Bull/Bear 시나리오 근거\n"
            f"수집한 데이터를 구조화해서 정리해줘."}],
    )
    search_ctx = "".join(b.text for b in search_resp.content if hasattr(b, "text"))

    write_resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=16000,
        system=INDUSTRY_SYSTEM_PROMPT,
        messages=[{"role": "user", "content":
            f"=== 수집된 최신 데이터 ===\n{search_ctx}\n========================\n\n"
            f"{_industry_prompt(meta)}"}],
    )
    return _parse_sections(write_resp.content[0].text)


def _industry_gemini(meta: dict, api_key: str) -> dict:
    resp = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}",
        json={
            "contents": [{"parts": [{"text": _industry_prompt(meta)}]}],
            "systemInstruction": {"parts": [{"text": INDUSTRY_SYSTEM_PROMPT}]},
            "generationConfig": {"maxOutputTokens": 16000, "temperature": 0.3},
        },
        timeout=180,
    )
    resp.raise_for_status()
    return _parse_sections(resp.json()["candidates"][0]["content"]["parts"][0]["text"])


# ── 공통 유틸 ────────────────────────────────────────────────────────────────────

def _parse_sections(raw: str) -> dict:
    sections, cur, buf = {}, "header", []
    for line in raw.split("\n"):
        if line.startswith("## "):
            if buf:
                sections[cur] = "\n".join(buf).strip()
            cur = line[3:].strip().lower().replace(" ", "_")
            buf = []
        else:
            buf.append(line)
    if buf:
        sections[cur] = "\n".join(buf).strip()
    sections["_raw"] = raw
    return sections

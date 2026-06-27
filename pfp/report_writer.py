import os, requests, pathlib
from dotenv import load_dotenv

load_dotenv(dotenv_path=pathlib.Path(__file__).parent / '.env', override=True)
GEMINI_API_KEY    = os.getenv("GEMINI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

SYSTEM_PROMPT = """당신은 LENS CAPITAL RESEARCH의 수석 애널리스트입니다.
반드시 아래 형식을 정확히 지켜서 레포트를 작성하세요.
- 각 섹션은 반드시 ## 섹션키 로 시작
- 표는 반드시 마크다운 표 형식
- 수치는 [A] 공시확인 / [E] 추정 표시
- 절대로 섹션 순서를 바꾸거나 섹션을 합치지 마세요
- 반드시 웹서치를 통해 오늘 날짜 기준 최신 데이터를 사용하세요
"""

def write_report(ticker: str, company_name: str, collected_data: dict) -> dict:
    load_dotenv(dotenv_path=pathlib.Path(__file__).parent / '.env', override=True)
    ak = os.getenv("ANTHROPIC_API_KEY", "")
    gk = os.getenv("GEMINI_API_KEY", "")
    if ak:
        print("[Claude] 웹서치 + 집필 중...")
        return _claude_with_search(ticker, company_name, ak)
    elif gk:
        print("[Gemini] 집필 중...")
        return _gemini(ticker, company_name, gk)
    else:
        raise Exception("API 키가 없습니다. .env 파일을 확인하세요.")


def _prompt(ticker, company_name):
    from datetime import datetime
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
| 시가총액 | $XXXB | $XXXB |

**핵심 요약 (Key Highlights)**
• [수치 포함 핵심 지표 1 — 의미 설명]
• [수치 포함 핵심 지표 2 — 의미 설명]
• [수치 포함 핵심 지표 3 — 의미 설명]
• [수치 포함 핵심 지표 4 — 의미 설명]
• [수치 포함 핵심 지표 5 — 의미 설명]

## III. 기업 개요 (Company Overview)
[설립연도, 본사, 핵심 사업모델, 매출 규모, 주요 고객 — 3~4문장]

| 구분 | 매출비중(추정) | 핵심 제품/서비스 | 주요 고객 |
| --- | --- | --- | --- |
| [사업부 1] | XX% | [핵심 제품] | [주요 고객] |
| [사업부 2] | XX% | [핵심 제품] | [주요 고객] |
| [사업부 3] | XX% | [핵심 제품] | [주요 고객] |
| 합계 | $XX.XB | [CEO명] │ [거래소: TICKER] | |

## IV. 시황·업종·산업 분석 (Top-down Analysis)
**① [산업 트렌드 1 제목]**
[시장규모, 성장률, 구조적 이유 — 2~3문장]

**② [산업 트렌드 2 제목]**
[기술 전환, 경쟁 구도 — 2~3문장]

**③ [산업 트렌드 3 제목]**
[공급망, 가격 결정권 — 2~3문장]

**④ [산업 트렌드 4 제목]**
[정책, 규제 환경 — 2~3문장]

**⑤ [산업 트렌드 5 제목]**
[차세대 기술, 신규 기회 — 2~3문장]

## V. 투자 근거 (Investment Points)
**1. [투자 포인트 1 제목]**
[핵심 수치 + 경쟁 우위 — 3~4문장]

**2. [투자 포인트 2 제목]**
[핵심 수치 + 성장 논거 — 3~4문장]

**3. [투자 포인트 3 제목]**
[수익성/현금 창출력 — 3~4문장]

**4. [투자 포인트 4 제목]**
[재무 건전성/자본배분 — 3~4문장]

**5. [투자 포인트 5 제목]**
[시장 확장/M&A/신사업 — 3~4문장]

**6. [투자 포인트 6 제목]**
[소프트웨어/서비스/반복수익 — 3~4문장]

## VI. 재무제표 & KPI 분석 (Financials)
**1. 연결 실적 핵심 요약 (3개년, 단위: $M)**

| 구분 | FY2022 | FY2023 | FY2024 | YoY(최신) |
| --- | --- | --- | --- | --- |
| 매출 ($M) | | | | |
| 매출 성장률 (%) | | | | |
| 영업이익률 (%) | | | | |
| 순이익 ($M) | | | | |
| EPS ($, 희석) | | | | |
| FCF 마진 (%) | | | | |
| 부채비율 (%) | | | | |
| CAPEX/매출 (%) | | | | |

📌 연결 실적에서 주목할 것
▸ [매출 성장률 트렌드 분석]
▸ [영업이익률 방향 분석]
▸ [FCF 마진 vs 영업이익률 분석]

**2. 최근 4분기 실적**

| 구분 | Q1 | Q2 | Q3 | Q4 |
| --- | --- | --- | --- | --- |
| 매출 ($M) | | | | |
| YoY 성장률 (%) | | | | |
| QoQ 성장률 (%) | | | | |

📌 분기 실적에서 주목할 것
▸ [YoY 성장률 흐름 분석]
▸ [QoQ 흐름 및 계절성 분석]
▸ [Q4 Exit Rate 분석]

**3. 업종 특화 KPI**

| KPI 항목 | 현재값 | 해석/업계 기준 | 신호 |
| --- | --- | --- | --- |
| [KPI 1] | | | ▲/●/▼ |
| [KPI 2] | | | ▲/●/▼ |
| [KPI 3] | | | ▲/●/▼ |
| [KPI 4] | | | ▲/●/▼ |
| [KPI 5] | | | ▲/●/▼ |
| [KPI 6] | | | ▲/●/▼ |

📌 KPI에서 주목할 것
▸ [가장 강력한 ▲ 신호 항목]
▸ [▼ 또는 ● 신호 모니터링 항목]
▸ [핵심 KPI 종합 해석]

## VII. 동종업체 비교 분석 (Peer Analysis)
**1. 핵심 지표 비교**

| 항목 | {ticker} | 피어1 | 피어2 | 피어3 |
| --- | --- | --- | --- | --- |
| 매출 성장률 (%) | | | | |
| 영업이익률 (%) | | | | |
| FCF 마진 (%) | | | | |
| ROIC (%) | | | | |
| CAPEX/매출 (%) | | | | |
| 순현금/순부채 | | | | |

📌 피어 비교에서 주목할 것
▸ [당사가 압도적으로 앞서는 항목]
▸ [당사가 뒤지거나 비슷한 항목]
▸ [시장점유율 추이]

**2. 밸류에이션 멀티플 비교**

| 항목 | {ticker} | 피어1 | 피어2 | 피어3 |
| --- | --- | --- | --- | --- |
| Forward P/E | | | | |
| PBR | | | | |
| EV/EBITDA | | | | |
| EV/FCF | | | | |
| PEG | | | | |

📌 밸류에이션에서 주목할 것
▸ [프리미엄/디스카운트 수준]
▸ [PEG 비율 합리성]
▸ [ROIC vs 멀티플 정당화 여부]

## VIII. 리스크 요인 (Risk Factors)
🔴 HIGH

**[리스크 1 제목]**
[리스크 1 본문 — 2~3문장]

**[리스크 2 제목]**
[리스크 2 본문 — 2~3문장]

🟡 MED

**[리스크 3 제목]**
[리스크 3 본문 — 2~3문장]

**[리스크 4 제목]**
[리스크 4 본문 — 2~3문장]

**[리스크 5 제목]**
[리스크 5 본문 — 2~3문장]

🟢 LOW

**[리스크 6 제목]**
[리스크 6 본문 — 2~3문장]

## IX. 적정주가 산출 (Valuation)
**1. DCF 분석 가정**

| 기준연도 | XXXX |
| --- | --- |
| 예측기간 | XXXXE~XXXXE (10년) |
| 할인율 (WACC) | X.X% |
| 영구성장률 (g) | X.X% |
| 순현금 조정 | $XX.XB |
| 주식수 | 약 XX.XB주 |
| DCF 적정주가 | $XXX (현 주가 대비 ±XX%) |

**2. DCF 민감도 분석 (WACC × g)**

| WACC \\ g | 1.0% | 1.5% | 2.0% | 2.5% | 3.0% |
| --- | --- | --- | --- | --- | --- |
| 7.0% | | | | | |
| 8.0% | | | | | |
| 9.0% | | | | | |
| 10.0% | | | | | |
| 11.0% | | | | | |

📌 DCF에서 주목할 것
▸ [Base Case 적정주가 vs 현재주가 괴리]
▸ [WACC 민감도 — 금리 변화 영향]
▸ [영구성장률 가정의 보수성 평가]

## X. 종합 결론 (Conclusion)
[종합 결론 3~4문장 — 핵심 투자 논거 요약]

**최종 투자의견: [BUY/HOLD/SELL] | 목표주가: $XXX (Bull) / $XXX (Bear)**
"""


def _claude_with_search(ticker, company_name, api_key):
    """Claude + web_search 툴로 최신 데이터 수집 + 레포트 집필"""
    from anthropic import Anthropic
    client = Anthropic(api_key=api_key)

    # 1단계: web_search로 최신 데이터 수집
    print(f"[Claude] {company_name} 최신 데이터 웹서치 중...")
    search_prompt = f"""다음 정보를 웹서치해서 수집해줘 (오늘 날짜 기준 최신 데이터):

1. {ticker} 현재 주가, 시가총액
2. {company_name} 가장 최근 분기 실적 (매출, 영업이익, EPS)
3. 최근 3개년 연간 재무 요약
4. 애널리스트 컨센서스 목표주가
5. 최근 3개월 주요 뉴스/이벤트
6. 주요 경쟁사 및 밸류에이션 멀티플

수집한 데이터를 구조화해서 정리해줘."""

    search_response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{"role": "user", "content": search_prompt}]
    )

    # 웹서치 결과 추출
    search_context = ""
    for block in search_response.content:
        if hasattr(block, 'text'):
            search_context += block.text

    print(f"[Claude] 웹서치 완료 ({len(search_context)}자) → 레포트 집필 중...")

    # 2단계: 수집된 데이터 기반으로 레포트 집필
    write_response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": f"""아래는 방금 웹서치로 수집한 {company_name} ({ticker}) 최신 데이터야.
이 데이터를 기반으로 레포트를 작성해줘. 수집된 실제 수치를 반드시 사용해.

=== 수집된 최신 데이터 ===
{search_context}
========================

{_prompt(ticker, company_name)}"""
        }]
    )

    raw = write_response.content[0].text
    print(f"[Claude] 집필 완료 ({len(raw)}자)")
    return _parse(raw)


def _gemini(ticker, company_name, api_key):
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}",
        headers={"Content-Type": "application/json"},
        json={
            "contents": [{"parts": [{"text": _prompt(ticker, company_name)}]}],
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "generationConfig": {"maxOutputTokens": 16000, "temperature": 0.3}
        },
        timeout=120
    )
    if r.status_code != 200:
        raise Exception(f"Gemini 오류: {r.text}")
    raw = r.json()["candidates"][0]["content"]["parts"][0]["text"]
    print(f"[Gemini] 완료 ({len(raw)}자)")
    return _parse(raw)


def _parse(raw):
    sections, cur, buf = {}, "header", []
    for line in raw.split("\n"):
        if line.startswith("## "):
            if buf: sections[cur] = "\n".join(buf).strip()
            cur = line[3:].strip().lower().replace(" ", "_")
            buf = []
        else:
            buf.append(line)
    if buf: sections[cur] = "\n".join(buf).strip()
    sections["_raw"] = raw
    return sections

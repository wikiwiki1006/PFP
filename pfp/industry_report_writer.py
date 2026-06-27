"""
industry_report_writer.py — 산업 리서치 레포트 AI 집필 모듈
"""
import os, pathlib
from datetime import datetime
from dotenv import load_dotenv

load_dotenv(dotenv_path=pathlib.Path(__file__).parent / '.env', override=True)

# ── 커버 가능 산업 목록 ─────────────────────────────────────────────────────────
INDUSTRIES = {
    "ai_infra": {
        "name_kr": "AI 데이터센터 연결 인프라",
        "name_en": "AI Connectivity Infrastructure",
        "tagline": "GPU 클러스터가 커질수록, 병목은 '연결'에서 터진다",
        "benchmark": "SOX / NASDAQ100",
        "coverage": "ALAB, CRDO, NVDA, AVGO, MRVL",
        "icon": "🔗",
    },
    "space": {
        "name_kr": "상업 우주 인프라",
        "name_en": "Commercial Space Infrastructure",
        "tagline": "지구 저궤도가 새로운 통신 고속도로가 된다",
        "benchmark": "S-Network Space Index (UFO)",
        "coverage": "ASTS, RKLB, LUNR, IRDM, PL",
        "icon": "🚀",
    },
    "semiconductor_memory": {
        "name_kr": "메모리 반도체 & HBM",
        "name_en": "Memory Semiconductor & HBM",
        "tagline": "AI HBM 수요가 DRAM 업사이클의 새 법칙을 쓴다",
        "benchmark": "SOX ETF",
        "coverage": "MU, SMCI, LRCX, KLAC, AMAT",
        "icon": "💾",
    },
    "semiconductor_logic": {
        "name_kr": "파운드리 & 로직 반도체",
        "name_en": "Foundry & Logic Semiconductor",
        "tagline": "첨단 공정 독점이 미래 AI 인프라의 열쇠다",
        "benchmark": "SOX ETF",
        "coverage": "TSM, INTC, ASML, AMAT, KLAC",
        "icon": "⚙️",
    },
    "defense": {
        "name_kr": "방산 & 항공우주",
        "name_en": "Defense & Aerospace",
        "tagline": "지정학 리스크가 방산 Capex 확장의 구조적 동력이 된다",
        "benchmark": "ITA ETF",
        "coverage": "LMT, NOC, RTX, HII, KTOS",
        "icon": "🛡️",
    },
    "ev_battery": {
        "name_kr": "전기차 & 배터리",
        "name_en": "EV & Battery",
        "tagline": "배터리 밀도 경쟁이 EV 보급 속도를 결정한다",
        "benchmark": "LIT ETF",
        "coverage": "TSLA, RIVN, NIO, QS, CHPT",
        "icon": "⚡",
    },
    "clean_energy": {
        "name_kr": "클린 에너지 & 태양광",
        "name_en": "Clean Energy & Solar",
        "tagline": "AI 전력 수요가 재생에너지 Capex의 새 엔진이 된다",
        "benchmark": "ICLN ETF",
        "coverage": "FSLR, ENPH, NEE, SEDG, ARRY",
        "icon": "☀️",
    },
    "cloud_saas": {
        "name_kr": "클라우드 & SaaS",
        "name_en": "Cloud & Enterprise SaaS",
        "tagline": "AI 인프라 위에서 소프트웨어 마진이 다시 확장된다",
        "benchmark": "BVP Nasdaq Emerging Cloud Index",
        "coverage": "MSFT, AMZN, GOOGL, NOW, SNOW",
        "icon": "☁️",
    },
    "cybersecurity": {
        "name_kr": "사이버보안",
        "name_en": "Cybersecurity",
        "tagline": "AI 시대의 공격 고도화가 보안 예산 확대를 강제한다",
        "benchmark": "HACK ETF / BUG ETF",
        "coverage": "CRWD, ZS, PANW, FTNT, S",
        "icon": "🔐",
    },
    "biotech": {
        "name_kr": "바이오텍 & 유전자 치료",
        "name_en": "Biotech & Gene Therapy",
        "tagline": "GLP-1·유전자 편집이 의료 패러다임의 전환점을 열다",
        "benchmark": "XBI ETF",
        "coverage": "MRNA, REGN, VRTX, BEAM, CRSP",
        "icon": "🧬",
    },
    "nuclear": {
        "name_kr": "원자력 에너지 & SMR",
        "name_en": "Nuclear Energy & SMR",
        "tagline": "SMR과 AI 전력 수요가 원전 르네상스의 방아쇠를 당긴다",
        "benchmark": "URA ETF",
        "coverage": "CCJ, NNE, LEU, OKLO, SMR",
        "icon": "⚛️",
    },
    "robotics": {
        "name_kr": "로보틱스 & 산업 자동화",
        "name_en": "Robotics & Industrial Automation",
        "tagline": "휴머노이드 로봇이 제조업 노동 방정식을 바꾼다",
        "benchmark": "ROBO ETF",
        "coverage": "ABB, FANUC, IRBT, BRZE, NVDA",
        "icon": "🤖",
    },
    "fintech": {
        "name_kr": "핀테크 & 디지털 결제",
        "name_en": "Fintech & Digital Payments",
        "tagline": "글로벌 결제 인프라의 디지털 전환이 새 수익 엔진이 된다",
        "benchmark": "FINX ETF",
        "coverage": "V, MA, SQ, PYPL, SOFI",
        "icon": "💳",
    },
    "datacenter_reit": {
        "name_kr": "데이터센터 REIT",
        "name_en": "Data Center REIT",
        "tagline": "AI 데이터센터 임대 수요가 REIT 배당 성장을 뒷받침한다",
        "benchmark": "XLRE / VNQ ETF",
        "coverage": "EQIX, DLR, AMT, CCI, CONE",
        "icon": "🏢",
    },
    "healthcare_tech": {
        "name_kr": "헬스케어 테크 & AI 진단",
        "name_en": "Healthcare Technology & AI Diagnostics",
        "tagline": "AI 진단과 디지털 치료가 의료 효율성 혁명을 주도한다",
        "benchmark": "IHF ETF",
        "coverage": "UNH, HCA, ISRG, VEEV, DOCS",
        "icon": "🏥",
    },
}

SYSTEM_PROMPT = """당신은 LENS CAPITAL RESEARCH의 수석 산업 애널리스트입니다.
다음 형식을 정확히 지켜서 산업 리서치 레포트를 작성하세요.
- 각 섹션은 반드시 ## 섹션명 으로 시작
- 표는 반드시 마크다운 표 형식 (| 헤더 | ... |)
- 수치는 [A] 공시확인 / [E] 추정 표시
- 섹션 순서를 바꾸거나 섹션을 합치지 마세요
- 반드시 웹서치를 통해 오늘 날짜 기준 최신 데이터를 사용하세요
"""


def write_industry_report(industry_id: str) -> dict:
    meta = INDUSTRIES[industry_id]
    load_dotenv(dotenv_path=pathlib.Path(__file__).parent / '.env', override=True)
    ak = os.getenv("ANTHROPIC_API_KEY", "")
    gk = os.getenv("GEMINI_API_KEY", "")
    if ak:
        return _claude_industry(meta, ak)
    elif gk:
        return _gemini_industry(meta, gk)
    else:
        raise Exception("API 키가 없습니다. .env 파일을 확인하세요.")


def _prompt(meta: dict) -> str:
    today = datetime.now().strftime("%Y년 %m월 %d일")
    return f"""오늘은 {today}입니다. 웹서치로 최신 데이터를 수집한 후 **{meta['name_kr']} ({meta['name_en']})** 산업 리포트를 작성하세요.

반드시 다음을 웹서치로 확인하세요:
- 관련 ETF/지수({meta['benchmark']}) 최근 12개월 수익률
- 주요 기업({meta['coverage']}) 최근 실적 및 주가 동향
- 산업 핵심 KPI 현황 (시장규모, 성장률, 가동률 등)
- 최근 3개월 주요 뉴스 및 이벤트
- Bull/Bear 시나리오 근거

다음 형식을 정확히 지켜서 작성하세요:

## HEADER
의견: [OVERWEIGHT/NEUTRAL/UNDERWEIGHT]
12M수익률: [+XX.X% or −XX.X%] [E]
Bull: [+XX%]
Bear: [−XX%]
슬로건: {meta['tagline']}
벤치마크: {meta['benchmark']}
커버리지: {meta['coverage']}
KEY_HIGHLIGHT_1: [최신 수치 포함 핵심 지표 1 — 의미 포함]
KEY_HIGHLIGHT_2: [최신 수치 포함 핵심 지표 2 — 의미 포함]
KEY_HIGHLIGHT_3: [최신 수치 포함 핵심 지표 3 — 의미 포함]
KEY_HIGHLIGHT_4: [최신 수치 포함 핵심 지표 4 — 의미 포함]
KEY_HIGHLIGHT_5: [최신 수치 포함 핵심 지표 5 — 의미 포함]

## II. Executive Summary (투자요약)
[산업 현황 2~3문장 요약. 핵심 드라이버와 현재 모멘텀 설명]

| 구분 | 현재값 |
| --- | --- |
| 벤치마크 12M 수익률 | [값 [E]] |
| 주요 산업 KPI | [값 [E]] |
| 시장규모 전망 | [값 [E]] |

수혜 / 소외 미니 매트릭스
| 구분 | 고노출 수혜 | 중노출 | 저노출·소외 |
| --- | --- | --- | --- |
| 대표 종목 | [ticker1, ticker2] | [ticker3, ticker4] | [ticker5, ticker6] |
| 매출 노출도 | 70%+ | 45~70% | 45% 미만 |
| 특징 | [고노출 특징] | [중노출 특징] | [저노출 특징] |

## III. 현재 시황 & 산업 동향 (Market Context)
[산업 지수 흐름 및 최근 주요 이벤트 — 2~3문단]

## IV. 밸류체인 맵 & 수혜/소외 매트릭스 (Value Chain & Beneficiary Map)
밸류체인 맵 (Value Chain Map)
| 업스트림 | 미드스트림 | 다운스트림 |
| --- | --- | --- |
| [업스트림 주체 2~3개] | [미드스트림 주체 2~3개] | [다운스트림 주체 2~3개] |

수혜/소외 매트릭스
| 구분 | 티커 | 노출도 | 코멘트 |
| --- | --- | --- | --- |
| 수혜 高 | [ticker] | ~XX% [E] | [한 줄 설명] |
| 수혜 高 | [ticker] | ~XX% [E] | [한 줄 설명] |
| 중립 | [ticker] | XX~XX% [E] | [한 줄 설명] |
| 중립 | [ticker] | XX~XX% [E] | [한 줄 설명] |
| 소외 | [ticker] | XX% 미만 [E] | [한 줄 설명] |

## V. 핵심 동인 분석 (Key Drivers)
① 수요(Demand) — [수요 동인 제목]
[수요 측면 분석 — 시장규모, 성장률, 구조적 이유 포함. 2~3문단]

② 공급(Supply) — [공급 동인 제목]
[공급 측면 분석 — 가동률, 리드타임, 병목 등 포함. 2~3문단]

③ 정책(Policy) — [정책 동인 제목]
[정책·규제 환경 분석 — 정부 지원, 규제 리스크 등. 2~3문단]

## VI. 산업 KPI 대시보드
| KPI 항목 | 현재값 | 추세 | 해석 |
| --- | --- | --- | --- |
| [KPI 1 이름] | [값 [E]] | ▲/▼/▶ | [해석 한 줄] |
| [KPI 2 이름] | [값 [E]] | ▲/▼/▶ | [해석 한 줄] |
| [KPI 3 이름] | [값 [E]] | ▲/▼/▶ | [해석 한 줄] |
| [KPI 4 이름] | [값 [E]] | ▲/▼/▶ | [해석 한 줄] |
| [KPI 5 이름] | [값 [E]] | ▲/▼/▶ | [해석 한 줄] |

## VII. Bull/Bear 시나리오 & 종목 워치리스트
| 구분 | 🐂 Bull Case | 🐻 Bear Case |
| --- | --- | --- |
| 산업 의견 | OVERWEIGHT 강화 | NEUTRAL로 하향 |
| 12M 산업지수 | [+XX%] | [−XX%] |
| 핵심 근거 | [Bull 근거 2~3줄] | [Bear 근거 2~3줄] |

종목 워치리스트 — 회사 레포트 파생 후보
| 티커 | 노출도 | 포지션 | 비고 |
| --- | --- | --- | --- |
| [ticker1] | XX% [E] | 수혜 高 | [한 줄] |
| [ticker2] | XX% [E] | 수혜 高 | [한 줄] |
| [ticker3] | XX% [E] | 수혜 高 | [한 줄] |
| [ticker4] | XX% [E] | 중립 | [한 줄] |
| [ticker5] | XX% [E] | 소외 | [한 줄] |

## VIII. 리스크 요인 (Risk Factors)
● HIGH
[리스크 1 제목]
[리스크 1 본문 — 2~3문장. 구체적 수치/사례 포함]

● MED
[리스크 2 제목]
[리스크 2 본문 — 2~3문장]

● LOW
[리스크 3 제목]
[리스크 3 본문 — 2~3문장]

## IX. 종합 결론 (Conclusion)
[종합 결론 3~4문장 — 핵심 투자 논거 및 포지셔닝 권고 요약]

산업 의견: [OVERWEIGHT/NEUTRAL/UNDERWEIGHT] | 12M 산업지수: Bull [+XX%] / Bear [−XX%]
"""


def _claude_industry(meta: dict, api_key: str) -> dict:
    from anthropic import Anthropic
    client = Anthropic(api_key=api_key)

    search_prompt = f"""다음 정보를 웹서치해서 수집해줘 (오늘 날짜 기준 최신 데이터):
1. {meta['name_kr']} 관련 ETF/지수({meta['benchmark']}) 최근 12개월 수익률
2. 주요 기업({meta['coverage']}) 최근 실적 및 주가
3. 산업 핵심 KPI (시장규모, 성장률, 가동률 등)
4. 최근 3개월 주요 뉴스·이벤트
5. Bull/Bear 시나리오 근거가 될 핵심 데이터
수집한 데이터를 구조화해서 정리해줘."""

    search_resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=3000,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{"role": "user", "content": search_prompt}],
    )
    ctx = "".join(b.text for b in search_resp.content if hasattr(b, "text"))
    print(f"[Industry] 웹서치 완료 ({len(ctx)}자) → 집필 중...")

    write_resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=12000,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": f"아래는 방금 웹서치로 수집한 최신 데이터야. 이 데이터를 기반으로 레포트를 작성해줘.\n\n=== 수집 데이터 ===\n{ctx}\n==================\n\n{_prompt(meta)}"
        }],
    )
    raw = write_resp.content[0].text
    print(f"[Industry] 집필 완료 ({len(raw)}자)")
    return _parse(raw)


def _gemini_industry(meta: dict, api_key: str) -> dict:
    import requests
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}",
        headers={"Content-Type": "application/json"},
        json={
            "contents": [{"parts": [{"text": _prompt(meta)}]}],
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "generationConfig": {"maxOutputTokens": 12000, "temperature": 0.3},
        },
        timeout=120,
    )
    if r.status_code != 200:
        raise Exception(f"Gemini 오류: {r.text}")
    raw = r.json()["candidates"][0]["content"]["parts"][0]["text"]
    print(f"[Gemini Industry] 완료 ({len(raw)}자)")
    return _parse(raw)


def _parse(raw: str) -> dict:
    sections, cur, buf = {}, "HEADER", []
    for line in raw.split("\n"):
        if line.startswith("## "):
            if buf:
                sections[cur] = "\n".join(buf).strip()
            cur = line[3:].strip()
            buf = []
        else:
            buf.append(line)
    if buf:
        sections[cur] = "\n".join(buf).strip()
    sections["_raw"] = raw
    return sections

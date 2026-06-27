"""
데이터 수집 모듈
현재: Claude 웹서치로 직접 수집
추후: GEMINI_API_KEY / PERPLEXITY_API_KEY 입력하면 자동 전환
"""
import os
import requests

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY", "")


def collect_company_data(ticker: str, company_name: str) -> dict:
    """
    기업 데이터 수집 - API 키 유무에 따라 자동 라우팅
    """
    if PERPLEXITY_API_KEY:
        print(f"[Perplexity] {company_name} 데이터 수집 중...")
        return _collect_via_perplexity(ticker, company_name)
    elif GEMINI_API_KEY:
        print(f"[Gemini] {company_name} 데이터 수집 중...")
        return _collect_via_gemini(ticker, company_name)
    else:
        print(f"[Claude 직접 수집] {company_name} 데이터 수집 중...")
        return _collect_via_claude_search(ticker, company_name)


def _collect_via_perplexity(ticker: str, company_name: str) -> dict:
    """Perplexity API로 수집 - 논문/뉴스/보고서"""
    queries = _build_queries(ticker, company_name)
    results = {}

    for section, query in queries.items():
        try:
            response = requests.post(
                "https://api.perplexity.ai/chat/completions",
                headers={
                    "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "llama-3.1-sonar-large-128k-online",
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "당신은 주식 리서치 데이터 수집 전문가입니다. "
                                "요청된 데이터를 수치, 출처 URL 포함 마크다운 표 형식으로만 반환하세요. "
                                "설명, 의견, 서론 없이 데이터만 반환하세요."
                            )
                        },
                        {"role": "user", "content": query}
                    ],
                    "max_tokens": 2000,
                    "return_citations": True
                },
                timeout=30
            )
            data = response.json()
            results[section] = data["choices"][0]["message"]["content"]
        except Exception as e:
            results[section] = f"수집 실패: {e}"

    return results


def _collect_via_gemini(ticker: str, company_name: str) -> dict:
    """Gemini API로 수집 - 최신 뉴스/통계"""
    queries = _build_queries(ticker, company_name)
    results = {}

    for section, query in queries.items():
        try:
            response = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}",
                headers={"Content-Type": "application/json"},
                json={
                    "contents": [{"parts": [{"text": query}]}],
                    "systemInstruction": {
                        "parts": [{
                            "text": (
                                "주식 리서치 데이터 수집 전문가입니다. "
                                "수치, 출처 URL만 마크다운 표로 반환. 설명 없음."
                            )
                        }]
                    },
                    "generationConfig": {"maxOutputTokens": 2000}
                },
                timeout=30
            )
            data = response.json()
            results[section] = data["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as e:
            results[section] = f"수집 실패: {e}"

    return results


def _collect_via_claude_search(ticker: str, company_name: str) -> dict:
    """Claude가 직접 수집할 데이터 명세서 반환 (report_writer.py에서 처리)"""
    return {
        "mode": "claude_direct",
        "ticker": ticker,
        "company_name": company_name,
        "queries": _build_queries(ticker, company_name)
    }


def _build_queries(ticker: str, company_name: str) -> dict:
    """섹션별 검색 쿼리 생성"""
    return {
        "company_overview": (
            f"{company_name} ({ticker}) 기업 개요: 설립연도, 본사, CEO, 핵심 사업모델, "
            f"사업부별 매출비중(%), 주요 고객. 수치와 출처 URL만 표로 반환."
        ),
        "industry_analysis": (
            f"{company_name} 속한 산업의 2024-2025 최신 트렌드: 시장규모($B), "
            f"CAGR(%), 주요 성장 동인, 규제 환경. 출처 URL 포함 표로 반환."
        ),
        "financials": (
            f"{company_name} ({ticker}) 최근 3개년 재무: 매출($M), 영업이익률(%), "
            f"순이익($M), EPS, FCF마진(%), 부채비율. SEC 공시 기준, 출처 URL 포함."
        ),
        "quarterly": (
            f"{company_name} ({ticker}) 최근 4분기 실적: 분기별 매출($M), "
            f"YoY성장률(%), QoQ성장률(%). 출처 URL 포함 표로 반환."
        ),
        "kpi": (
            f"{company_name} ({ticker}) 업종 핵심 KPI: Deferred Revenue, Backlog, "
            f"주요 운영지표 수치와 YoY변화. 출처 URL 포함."
        ),
        "peers": (
            f"{company_name} ({ticker}) 동종업체 비교: 주요 피어 3개사 매출성장률, "
            f"영업이익률, FCF마진, ROIC, EV/EBITDA, Forward P/E. 출처 URL 포함."
        ),
        "risks": (
            f"{company_name} ({ticker}) 주요 리스크 요인 2025: 경쟁, 규제, 매크로, "
            f"실행 리스크. 심각도(HIGH/MED/LOW) 포함 최신 뉴스 기반으로."
        ),
        "valuation": (
            f"{company_name} ({ticker}) 애널리스트 목표주가, 컨센서스, DCF 가정 "
            f"(WACC, 영구성장률). 최신 증권사 리포트 기반. 출처 URL 포함."
        ),
    }
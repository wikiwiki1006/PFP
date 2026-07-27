"""
backend/services/report_writer.py
────────────────────────────────────
LENS 종목·산업 리서치 레포트 AI 집필 서비스
yfinance 실제 데이터 + Perplexity 뉴스 + Haiku 구조화 + Sonnet 분석
"""
from __future__ import annotations

import os
import re
import requests
from datetime import datetime
from pathlib import Path

import anthropic
import yfinance as yf
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY", "")
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY", "")
TODAY = datetime.now().strftime("%Y년 %m월 %d일")

# ── 시스템 프롬프트 ──────────────────────────────────────────────────────────────

EQUITY_SYSTEM_PROMPT = """당신은 LENS CAPITAL RESEARCH의 수석 애널리스트입니다.
반드시 아래 규칙을 엄수하세요:
1. 지정된 모든 섹션(## 헤더 포함)을 빠짐없이 순서대로 작성하세요.
2. 각 섹션은 반드시 ## 섹션제목 으로 시작하세요. 섹션을 생략하거나 합치지 마세요.
3. 표(Table)는 반드시 마크다운 표 형식(| 헤더 | 헤더 |\\n| --- | --- |\\n| 값 | 값 |)으로 작성하세요.
4. 수치는 [A] 공시확인 / [E] 추정 표시를 반드시 붙이세요.
5. 제공된 yfinance 수치를 그대로 인용하고 추정치에만 [E]를 붙이세요.
6. 마지막 섹션까지 완전하게 마무리하세요. 토큰이 부족하면 내용을 줄이되 섹션 자체는 반드시 포함하세요.
"""

INDUSTRY_SYSTEM_PROMPT = """당신은 LENS CAPITAL RESEARCH의 수석 산업 애널리스트입니다.
반드시 아래 규칙을 엄수하세요:
1. 지정된 모든 섹션(## 헤더 포함)을 빠짐없이 순서대로 작성하세요.
2. 각 섹션은 반드시 ## 섹션제목 으로 시작하세요. 섹션을 생략하거나 합치지 마세요.
3. 표(Table)는 반드시 마크다운 표 형식(| 헤더 | 헤더 |\\n| --- | --- |\\n| 값 | 값 |)으로 작성하세요.
4. 수치는 [A] 공시확인 / [E] 추정 표시를 반드시 붙이세요.
5. 제공된 yfinance 수치를 그대로 인용하고 추정치에만 [E]를 붙이세요.
6. 마지막 섹션까지 완전하게 마무리하세요. 토큰이 부족하면 내용을 줄이되 섹션 자체는 반드시 포함하세요.
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

def _call_claude(model: str, prompt: str, system: str, max_tokens: int) -> str:
    """Claude 호출. 토큰 한도로 잘린 경우 후속 호출로 마무리 문장 복구."""
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    msg = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(
        block.text for block in msg.content
        if getattr(block, "type", None) == "text"
    )

    if msg.stop_reason == "max_tokens" and text.strip():
        try:
            fix = client.messages.create(
                model=model,
                max_tokens=200,
                system=system,
                messages=[
                    {"role": "user",      "content": prompt},
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
            pass

    return text


def _call_haiku(prompt: str, system: str = "", max_tokens: int = 2000) -> str:
    return _call_claude("claude-haiku-4-5-20251001", prompt, system, max_tokens)


def _call_sonnet(prompt: str, system: str = "", max_tokens: int = 8000) -> str:
    return _call_claude("claude-sonnet-4-6", prompt, system, max_tokens)



# ── yfinance 데이터 수집 ──────────────────────────────────────────────────────────

def gather_equity_yfinance(ticker: str) -> tuple[str, str, dict]:
    """yfinance로 종목 데이터 수집.
    Returns (company_name, formatted_text, raw_dict).
    """
    try:
        t = yf.Ticker(ticker)
        info = t.info

        company_name = info.get("longName") or info.get("shortName") or ticker

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
            lines.append(f"현재주가: ${float(price):,.2f} [A]")

        mktcap = raw_dict["marketCap"]
        if mktcap:
            lines.append(f"시가총액: ${float(mktcap)/1e9:.1f}B [A]")

        if raw_dict["trailingPE"]:
            lines.append(f"Trailing P/E: {float(raw_dict['trailingPE']):.1f}x [A]")
        if raw_dict["forwardPE"]:
            lines.append(f"Forward P/E: {float(raw_dict['forwardPE']):.1f}x [E]")
        if raw_dict["trailingEps"]:
            lines.append(f"EPS (TTM): ${float(raw_dict['trailingEps']):.2f} [A]")
        if raw_dict["totalRevenue"]:
            lines.append(f"연매출: ${float(raw_dict['totalRevenue'])/1e9:.2f}B [A]")
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
            lines.append(f"52주 최고: ${float(raw_dict['fiftyTwoWeekHigh']):,.2f}")
        if raw_dict["fiftyTwoWeekLow"]:
            lines.append(f"52주 최저: ${float(raw_dict['fiftyTwoWeekLow']):,.2f}")
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
            lines.append(f"애널리스트 평균목표주가: ${float(raw_dict['targetMeanPrice']):,.2f} [E]")
        if raw_dict["targetHighPrice"]:
            lines.append(f"애널리스트 최고목표주가: ${float(raw_dict['targetHighPrice']):,.2f} [E]")
        if raw_dict["targetLowPrice"]:
            lines.append(f"애널리스트 최저목표주가: ${float(raw_dict['targetLowPrice']):,.2f} [E]")
        if raw_dict["numberOfAnalystOpinions"]:
            lines.append(f"커버리지 애널리스트: {raw_dict['numberOfAnalystOpinions']}명")
        if raw_dict["recommendationKey"]:
            lines.append(f"컨센서스 의견: {str(raw_dict['recommendationKey']).upper()}")

        if annual_data:
            lines.append("")
            lines.append("【연간 실적 (최근 3개년, yfinance)】")
            for year, data in sorted(annual_data.items(), reverse=True):
                parts = [f"  {year}년:"]
                if data.get("total_revenue"):
                    parts.append(f"매출 ${float(data['total_revenue'])/1e9:.2f}B")
                if data.get("gross_profit"):
                    parts.append(f"매출총이익 ${float(data['gross_profit'])/1e9:.2f}B")
                if data.get("operating_income"):
                    parts.append(f"영업이익 ${float(data['operating_income'])/1e9:.2f}B")
                if data.get("net_income"):
                    parts.append(f"순이익 ${float(data['net_income'])/1e9:.2f}B")
                lines.append(" | ".join(parts))

        raw_dict["annual_data"] = annual_data
        return company_name, "\n".join(lines), raw_dict

    except Exception as exc:
        return ticker, f"(yfinance 데이터 수집 오류: {exc})", {}


def gather_industry_yfinance(meta: dict) -> tuple[str, dict]:
    """산업 ETF + 커버리지 종목 yfinance 데이터 수집.
    Returns (formatted_text, raw_dict).
    """
    lines = [f"【{meta['name_kr']} 산업 yfinance 실제 데이터】  기준: {TODAY}"]
    raw: dict = {}

    # 벤치마크 ETF 1년 수익률
    benchmark_str = meta.get("benchmark", "")
    words = re.findall(r"\b[A-Z]{2,6}\b", benchmark_str.upper())
    etf_tickers: list[str] = []
    skip_words = {"ETF", "AND", "THE", "BVP"}
    for w in words:
        if w in skip_words:
            continue
        mapped = _BENCHMARK_ETF_MAP.get(w)
        if mapped and mapped not in etf_tickers:
            etf_tickers.append(mapped)
        elif len(w) <= 5 and w not in etf_tickers:
            etf_tickers.append(w)

    if not etf_tickers:
        etf_tickers = ["SPY"]

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

    lines.append("")
    lines.append("【커버리지 종목 핵심 지표】")
    coverage_data: dict = {}
    for ct in coverage_tickers:
        try:
            info = yf.Ticker(ct).info
            price      = info.get("currentPrice") or info.get("regularMarketPrice")
            mktcap     = info.get("marketCap")
            pe         = info.get("trailingPE")
            rev_growth = info.get("revenueGrowth")
            name       = info.get("shortName") or ct

            parts = [f"  {ct}"]
            if price:
                parts.append(f"주가 ${float(price):,.2f}")
            if mktcap:
                parts.append(f"시총 ${float(mktcap)/1e9:.1f}B")
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
            lines.append(f"  {ct}: 데이터 없음")

    raw["coverage_data"] = coverage_data
    return "\n".join(lines), raw


# ── Perplexity 뉴스 수집 ──────────────────────────────────────────────────────────

def gather_equity_perplexity(ticker: str, company_name: str) -> str:
    """Perplexity sonar로 종목 최신 뉴스·애널리스트 동향 수집."""
    if not PERPLEXITY_API_KEY:
        return ""
    try:
        prompt = f"""오늘 날짜: {TODAY}
종목: {company_name} ({ticker})

아래 정보를 한국어로 수집해주세요. 수치 인용보다는 뉴스와 서사 위주로 작성하세요.

【최근 30일 주요 뉴스 및 이벤트】
- 주요 언론 보도 3~5건 (제목·출처·날짜·핵심 요약 각 1문장)

【애널리스트 의견 변화 (최근 30일)】
- 목표주가 상향/하향 조정, 투자의견 변경 내용

【경쟁사 동향】
- 주요 경쟁사 최근 이슈 1~2건

【산업 트렌드】
- {company_name}과 관련된 최신 산업 트렌드 1~2건"""

        resp = requests.post(
            "https://api.perplexity.ai/chat/completions",
            headers={
                "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "sonar",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 1500,
                "temperature": 0.0,
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception:
        return ""


def gather_industry_perplexity(meta: dict) -> str:
    """Perplexity sonar로 산업 최신 뉴스·트렌드·규제 동향 수집."""
    if not PERPLEXITY_API_KEY:
        return ""
    try:
        prompt = f"""오늘 날짜: {TODAY}
산업: {meta['name_kr']} ({meta['name_en']})
주요 기업: {meta['coverage']}

아래 정보를 한국어로 수집해주세요.

【산업 최신 뉴스 (최근 30일)】
- 주요 언론 보도 3~5건 (제목·출처·날짜·핵심 요약 1문장씩)

【산업 트렌드 및 규제 동향】
- 최근 주요 정책 변화, 규제 이슈, 기술 동향

【핵심 KPI 업데이트】
- 최근 발표된 시장 규모, 성장률, 수요 지표"""

        resp = requests.post(
            "https://api.perplexity.ai/chat/completions",
            headers={
                "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "sonar",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 1500,
                "temperature": 0.0,
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception:
        return ""


# ── 프롬프트 빌더 ─────────────────────────────────────────────────────────────────

def _equity_prompt(ticker: str, company_name: str) -> str:
    """Sonnet 단일 호출용 (8000 토큰)."""
    today = datetime.now().strftime("%Y년 %m월 %d일")
    return f"""오늘은 {today}입니다. {company_name} ({ticker}) 종목 리서치 레포트를 아래 10개 섹션 순서대로 빠짐없이 작성하세요.
yfinance 수치는 그대로 인용[A], 추정치는 [E] 표시. 마지막 섹션(X)까지 반드시 완성하세요.

## HEADER
투자의견: [BUY/HOLD/SELL]
현재주가: $XXX.XX [A]
시가총액: $XXXB [A]
Bull 목표주가: $XXX
Bear 목표주가: $XXX
슬로건: [핵심 투자포인트 한 줄 — 수치 포함]
KEY_HIGHLIGHT_1: [수치 포함 핵심 지표 1]
KEY_HIGHLIGHT_2: [수치 포함 핵심 지표 2]
KEY_HIGHLIGHT_3: [수치 포함 핵심 지표 3]
KEY_HIGHLIGHT_4: [수치 포함 핵심 지표 4]
KEY_HIGHLIGHT_5: [수치 포함 핵심 지표 5]
거래소: [NYSE/NASDAQ]
업종: [업종명]

## II. 투자의견 요약 (Executive Summary)
| 구분 | 🐂 Bull Case | 🐻 Bear Case |
| --- | --- | --- |
| 투자의견 | BUY | HOLD |
| 목표주가 | $XXX | $XXX |
| 현재주가 | $XXX.XX [A] | $XXX.XX [A] |

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
**최종 투자의견: [BUY/HOLD/SELL] | 목표주가: $XXX (Bull) / $XXX (Bear)**
"""


def _equity_prompt_part1(ticker: str, company_name: str) -> str:
    """Haiku Phase-1: HEADER + II~V."""
    today = datetime.now().strftime("%Y년 %m월 %d일")
    return f"""오늘은 {today}입니다. {company_name} ({ticker}) 레포트의 **HEADER와 섹션 II~V만** 작성하세요.
아래 5개 블록을 모두 완성해야 합니다. 섹션 VI 이후는 쓰지 마세요.

## HEADER
투자의견: [BUY/HOLD/SELL]
현재주가: $XXX.XX [A]
시가총액: $XXXB [A]
Bull 목표주가: $XXX
Bear 목표주가: $XXX
슬로건: [핵심 투자포인트 한 줄 — 수치 포함]
KEY_HIGHLIGHT_1: [수치 포함 핵심 지표 1]
KEY_HIGHLIGHT_2: [수치 포함 핵심 지표 2]
KEY_HIGHLIGHT_3: [수치 포함 핵심 지표 3]
KEY_HIGHLIGHT_4: [수치 포함 핵심 지표 4]
KEY_HIGHLIGHT_5: [수치 포함 핵심 지표 5]
거래소: [NYSE/NASDAQ]
업종: [업종명]

## II. 투자의견 요약 (Executive Summary)
| 구분 | 🐂 Bull Case | 🐻 Bear Case |
| --- | --- | --- |
| 투자의견 | BUY | HOLD |
| 목표주가 | $XXX | $XXX |
| 현재주가 | $XXX.XX [A] | $XXX.XX [A] |

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


def _equity_prompt_part2(ticker: str, company_name: str) -> str:
    """Haiku Phase-2: VI~X."""
    today = datetime.now().strftime("%Y년 %m월 %d일")
    return f"""오늘은 {today}입니다. {company_name} ({ticker}) 레포트의 **섹션 VI~X만** 작성하세요.
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
**최종 투자의견: [BUY/HOLD/SELL] | 목표주가: $XXX (Bull) / $XXX (Bear)**
"""


def _industry_prompt(meta: dict) -> str:
    """Sonnet 단일 호출용 (8000 토큰)."""
    today = datetime.now().strftime("%Y년 %m월 %d일")
    return f"""오늘은 {today}입니다. **{meta['name_kr']} ({meta['name_en']})** 산업 레포트를 아래 9개 섹션 순서대로 빠짐없이 작성하세요.
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


def _industry_prompt_part1(meta: dict) -> str:
    """Haiku Phase-1: HEADER + II~V."""
    today = datetime.now().strftime("%Y년 %m월 %d일")
    return f"""오늘은 {today}입니다. **{meta['name_kr']}** 산업 레포트의 **HEADER와 섹션 II~V만** 작성하세요.
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


def _industry_prompt_part2(meta: dict) -> str:
    """Haiku Phase-2: VI~IX."""
    today = datetime.now().strftime("%Y년 %m월 %d일")
    return f"""오늘은 {today}입니다. **{meta['name_kr']}** 산업 레포트의 **섹션 VI~IX만** 작성하세요.
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

def write_equity_report(ticker: str, model_tier: str = "basic") -> dict:
    """종목 리서치 레포트 생성.
    model_tier: "basic" → Haiku 2-phase (각 4096 토큰)
                "deep"  → Sonnet 단일 호출 (8000 토큰)
    """
    ticker = ticker.upper()

    # 1) yfinance 데이터
    company_name, yf_text, raw_dict = gather_equity_yfinance(ticker)

    # 2) Perplexity 뉴스
    news_text = gather_equity_perplexity(ticker, company_name)

    # 3) Haiku — 핵심 지표 구조화 (항상 Haiku, 단순 정리 작업)
    haiku_prompt = f"""다음 yfinance 데이터에서 핵심 재무 지표를 한국어로 간략히 요약해주세요:

{yf_text}

아래 항목으로 정리해주세요:
- 현재 주가 및 시가총액
- 밸류에이션 (Trailing P/E, Forward P/E)
- 수익성 (매출, 영업이익률, 순이익률)
- 성장성 (매출성장률 YoY)
- 기술적 지표 (52주 고/저, 베타)
- 애널리스트 컨센서스 의견 및 목표주가
- 배당 정보 (있는 경우)"""

    structured_data = _call_haiku(haiku_prompt, max_tokens=2000)

    context = (
        f"【yfinance 실제 데이터】\n{yf_text}\n\n"
        f"【구조화된 핵심 지표 요약】\n{structured_data}\n\n"
        f"【최신 뉴스·애널리스트 동향 (Perplexity)】\n"
        f"{news_text if news_text else '(뉴스 데이터 없음 — 학습 지식 활용)'}"
    )

    if model_tier == "deep":
        # Sonnet: 단일 호출, 8000 토큰으로 전체 섹션 완성
        report_prompt = f"{context}\n\n{_equity_prompt(ticker, company_name)}"
        raw = _call_sonnet(report_prompt, EQUITY_SYSTEM_PROMPT, max_tokens=8000)
    else:
        # Haiku: 2-phase — Phase1(HEADER+II~V) + Phase2(VI~X), 각 4096 토큰
        p1 = _call_haiku(
            f"{context}\n\n{_equity_prompt_part1(ticker, company_name)}",
            EQUITY_SYSTEM_PROMPT, max_tokens=4096,
        )
        p2 = _call_haiku(
            f"{context}\n\n{_equity_prompt_part2(ticker, company_name)}",
            EQUITY_SYSTEM_PROMPT, max_tokens=4096,
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
    }


def write_industry_report(industry_id: str, model_tier: str = "basic") -> dict:
    """산업 리서치 레포트 생성.
    model_tier: "basic" → Haiku 2-phase (각 4096 토큰)
                "deep"  → Sonnet 단일 호출 (8000 토큰)
    """
    if industry_id not in INDUSTRIES:
        raise ValueError(f"지원하지 않는 산업: {industry_id}")
    meta = INDUSTRIES[industry_id]

    # 1) yfinance 데이터
    yf_text, raw_dict = gather_industry_yfinance(meta)

    # 2) Perplexity 뉴스
    news_text = gather_industry_perplexity(meta)

    context = (
        f"【yfinance 실제 데이터】\n{yf_text}\n\n"
        f"【최신 뉴스·트렌드·규제 (Perplexity)】\n"
        f"{news_text if news_text else '(뉴스 데이터 없음 — 학습 지식 활용)'}"
    )

    if model_tier == "deep":
        # Sonnet: 단일 호출, 8000 토큰으로 전체 섹션 완성
        report_prompt = f"{context}\n\n{_industry_prompt(meta)}"
        raw = _call_sonnet(report_prompt, INDUSTRY_SYSTEM_PROMPT, max_tokens=8000)
    else:
        # Haiku: 2-phase — Phase1(HEADER+II~V) + Phase2(VI~IX), 각 4096 토큰
        p1 = _call_haiku(
            f"{context}\n\n{_industry_prompt_part1(meta)}",
            INDUSTRY_SYSTEM_PROMPT, max_tokens=4096,
        )
        p2 = _call_haiku(
            f"{context}\n\n{_industry_prompt_part2(meta)}",
            INDUSTRY_SYSTEM_PROMPT, max_tokens=4096,
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
                sections[cur] = "\n".join(buf).strip()
            cur = line[3:].strip().lower().replace(" ", "_")
            buf = []
        else:
            buf.append(line)
    if buf:
        sections[cur] = "\n".join(buf).strip()
    sections["_raw"] = raw
    return sections

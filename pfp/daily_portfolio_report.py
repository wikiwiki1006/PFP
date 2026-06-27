"""
daily_portfolio_report.py — ALPHA TERMINAL 데일리 브리프 엔진

사용법:
  Streamlit: from daily_portfolio_report import generate_daily_report
  CLI:       python daily_portfolio_report.py
"""
from __future__ import annotations

import json
import os
import pathlib
from datetime import datetime, timezone
from typing import Callable

import yfinance as yf
from dotenv import load_dotenv

BASE_DIR = pathlib.Path(__file__).parent
load_dotenv(BASE_DIR / ".env", override=True)

_DATA_DIR = BASE_DIR / "data"
_DB_FILE  = _DATA_DIR / "holdings.json" if (_DATA_DIR / "holdings.json").exists() else BASE_DIR / "holdings.json"


# ── 1단계: 가격 데이터 수집 ───────────────────────────────────────────────────

def _fetch_price_data(holdings: dict) -> dict:
    """보유 종목 + SPY 전일/전전일 종가 및 변동률 수집."""
    tickers = [t for t in holdings if t != "CASH"]
    if not tickers:
        return {}

    fetch_list = list(set(tickers + ["SPY", "^VIX", "^TNX"]))
    df = yf.download(fetch_list, period="5d", auto_adjust=True, progress=False)

    if df.empty:
        return {}

    close = df["Close"].ffill() if isinstance(df.columns, type(df.columns)) and hasattr(df, "columns") else df.ffill()
    if hasattr(close, "columns") and len(close.columns) == 0:
        return {}

    result: dict = {}

    for t in tickers:
        col = t if t in close.columns else None
        if col is None:
            continue
        series = close[col].dropna()
        if len(series) < 2:
            continue

        today_c = float(series.iloc[-1])
        prev_c  = float(series.iloc[-2])
        chg_pct = (today_c / prev_c - 1) * 100 if prev_c else 0.0

        qty      = holdings[t]["q"]
        avg_cost = holdings[t]["avg"]
        result[t] = {
            "close":     today_c,
            "prev":      prev_c,
            "chg_pct":   round(chg_pct, 2),
            "qty":       qty,
            "avg_cost":  avg_cost,
            "pos_val":   round(today_c * qty, 2),
            "day_pnl":   round((today_c - prev_c) * qty, 2),
            "total_pnl": round((today_c - avg_cost) * qty, 2),
            "sector":    holdings[t].get("sector", "N/A"),
        }

    # 벤치마크/매크로
    for meta_key, col in [("SPY", "SPY"), ("VIX", "^VIX"), ("TNX", "^TNX")]:
        if col in close.columns:
            s = close[col].dropna()
            if len(s) >= 2:
                result[f"__{meta_key}"] = {
                    "close":   round(float(s.iloc[-1]), 2),
                    "prev":    round(float(s.iloc[-2]), 2),
                    "chg_pct": round((float(s.iloc[-1]) / float(s.iloc[-2]) - 1) * 100, 2),
                }

    # 전일 날짜
    result["__date"] = close.index[-1].strftime("%Y년 %m월 %d일 (%a)")
    return result


# ── 2단계: 뉴스 수집 ─────────────────────────────────────────────────────────

def _fetch_yf_news(ticker: str, max_items: int = 5) -> list[dict]:
    """Yahoo Finance 뉴스 헤드라인 수집."""
    try:
        items = yf.Ticker(ticker).news or []
        out = []
        for item in items[:max_items]:
            # yfinance 버전별 포맷 대응
            title = item.get("title") or item.get("content", {}).get("title", "")
            pub   = item.get("publisher") or item.get("content", {}).get("provider", {}).get("displayName", "")
            ts    = item.get("providerPublishTime") or 0
            link  = item.get("link") or item.get("content", {}).get("canonicalUrl", {}).get("url", "")
            if title:
                out.append({
                    "title": title,
                    "publisher": pub,
                    "time": datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%m/%d %H:%M") if ts else "—",
                    "link": link,
                })
        return out
    except Exception:
        return []


def _collect_news(price_data: dict, threshold_pct: float = 3.0) -> dict:
    """
    변동성 종목(절대값 threshold 이상) 우선 + 나머지 종목 뉴스 수집.
    반환: { ticker: [{"title", "publisher", "time"}, ...] }
    """
    stock_keys = [k for k in price_data if not k.startswith("__")]

    # 우선순위: 절대 변동률 큰 순
    sorted_tickers = sorted(stock_keys, key=lambda t: abs(price_data[t]["chg_pct"]), reverse=True)

    news: dict = {}
    for t in sorted_tickers:
        news[t] = _fetch_yf_news(t, max_items=5)

    return news


# ── 3단계: Claude 생성 ────────────────────────────────────────────────────────

def _build_prompt(holdings: dict, price_data: dict, news: dict) -> str:
    stock_keys = sorted(
        [k for k in price_data if not k.startswith("__")],
        key=lambda t: price_data[t]["chg_pct"],
    )

    date_str = price_data.get("__date", datetime.now().strftime("%Y년 %m월 %d일"))

    # 포트폴리오 스냅샷 텍스트 구성
    snap_lines = []
    total_val  = sum(price_data[t]["pos_val"] for t in stock_keys)
    total_pnl  = sum(price_data[t]["day_pnl"]  for t in stock_keys)
    cash_val   = holdings.get("CASH", {}).get("q", 0)
    for t in stock_keys:
        d = price_data[t]
        snap_lines.append(
            f"  {t}: 종가 ${d['close']:,.2f}  전일대비 {d['chg_pct']:+.2f}%  "
            f"1일 P&L ${d['day_pnl']:+,.0f}  섹터 {d['sector']}"
        )

    spy_info = price_data.get("__SPY", {})
    spy_line = (
        f"SPY 전일 변동: {spy_info.get('chg_pct', 0):+.2f}%"
        if spy_info else "SPY 데이터 없음"
    )

    vix_info = price_data.get("__VIX", {})
    tnx_info = price_data.get("__TNX", {})
    macro_line = (
        f"VIX: {vix_info.get('close', '?')} ({vix_info.get('chg_pct', 0):+.2f}%)  "
        f"10Y TNX: {tnx_info.get('close', '?')}% ({tnx_info.get('chg_pct', 0):+.2f}%)"
        if vix_info else "매크로 데이터 없음"
    )

    # 뉴스 텍스트 구성
    news_text = ""
    for t in stock_keys:
        items = news.get(t, [])
        if items:
            news_text += f"\n[{t} 관련 뉴스]\n"
            for n in items:
                news_text += f"  - \"{n['title']}\" ({n['publisher']}, {n['time']})\n"

    # 변동성 종목 목록 (>3%)
    big_movers = [t for t in stock_keys if abs(price_data[t]["chg_pct"]) >= 3.0]
    big_movers_str = ", ".join(big_movers) if big_movers else "없음 (전 종목 3% 미만 변동)"

    prompt = f"""아래는 {date_str} 기준 포트폴리오 데이터와 관련 뉴스입니다.

=== 포트폴리오 스냅샷 ===
{chr(10).join(snap_lines)}
전체 주식 평가액: ${total_val:,.0f}  현금: ${cash_val:,.0f}
전일 총 P&L: ${total_pnl:+,.0f}
{spy_line}
매크로 지표: {macro_line}
절대 변동 3% 이상 종목: {big_movers_str}

=== 관련 뉴스 ===
{news_text if news_text.strip() else "수집된 뉴스 없음 — 웹서치로 보완해 주세요."}

=== 지시사항 ===
위 데이터와 추가 웹서치를 결합하여, 아래 형식을 엄격히 따른 월가 인텔리전스 스타일 데일리 브리프를 한국어로 작성하라.
미사여구 없이 핵심만. 섹션 순서·제목·구분선(---)을 정확히 유지할 것.
절대 변동 3% 이상 종목은 반드시 섹션 2에 포함하고, 원인 분석은 뉴스 + 금융공학적 시각으로 작성하라.
섹션 2에 언급되는 종목은 실제 변동률이 큰 순서로 상승/하락 구분하여 배치하라.

=== 출력 형식 ===
# 📊 ALPHA TERMINAL DAILY BRIEF ({date_str} 정산)

## 1. 포트폴리오 전일 요약 (Portfolio Snapshot)
* **최고 상승 종목:** [Ticker] ([+X.XX%])
* **최대 하락 종목:** [Ticker] ([-X.XX%])
* **특이 사항:** 전일 포트폴리오 전체 자산은 벤치마크(S&P 500) 대비 [아웃퍼폼/언더퍼폼] 했습니다. [구체적 수치 포함 1~2문장]

---

## 2. 주요 종목별 등락 원인 분석 (Why It Moved)

[변동성 종목 각각에 대해 아래 블록 반복. 상승은 🚀, 하락은 📉 이모지 사용.]

### 🚀 [Ticker] ([+X.XX%]) — [핵심 이유 한 줄 요약]
* **핵심 원인:** [구체적 원인 2문장]
* **주요 관련 뉴스:**
  - "[헤드라인]" (출처 / 시각)
  - "[헤드라인]" (출처 / 시각)

---

## 3. 오늘 장 시작 전 매크로 및 섹터 헤드업 (Today's Watch Items)
* **[매크로 변수]:** [금리/환율/VIX 등이 보유 종목에 미칠 영향 1문장]
* **[모니터링 리스크]:** [오늘 실적 발표 또는 경제지표가 있는 종목 경고 1문장]
* **[기회 포착]:** [현재 모멘텀 상 단기 주목할 포인트 1문장]
"""
    return prompt


def _generate_with_claude(
    holdings: dict,
    price_data: dict,
    news: dict,
    api_key: str,
    log: Callable[[str], None] | None = None,
) -> str:
    from anthropic import Anthropic

    _log = log or (lambda m: print(f"[Claude] {m}"))
    client = Anthropic(api_key=api_key)

    # Step A: 변동성 종목 웹서치 (>3%)
    big_movers = [
        t for t in price_data
        if not t.startswith("__") and abs(price_data[t]["chg_pct"]) >= 3.0
    ]

    if big_movers:
        _log(f"웹서치: {', '.join(big_movers)} 최신 뉴스 수집 중...")
        search_q = (
            f"다음 주식들의 {price_data.get('__date', '전일')} 주가 급등락 원인을 찾아줘: "
            f"{', '.join(big_movers)}. "
            "각 종목의 핵심 뉴스 헤드라인과 원인 2~3줄로 요약해줘."
        )
        try:
            sr = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=3000,
                tools=[{"type": "web_search_20250305", "name": "web_search"}],
                messages=[{"role": "user", "content": search_q}],
            )
            web_ctx = "\n".join(b.text for b in sr.content if hasattr(b, "text"))
        except Exception as e:
            _log(f"웹서치 오류 (계속 진행): {e}")
            web_ctx = ""
    else:
        web_ctx = ""
        _log("3% 이상 급등락 종목 없음 — 웹서치 스킵")

    # Step B: 브리프 생성
    _log("데일리 브리프 생성 중...")
    base_prompt = _build_prompt(holdings, price_data, news)
    full_prompt = base_prompt
    if web_ctx:
        full_prompt += f"\n\n=== 웹서치 추가 컨텍스트 ===\n{web_ctx}"

    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        system=(
            "당신은 월가 헤지펀드의 수석 포트폴리오 애널리스트입니다. "
            "지정된 마크다운 형식을 정확히 따르고, 미사여구 없이 핵심 수치와 원인만 기술하세요. "
            "최대 2페이지 분량(약 800~1200 토큰)으로 간결하게 작성하세요."
        ),
        messages=[{"role": "user", "content": full_prompt}],
    )
    return resp.content[0].text


def _generate_with_gemini(
    holdings: dict,
    price_data: dict,
    news: dict,
    api_key: str,
    log: Callable[[str], None] | None = None,
) -> str:
    import requests

    _log = log or (lambda m: print(f"[Gemini] {m}"))
    _log("Gemini Flash로 브리프 생성 중...")

    prompt = _build_prompt(holdings, price_data, news)
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.0-flash:generateContent?key={api_key}"
    )
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 3000, "temperature": 0.3},
    }
    resp = requests.post(url, json=body, timeout=120)
    resp.raise_for_status()
    return resp.json()["candidates"][0]["content"]["parts"][0]["text"]


# ── 메인 진입점 ───────────────────────────────────────────────────────────────

def generate_daily_report(
    holdings: dict,
    log: Callable[[str], None] | None = None,
) -> tuple[str, dict]:
    """
    포트폴리오 데일리 브리프를 생성한다.

    Args:
        holdings: {ticker: {q, avg, sector, ...}, ...} dict
        log: 진행 상태를 받는 콜백 (None이면 print)

    Returns:
        (markdown_report_str, price_data_dict)
    """
    load_dotenv(BASE_DIR / ".env", override=True)
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
    gemini_key    = os.getenv("GEMINI_API_KEY", "")

    _log = log or (lambda m: print(f"  {m}"))

    _log("1/3  yfinance 가격 데이터 수집 중...")
    price_data = _fetch_price_data(holdings)
    if not price_data:
        raise RuntimeError("가격 데이터를 가져오지 못했습니다. 인터넷 연결을 확인하세요.")

    _log("2/3  뉴스 헤드라인 수집 중...")
    news = _collect_news(price_data)

    _log("3/3  AI 브리프 생성 중 (약 30~60초)...")
    if anthropic_key:
        report = _generate_with_claude(holdings, price_data, news, anthropic_key, _log)
    elif gemini_key:
        report = _generate_with_gemini(holdings, price_data, news, gemini_key, _log)
    else:
        raise RuntimeError("ANTHROPIC_API_KEY 또는 GEMINI_API_KEY가 .env에 없습니다.")

    return report, price_data


def _load_holdings_from_file() -> dict:
    """CLI 모드: holdings.json에서 보유 종목 로드."""
    for candidate in [_DATA_DIR / "holdings.json", BASE_DIR / "holdings.json"]:
        if candidate.exists():
            with open(candidate, encoding="utf-8") as f:
                raw = json.load(f)
            return raw.get("my_holdings", raw)
    raise FileNotFoundError("holdings.json 파일을 찾을 수 없습니다.")


# ── CLI 진입 ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print("=" * 60)
    print("  ALPHA TERMINAL DAILY BRIEF — CLI 모드")
    print("=" * 60)

    try:
        holdings = _load_holdings_from_file()
        print(f"보유 종목: {[t for t in holdings if t != 'CASH']}")
        report, price_data = generate_daily_report(holdings)

        out_path = BASE_DIR / "outputs" / f"daily_brief_{datetime.now().strftime('%Y%m%d')}.md"
        out_path.parent.mkdir(exist_ok=True)
        out_path.write_text(report, encoding="utf-8")

        print("\n" + "=" * 60)
        print(report)
        print("=" * 60)
        print(f"\n저장 완료: {out_path}")
    except Exception as e:
        print(f"\n[오류] {e}")
        raise

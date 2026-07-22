"""
backend/services/daily_report.py
──────────────────────────────────
데일리 브리프 생성 서비스 (pfp/daily_portfolio_report.py 이식)
"""
from __future__ import annotations

import os
from datetime import datetime, timezone, time as _time
from typing import Callable

import yfinance as yf


def _is_market_open() -> bool:
    """미국 장중 여부 (UTC 기준 13:30~20:00, 평일)."""
    now = datetime.now(timezone.utc)
    if now.weekday() >= 5:
        return False
    return _time(13, 30) <= now.time() < _time(20, 0)


def _fetch_price_data(holdings: dict) -> dict:
    tickers = [t for t in holdings if t != "CASH"]
    if not tickers:
        return {}

    fetch_list = list(set(tickers + ["SPY", "^VIX", "^TNX"]))
    df = yf.download(fetch_list, period="5d", auto_adjust=True, progress=False)
    if df.empty:
        return {}

    import pandas as pd
    close = df["Close"].ffill() if isinstance(df.columns, pd.MultiIndex) else df.ffill()

    # 브리핑은 항상 이전 완료된 거래일 기준 (장중이어도 전날 종가 사용)
    today_utc = datetime.now(timezone.utc).date()
    shift = 1 if close.index[-1].date() >= today_utc else 0

    result: dict = {}

    for t in tickers:
        if t not in close.columns:
            continue
        series = close[t].dropna()
        if len(series) < 2 + shift:
            continue
        today_c = float(series.iloc[-(1 + shift)])
        prev_c  = float(series.iloc[-(2 + shift)])
        chg_pct = (today_c / prev_c - 1) * 100 if prev_c else 0.0
        qty      = holdings[t].get("q", 0)
        avg_cost = holdings[t].get("avg", 0)
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

    for meta_key, col in [("SPY", "SPY"), ("VIX", "^VIX"), ("TNX", "^TNX")]:
        if col in close.columns:
            s = close[col].dropna()
            if len(s) >= 2 + shift:
                result[f"__{meta_key}"] = {
                    "close":   round(float(s.iloc[-(1 + shift)]), 2),
                    "prev":    round(float(s.iloc[-(2 + shift)]), 2),
                    "chg_pct": round((float(s.iloc[-(1 + shift)]) / float(s.iloc[-(2 + shift)]) - 1) * 100, 2),
                }

    result["__date"] = close.index[-(1 + shift)].strftime("%Y년 %m월 %d일 (%a)")
    return result


def _fetch_yf_news(ticker: str, max_items: int = 5) -> list[dict]:
    try:
        items = yf.Ticker(ticker).news or []
        out = []
        for item in items[:max_items]:
            title = item.get("title") or item.get("content", {}).get("title", "")
            pub   = item.get("publisher") or item.get("content", {}).get("provider", {}).get("displayName", "")
            ts    = item.get("providerPublishTime") or 0
            link  = item.get("link") or item.get("content", {}).get("canonicalUrl", {}).get("url", "")
            if title:
                out.append({
                    "title":     title,
                    "publisher": pub,
                    "time":      datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%m/%d %H:%M") if ts else "—",
                    "link":      link,
                })
        return out
    except Exception:
        return []


def _collect_news(price_data: dict) -> dict:
    stock_keys = sorted(
        [k for k in price_data if not k.startswith("__")],
        key=lambda t: abs(price_data[t]["chg_pct"]),
        reverse=True,
    )
    return {t: _fetch_yf_news(t, max_items=5) for t in stock_keys}


def _build_prompt(holdings: dict, price_data: dict, news: dict) -> str:
    stock_keys = sorted(
        [k for k in price_data if not k.startswith("__")],
        key=lambda t: price_data[t]["chg_pct"],
    )
    date_str   = price_data.get("__date", datetime.now().strftime("%Y년 %m월 %d일"))
    total_val  = sum(price_data[t]["pos_val"] for t in stock_keys)
    total_pnl  = sum(price_data[t]["day_pnl"]  for t in stock_keys)
    cash_val   = holdings.get("CASH", {}).get("q", 0)

    snap_lines = [
        f"  {t}: 종가 ${price_data[t]['close']:,.2f}  전일대비 {price_data[t]['chg_pct']:+.2f}%  "
        f"1일 P&L ${price_data[t]['day_pnl']:+,.0f}  섹터 {price_data[t]['sector']}"
        for t in stock_keys
    ]

    spy_info   = price_data.get("__SPY", {})
    vix_info   = price_data.get("__VIX", {})
    tnx_info   = price_data.get("__TNX", {})
    spy_line   = f"SPY 전일 변동: {spy_info.get('chg_pct', 0):+.2f}%" if spy_info else "SPY 데이터 없음"
    macro_line = (
        f"VIX: {vix_info.get('close','?')} ({vix_info.get('chg_pct',0):+.2f}%)  "
        f"10Y TNX: {tnx_info.get('close','?')}% ({tnx_info.get('chg_pct',0):+.2f}%)"
    ) if vix_info else "매크로 데이터 없음"

    news_text = ""
    for t in stock_keys:
        items = news.get(t, [])
        if items:
            news_text += f"\n[{t} 관련 뉴스]\n"
            for n in items:
                news_text += f"  - \"{n['title']}\" ({n['publisher']}, {n['time']})\n"

    big_movers = [t for t in stock_keys if abs(price_data[t]["chg_pct"]) >= 3.0]
    big_movers_str = ", ".join(big_movers) if big_movers else "없음 (전 종목 3% 미만 변동)"

    return f"""아래는 {date_str} 기준 포트폴리오 데이터와 관련 뉴스입니다.

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

=== 출력 형식 ===
# 📊 ALPHA TERMINAL DAILY BRIEF ({date_str} 정산(미국 동부시간))

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

---

## 3. 오늘 장 시작 전 매크로 및 섹터 헤드업 (Today's Watch Items)
* **[매크로 변수]:** [금리/환율/VIX 등이 보유 종목에 미칠 영향 1문장]
* **[모니터링 리스크]:** [오늘 실적 발표 또는 경제지표가 있는 종목 경고 1문장]
* **[기회 포착]:** [현재 모멘텀 상 단기 주목할 포인트 1문장]
"""


def generate_daily_report(
    holdings: dict,
    log: Callable[[str], None] | None = None,
) -> tuple[str, dict]:
    """
    포트폴리오 데일리 브리프 생성.
    Returns: (markdown_report, price_data_dict)
    """
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
    gemini_key    = os.getenv("GEMINI_API_KEY", "")
    _log = log or (lambda m: print(f"  {m}"))

    _log("1/3 가격 데이터 수집 중...")
    price_data = _fetch_price_data(holdings)
    if not price_data:
        raise RuntimeError("가격 데이터를 가져오지 못했습니다.")

    _log("2/3  뉴스 헤드라인 수집 중...")
    news = _collect_news(price_data)

    _log("3/3  AI 브리프 생성 중 (약 30~60초)...")
    if anthropic_key:
        report = _generate_with_claude(holdings, price_data, news, anthropic_key, _log)
    elif gemini_key:
        report = _generate_with_gemini(holdings, price_data, news, gemini_key, _log)
    else:
        raise RuntimeError("ANTHROPIC_API_KEY 또는 GEMINI_API_KEY가 없습니다.")

    return report, price_data


def _generate_with_claude(holdings, price_data, news, api_key, log) -> str:
    from anthropic import Anthropic
    client = Anthropic(api_key=api_key)

    big_movers = [t for t in price_data if not t.startswith("__") and abs(price_data[t]["chg_pct"]) >= 3.0]
    web_ctx = ""
    if big_movers:
        log(f"웹서치: {', '.join(big_movers)} 최신 뉴스 수집 중...")
        try:
            sr = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=3000,
                tools=[{"type": "web_search_20250305", "name": "web_search"}],
                messages=[{"role": "user", "content":
                    f"다음 주식들의 {price_data.get('__date', '전일')} 주가 급등락 원인: "
                    f"{', '.join(big_movers)}. 각 종목 핵심 뉴스 헤드라인과 원인 2~3줄 요약."}],
            )
            web_ctx = "\n".join(b.text for b in sr.content if hasattr(b, "text"))
        except Exception as e:
            log(f"웹서치 오류 (계속 진행): {e}")

    base_prompt = _build_prompt(holdings, price_data, news)
    full_prompt = base_prompt + (f"\n\n=== 웹서치 추가 컨텍스트 ===\n{web_ctx}" if web_ctx else "")

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


def _generate_with_gemini(holdings, price_data, news, api_key, log) -> str:
    import requests
    log("Gemini Flash로 브리프 생성 중...")
    prompt = _build_prompt(holdings, price_data, news)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 3000, "temperature": 0.3},
    }
    resp = requests.post(url, json=body, timeout=120)
    resp.raise_for_status()
    return resp.json()["candidates"][0]["content"]["parts"][0]["text"]

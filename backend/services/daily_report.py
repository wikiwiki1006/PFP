"""
backend/services/daily_report.py
──────────────────────────────────
데일리 브리프 생성 서비스 (pfp/daily_portfolio_report.py 이식)
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, time as _time
from typing import Callable

import yfinance as yf

logger = logging.getLogger(__name__)


def _is_market_open() -> bool:
    """미국 장중 여부 (UTC 기준 13:30~20:00, 평일)."""
    now = datetime.now(timezone.utc)
    if now.weekday() >= 5:
        return False
    return _time(13, 30) <= now.time() < _time(20, 0)


# 시장별 벤치마크. 한국 브리핑에 SPY·VIX 를 붙이면 "내 종목이 S&P 대비
# 언더퍼폼" 같은, 한국 투자자에게 의미가 옅은 서술이 나온다.
_BENCHMARKS = {
    "US": [("SPY", "SPY"), ("VIX", "^VIX"), ("TNX", "^TNX")],
    "KR": [("KOSPI", "^KS11"), ("KOSDAQ", "^KQ11"), ("USDKRW", "USDKRW=X")],
}


def _fetch_price_data(holdings: dict, market: str = "US") -> dict:
    tickers = [t for t in holdings if t != "CASH"]
    if not tickers:
        return {}

    bench = _BENCHMARKS.get(market, _BENCHMARKS["US"])
    fetch_list = list(set(tickers + [sym for _, sym in bench]))
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
        # 0 은 주가가 아니라 깨진 데이터다. 예전에는 변동률만 0.0% 로 눌렀는데,
        # 그러면 day_pnl 이 (today - 0) * qty 가 되어 **평가액 전부를 그날의
        # 이익으로** 보고한다. 값을 지어내는 대신 이 종목을 빼고, 아래 합계도
        # 이 종목 없이 낸다.
        if prev_c <= 0:
            logger.warning("전일 종가가 0 이하라 %s 를 브리프에서 제외한다 (prev=%s)",
                           t, prev_c)
            continue
        chg_pct = (today_c / prev_c - 1) * 100
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

    for meta_key, col in bench:
        if col in close.columns:
            s = close[col].dropna()
            if len(s) >= 2 + shift:
                b_now  = float(s.iloc[-(1 + shift)])
                b_prev = float(s.iloc[-(2 + shift)])
                # 지수도 마찬가지다. 0 이면 나눌 수 없고, 넣지 않으면 프롬프트가
                # "매크로 데이터 없음" 으로 빠져 모델이 없다는 걸 안다.
                if b_prev <= 0:
                    logger.warning("전일 값이 0 이하라 %s 를 브리프에서 제외한다 (prev=%s)",
                                   meta_key, b_prev)
                    continue
                result[f"__{meta_key}"] = {
                    "close":   round(b_now, 2),
                    "prev":    round(b_prev, 2),
                    "chg_pct": round((b_now / b_prev - 1) * 100, 2),
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


def _collect_korean_news(tickers: list[str], names: dict) -> str:
    """한국 종목 뉴스를 국내 경제지에서 모은다.

    yfinance 의 news 는 한국 종목에서 거의 비어 있고, 있어도 영문 통신사
    기사다. 그 재료로는 국내 수급·정책 맥락이 나오지 않는다.
    """
    if not tickers:
        return ""
    from backend.services.news_sources import focus_block
    today = datetime.now().strftime("%Y-%m-%d")
    listed = ", ".join(f"{names.get(t, t)}({t})" for t in tickers[:12])
    prompt = f"""Today: {today}
Korean stocks in this portfolio: {listed}

Collect **in English**, concise bullets, last 48 hours:
[Per-stock news] 1-2 items each for the most-moved names (title, source, date, one line)
[KOSPI/KOSDAQ session] foreign & institutional flows, sector rotation
[Macro] KRW/USD, rates, policy or regulation affecting these names
{focus_block("KR")}"""
    return _perplexity_search(prompt, market="KR")


def _collect_news(price_data: dict) -> dict:
    stock_keys = sorted(
        [k for k in price_data if not k.startswith("__")],
        key=lambda t: abs(price_data[t]["chg_pct"]),
        reverse=True,
    )
    return {t: _fetch_yf_news(t, max_items=5) for t in stock_keys}


def _build_prompt(holdings: dict, price_data: dict, news: dict,
                  market: str = "US") -> str:
    is_kr = market == "KR"
    cur   = "₩" if is_kr else "$"
    # 원화는 소수점이 없다(호가 단위 1원).
    dec   = 0 if is_kr else 2
    stock_keys = sorted(
        [k for k in price_data if not k.startswith("__")],
        key=lambda t: price_data[t]["chg_pct"],
    )
    date_str   = price_data.get("__date", datetime.now().strftime("%Y년 %m월 %d일"))
    total_val  = sum(price_data[t]["pos_val"] for t in stock_keys)
    total_pnl  = sum(price_data[t]["day_pnl"]  for t in stock_keys)
    cash_val   = holdings.get("CASH", {}).get("q", 0)

    snap_lines = [
        f"  {t}: 종가 {cur}{price_data[t]['close']:,.{dec}f}  전일대비 {price_data[t]['chg_pct']:+.2f}%  "
        f"1일 P&L {cur}{price_data[t]['day_pnl']:+,.0f}  섹터 {price_data[t]['sector']}"
        for t in stock_keys
    ]

    if is_kr:
        ks = price_data.get("__KOSPI", {})
        kq = price_data.get("__KOSDAQ", {})
        fx = price_data.get("__USDKRW", {})
        bench_name = "KOSPI"
        spy_line = (f"KOSPI 전일 변동: {ks.get('chg_pct', 0):+.2f}%"
                    if ks else "KOSPI 데이터 없음")
        macro_line = (
            f"KOSDAQ: {kq.get('close','?')} ({kq.get('chg_pct',0):+.2f}%)  "
            f"원/달러: {fx.get('close','?')} ({fx.get('chg_pct',0):+.2f}%)"
        ) if kq or fx else "매크로 데이터 없음"
    else:
        spy_info   = price_data.get("__SPY", {})
        vix_info   = price_data.get("__VIX", {})
        tnx_info   = price_data.get("__TNX", {})
        bench_name = "S&P 500"
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

    tz_label = "한국시간" if is_kr else "미국 동부시간"
    # 이 프롬프트를 받는 호출(generate_daily_report 의 마지막 messages.create)에는
    # web_search 도구가 없다. 검색은 그 앞 단계에서 이미 끝나 '웹서치 추가 컨텍스트'
    # 로 붙어 온다. 여기서 "웹서치로 보완하라"고 시키면 모델은 못 한다고 말하지
    # 않고 학습 지식으로 채우고, 그 결과가 오늘 날짜가 박힌 리포트에 최신 뉴스인
    # 것처럼 실린다. 있는 자료로만 쓰라고 한다.
    style_line = (
        "아래 제공된 데이터로만 작성하라. 검색하거나 기억에 의존하지 마라.\n"
        "아래 형식을 엄격히 따른 데일리 브리프를 한국어로 작성하라.\n"
        "**한국 투자자 관점**으로 쓴다. 코스피·코스닥 수급, 원/달러 환율, 외국인·기관 매매,\n"
        "국내 업황과 정책을 축으로 해석하고, 해외 이슈는 국내 시장에 전이되는 경로로만 다뤄라.\n"
        "근거는 국내 경제지(한국경제·매일경제·연합인포맥스·이데일리·조선비즈 등)를 우선한다.\n"
        "금액은 원화(조/억)로 쓰고 달러로 환산하지 마라."
        if is_kr else
        "아래 제공된 데이터로만 작성하라. 검색하거나 기억에 의존하지 마라.\n"
        "아래 형식을 엄격히 따른 월가 인텔리전스 스타일 데일리 브리프를 한국어로 작성하라."
    )

    return f"""아래는 {date_str} 기준 포트폴리오 데이터와 관련 뉴스입니다.

=== 포트폴리오 스냅샷 ===
{chr(10).join(snap_lines)}
전체 주식 평가액: {cur}{total_val:,.0f}  현금: {cur}{cash_val:,.0f}
전일 총 P&L: {cur}{total_pnl:+,.0f}
{spy_line}
매크로 지표: {macro_line}
절대 변동 3% 이상 종목: {big_movers_str}

=== 관련 뉴스 ===
{news_text if news_text.strip() else "수집된 뉴스 없음. 뉴스에 근거한 서술을 하지 말고, 뉴스를 확보하지 못했다고 밝혀라."}

=== 지시사항 ===
{style_line}
미사여구 없이 핵심만. 섹션 순서·제목·구분선(---)을 정확히 유지할 것.
절대 변동 3% 이상 종목은 반드시 섹션 2에 포함하고, 원인 분석은 뉴스 + 금융공학적 시각으로 작성하라.

=== 출력 형식 ===
# 📊 ALPHA TERMINAL DAILY BRIEF ({date_str} 정산({tz_label}))

## 1. 포트폴리오 전일 요약 (Portfolio Snapshot)
* **최고 상승 종목:** [Ticker] ([+X.XX%])
* **최대 하락 종목:** [Ticker] ([-X.XX%])
* **특이 사항:** 전일 포트폴리오 전체 자산은 벤치마크({bench_name}) 대비 [아웃퍼폼/언더퍼폼] 했습니다. [구체적 수치 포함 1~2문장]

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


PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY", "")


def _perplexity_search(query: str, market: str = "US") -> str:
    """Perplexity sonar로 실시간 웹 검색. API 키 없거나 오류 시 빈 문자열 반환.

    두 경우 모두 "" 를 돌려주므로, 어느 쪽이었는지는 여기서만 알 수 있다.
    호출자가 추측해서 기록하면 진단이 엉뚱한 곳으로 간다.
    """
    if not PERPLEXITY_API_KEY:
        logger.warning("Perplexity 웹서치 건너뜀 — PERPLEXITY_API_KEY 미설정")
        return ""
    try:
        import requests as _req
        from backend.services.news_sources import perplexity_extra
        resp = _req.post(
            "https://api.perplexity.ai/chat/completions",
            headers={"Authorization": f"Bearer {PERPLEXITY_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": "sonar",
                "messages": [{"role": "user", "content": query}],
                "max_tokens": 1500,
                "temperature": 0.0,
                **perplexity_extra(market),
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception:
        logger.warning("Perplexity 웹서치 실패 (market=%s) — 뉴스 없이 진행",
                       market, exc_info=True)
        return ""


def generate_daily_report(
    holdings: dict,
    log: Callable[[str], None] | None = None,
    market: str = "US",
) -> tuple[str, dict]:
    """
    포트폴리오 데일리 브리프 생성.
    Returns: (markdown_report, price_data_dict)
    """
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
    _log = log or (lambda m: print(f"  {m}"))

    _log("1/3 가격 데이터 수집 중...")
    price_data = _fetch_price_data(holdings, market)
    if not price_data:
        raise RuntimeError("가격 데이터를 가져오지 못했습니다.")

    _log("2/3  뉴스 헤드라인 수집 중...")
    news = _collect_news(price_data)
    # 한국 종목은 yfinance 뉴스가 사실상 비어 있어 국내 매체로 따로 채운다.
    kr_context = ""
    if market == "KR":
        _stocks = [k for k in price_data if not k.startswith("__")]
        try:
            from backend.services.markets import name_map_for
            _names = name_map_for(_stocks, "KR")
        except Exception:
            _names = {}
        kr_context = _collect_korean_news(_stocks, _names)

    _log("3/3  AI 브리프 생성 중 (약 30~60초)...")
    if not anthropic_key:
        raise RuntimeError("ANTHROPIC_API_KEY가 설정되지 않았습니다.")

    report = _generate_with_claude(holdings, price_data, news, anthropic_key, _log, market)
    return report, price_data


def _generate_with_claude(holdings, price_data, news, api_key, log,
                          market: str = "US", extra_context: str = "") -> str:
    from anthropic import Anthropic
    client = Anthropic(api_key=api_key)

    big_movers = [t for t in price_data if not t.startswith("__") and abs(price_data[t]["chg_pct"]) >= 3.0]
    web_ctx = ""
    if big_movers:
        log(f"Perplexity 웹서치: {', '.join(big_movers)} 최신 뉴스 수집 중...")
        if market == "KR":
            # 종목 코드(005930.KS)로 물으면 국내 기사가 잘 안 걸린다. 회사명을 쓴다.
            from backend.services.markets import name_map_for
            _nm = name_map_for(big_movers, "KR")
            subjects = ", ".join(_nm.get(t, t) for t in big_movers)
            query = (
                f"{price_data.get('__date', '전일')} 한국 증시에서 다음 종목의 주가 급등락 원인: "
                f"{subjects}. 한국 경제지 기사를 근거로 종목별 핵심 헤드라인과 원인을 "
                f"2~3줄로 요약. 국내 수급·업황·공시 중심으로."
            )
        else:
            query = (
                f"다음 주식들의 {price_data.get('__date', '전일')} 주가 급등락 원인: "
                f"{', '.join(big_movers)}. 각 종목 핵심 뉴스 헤드라인과 원인 2~3줄 요약."
            )
        web_ctx = _perplexity_search(query, market)

        if not web_ctx:
            # 원인을 여기서 단정하지 않는다. _perplexity_search 는 키 미설정과
            # 호출 실패 둘 다 "" 를 돌려준다 — 네트워크 실패를 '미설정'으로
            # 적어 두면 진단하는 사람이 키 설정만 들여다본다. 실제 원인은
            # 그쪽이 로그에 남긴다.
            log("Perplexity 결과 없음 — Claude 웹서치로 대체 중...")
            try:
                sr = client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=3000,
                    tools=[{"type": "web_search_20250305", "name": "web_search"}],
                    messages=[{"role": "user", "content": query}],
                )
                web_ctx = "\n".join(b.text for b in sr.content if hasattr(b, "text"))
            except Exception as e:
                log(f"웹서치 오류 (계속 진행): {e}")

    base_prompt = _build_prompt(holdings, price_data, news, market)
    full_prompt = base_prompt
    if extra_context:
        full_prompt += f"\n\n=== 국내 매체 수집 ===\n{extra_context}"
    if web_ctx:
        full_prompt += f"\n\n=== 웹서치 추가 컨텍스트 ===\n{web_ctx}"

    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        system=(
            ("당신은 한국 증권사 리서치센터의 수석 포트폴리오 애널리스트입니다. "
             "코스피·코스닥 수급, 원/달러 환율, 외국인·기관 매매, 국내 업황과 정책을 "
             "축으로 해석합니다. 해외 이슈는 국내 시장에 전이되는 경로로만 다룹니다. "
             "금액은 원화(조/억)로 씁니다. "
             if market == "KR" else
             "당신은 월가 헤지펀드의 수석 포트폴리오 애널리스트입니다. ")
            + "지정된 마크다운 형식을 정확히 따르고, 미사여구 없이 핵심 수치와 원인만 기술하세요. "
              "최대 2페이지 분량(약 800~1200 토큰)으로 간결하게 작성하세요."
        ),
        messages=[{"role": "user", "content": full_prompt}],
    )
    return resp.content[0].text

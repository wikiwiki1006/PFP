"""
backend/services/daily_report.py
──────────────────────────────────
데일리 브리프 생성 서비스 (pfp/daily_portfolio_report.py 이식)
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timezone
from typing import Callable

import yfinance as yf

logger = logging.getLogger(__name__)


# 장중 판정이 필요해지면 `market_calendar.is_us_market_open()` 을 쓴다.
# 이 파일에도 `_is_market_open()` 이 있었는데, 주말만 보고 공휴일을 개장으로
# 오판하는 구현이었다 — market_calendar 가 대체한 사본 셋 중 하나다.
# 호출자가 없어 아무 증상이 없었고, 그래서 옮겨지지 않은 채 남아 있었다.


# 시장별 벤치마크. 한국 브리핑에 SPY·VIX 를 붙이면 "내 종목이 S&P 대비
# 언더퍼폼" 같은, 한국 투자자에게 의미가 옅은 서술이 나온다.
_BENCHMARKS = {
    "US": [("SPY", "SPY"), ("VIX", "^VIX"), ("TNX", "^TNX")],
    "KR": [("KOSPI", "^KS11"), ("KOSDAQ", "^KQ11"), ("USDKRW", "USDKRW=X")],
}


def _vol_outside_benchmarks(market: str) -> str | None:
    """그 시장 변동성 지수가 `_BENCHMARKS` 로 받아지지 않으면 그 이름, 받아지면 None.

    미국 VIX 는 `^VIX` 로 위 표에 있어 다른 지표와 같은 시세 프레임 · 같은 기준 세션에서
    나온다. 한국 VKOSPI 는 야후에 없어서(`markets.KR` 주석 참고) 표에 넣을 수 없고,
    `market_data.volatility_index` 로 따로 받는다 → `price_data["__VOL"]`.

    이름은 시장 정의에서 읽는다. 여기에 'VKOSPI' 를 적지 않는다.
    """
    from backend.services.markets import get_market
    vi = get_market(market).volatility_index or {}
    bench_syms = {sym for _, sym in _BENCHMARKS.get(market, _BENCHMARKS["US"])}
    if vi.get("symbol") and vi["symbol"] not in bench_syms:
        return vi.get("label")
    return None


def _volatility_entry(market: str) -> dict | None:
    """`price_data["__VOL"]` 한 칸. 값을 못 받았으면 None — 프롬프트가 '데이터 없음' 으로 적는다.

    **기준일을 같이 담는다.** 이 값은 시세 프레임이 아니라 다른 출처의 마지막 행이라,
    브리프가 다루는 세션(`_brief_session`)과 날짜가 같다는 보장이 없다. 날짜를 안
    적으면 모델은 스냅샷과 같은 날로 읽는다.
    """
    try:
        from backend.services.market_data import volatility_index
        vi = volatility_index(market)
    except Exception:
        # `volatility_index` 는 자기 실패를 value=None 으로 돌려준다. 여기로 오는
        # 것은 그 바깥(임포트·시장 정의)이 깨진 경우다.
        logger.warning("변동성 지수 조회가 예외로 끝났다 (market=%s) — 브리프에 없다고 적는다",
                       market, exc_info=True)
        return None
    if vi.get("value") is None:
        return None          # 원인은 volatility_index 가 경고로 남겼다
    return {
        "close":   round(float(vi["value"]), 2),
        "prev":    vi.get("prev_close"),
        "chg_pct": vi.get("change_pct"),
        "as_of":   str(vi["as_of"])[:10] if vi.get("as_of") else None,
    }


def _brief_session(market: str, now: datetime | None) -> date:
    """이 브리프가 다루는 세션 — 그 시장에서 **종가가 확정된 마지막 거래일**.

    판정은 market_calendar 에 묻는다. 캘린더가 실패하면(`_holidays_for_year` 는
    예외를 올린다) 그대로 올린다 — 세션을 모르는 채로 만든 브리프는 숫자가 틀려도
    티가 안 난다. 호출부(`routers/reports.py`)가 500 과 사유로 돌려준다.
    """
    from backend.services.market_calendar import (
        ET, KST, last_completed_kr_session, last_completed_session,
    )
    if market == "KR":
        return last_completed_kr_session(now.astimezone(KST) if now is not None else None)
    return last_completed_session(now.astimezone(ET) if now is not None else None)


def _fetch_price_data(holdings: dict, market: str = "US",
                      now: datetime | None = None) -> dict:
    """브리프가 다룰 세션의 보유 종목·벤치마크 등락.

    **판정은 전부 이미 있는 것에 묻는다** — 여기서 새 규칙을 만들지 않는다.
      · 어느 세션인가  `_brief_session` → market_calendar.last_completed_(kr_)session
      · 시세           market_data.get_close_df(fill=False) — 실제 관측치만 남긴 희소 프레임
                       (`/api/macro/daily-brief` 와 같은 입구)
      · 등락           price_series.daily_change — 티커별 마지막 두 실제 관측치
      · 뒤처짐         price_series._is_behind_own_market — 자기 시장 확정 세션보다 앞선 관측

    예전에는 yf.download 프레임을 **합집합 날짜로 ffill** 해 '마지막 행' 을 세션으로
    삼고, "장중이면 전날" 을 UTC 날짜로 골랐다. 합성 프레임·시각 고정으로 재현한 것:
      · 추석 뒤 월요일 08:00 KST — 원/달러만 값이 있는 휴장일 09-25 가 세션이 되고
        보유 종목·KOSPI 가 전부 +0.00% (ffill 이 전일 값을 복제)
      · 추수감사절 다음 날 08:00 ET — 24시간 자산이나 유령행 하나로 11-26 이 세션,
        AAPL·SPY·VIX·TNX 전부 +0.00%
      · KR 09-18 08:00 KST 에 09-16, US 09-17 17:00 ET 에 09-16 (한 세션 늦음)
      · 기준일 행이 없는 종목 하나는 전일 값이 복제돼 +0.00%

    반환: 티커별 등락 · `__KOSPI` 같은 벤치마크 · `__date`(기준 세션) · `__missing`
    ({티커: 사유}) · 한국이면 `__VOL`. 등락을 못 구한 보유 종목은 조용히 빼지 않고
    `__missing` 에 남긴다 — 프롬프트가 '데이터 없음' 으로 적고 합계를 막는다.

    `now` 는 시간대가 붙은 시각이다 (없으면 지금). 측정·테스트 이음매.
    """
    import pandas as pd
    from backend.services.market_data import get_close_df
    from backend.services.price_series import _is_behind_own_market, daily_change

    tickers = [t for t in holdings if t != "CASH"]
    if not tickers:
        return {}

    ref = _brief_session(market, now)
    bench = _BENCHMARKS.get(market, _BENCHMARKS["US"])
    raw = get_close_df(tickers + [sym for _, sym in bench], period="1mo", ttl=60,
                       include_market=False, fill=False)
    if raw is None or raw.empty:
        return {}
    # 기준 세션 **뒤의** 행은 버린다. 야후는 장중에도 당일 봉의 Close 를 채우므로
    # (§1.5) DB 가 아닌 경로로 온 프레임에는 미확정 봉이 있을 수 있다. 브리프는
    # 확정 종가 기준이다.
    idx = raw.index.tz_localize(None) if raw.index.tz is not None else raw.index
    raw = raw[idx.normalize() <= pd.Timestamp(ref)]

    def _change(t: str):
        """(DailyChange, None) 또는 (None, 못 구한 사유)."""
        dc = daily_change(raw, t, now=now)
        if dc is None:
            # daily_change 는 관측치 부족과 전일 종가 0 이하를 둘 다 None 으로 준다.
            return None, f"{ref} 까지 쓸 수 있는 실제 종가 두 개가 없음"
        # 마지막 관측이 기준 세션보다 앞이면 그건 **다른 날의 등락**이다. 기준일
        # 등락으로 적으면 이번에 고친 결함과 같은 형태가 된다. 24시간 자산은 세션이
        # 없어 판정하지 않는다(그 함수의 규칙). 캘린더 실패는 위 `ref` 에서 이미 올라왔다.
        if _is_behind_own_market(t, dc.as_of, now):
            return None, f"{ref} 종가 없음 (마지막 {dc.as_of.date()})"
        return dc, None

    result: dict = {}
    missing: dict[str, str] = {}
    for t in tickers:
        dc, why = _change(t)
        if dc is None:
            missing[t] = why
            logger.warning("브리프(%s): %s 등락 없음 — %s", market, t, why)
            continue
        qty      = holdings[t].get("q", 0)
        avg_cost = holdings[t].get("avg", 0)
        result[t] = {
            "close":     dc.price,
            "prev":      dc.prev_close,
            "chg_pct":   round(dc.chg_pct, 2),
            "qty":       qty,
            "avg_cost":  avg_cost,
            "pos_val":   round(dc.price * qty, 2),
            "day_pnl":   round(dc.chg_val * qty, 2),
            "total_pnl": round((dc.price - avg_cost) * qty, 2),
            # 기본값을 두지 않는다. `holdings` 는 DB 열을 그대로 주므로 NULL 이면 키는
            # 있고 값이 None 이다 — 예전 기본값 "N/A" 는 쓰이지도 않고 "섹터 None" 이 나갔다.
            "sector":    holdings[t].get("sector") or None,
        }

    for meta_key, sym in bench:
        dc, why = _change(sym)
        if dc is None:
            # 넣지 않으면 프롬프트가 "{이름}: 데이터 없음" 으로 적는다 (`_bench_text`).
            logger.warning("브리프(%s): 벤치마크 %s(%s) 등락 없음 — %s", market, meta_key, sym, why)
            continue
        result[f"__{meta_key}"] = {
            "close":   round(dc.price, 2),
            "prev":    round(dc.prev_close, 2),
            "chg_pct": round(dc.chg_pct, 2),
        }

    # 표로 못 받는 변동성 지수(한국 VKOSPI). 미국은 ^VIX 가 표에 있어 부르지 않는다.
    if _vol_outside_benchmarks(market):
        vol = _volatility_entry(market)
        if vol is not None:
            result["__VOL"] = vol

    result["__missing"] = missing
    result["__date"] = ref.strftime("%Y년 %m월 %d일 (%a)")
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
        # 빈 목록은 "이 종목 뉴스 없음" 과 같은 값이라, 조회가 죽어도 프롬프트에는
        # "수집된 뉴스 없음" 으로만 나간다. 어느 쪽이었는지는 여기에만 남는다.
        logger.warning("yfinance 뉴스 조회 실패 (%s) — 이 종목 헤드라인 없이 진행",
                       ticker, exc_info=True)
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


def _news_section(news_text: str, kr_press: str | None, web_ctx: str) -> str:
    """'=== 관련 뉴스 ===' 섹션 본문.

    뉴스는 세 곳에서 온다 — 종목별 yfinance 헤드라인, 한국이면 국내 매체 수집
    (`_collect_korean_news`), 급등락 종목이 있으면 그 원인 수집. **한 섹션에 모으고
    '뉴스 없음' 은 셋 다 비었을 때만 적는다.**

    예전에는 뒤의 둘을 `_generate_with_claude` 가 출력 형식 **뒤에** 이어 붙였고,
    이 섹션은 첫째만 보고 "수집된 뉴스 없음 … 확보하지 못했다고 밝혀라" 를 적었다.
    한국 종목은 yfinance 뉴스가 거의 비어 있어서, 급등락 원인을 받아 온 한국
    브리프도 같은 프롬프트 안에서 "뉴스 없음" 지시를 함께 받았다 (미국도 헤드라인이
    빈 날은 같다). 어느 쪽을 따르라는 말은 프롬프트 어디에도 없었다.

    `kr_press` 는 None 과 "" 가 다르다. None 은 수집하지 않은 경로(미국)이고,
    "" 는 수집했는데 받은 것이 없다 — 한국 지시문이 "근거는 국내 경제지를 우선한다"
    고 말하므로, 그 재료가 없다는 것을 적어야 모델이 있는 척 인용하지 않는다.
    """
    from backend.services.perplexity import window_notice

    blocks = []
    if news_text.strip():
        blocks.append(news_text.strip("\n"))
    press_missing_at = None
    if kr_press is not None:
        if kr_press.strip():
            # 요청 범위는 `_collect_korean_news` 의 "last 48 hours" 다.
            blocks.append(f"[국내 매체 수집]\n{window_notice('48시간')}\n{kr_press.strip()}")
        else:
            press_missing_at = len(blocks)
    if web_ctx and web_ctx.strip():
        # 급등락 원인 질의는 '전일' 을 요청한다.
        blocks.append(f"[급등락 종목 원인 수집]\n{window_notice('전 거래일')}\n{web_ctx.strip()}")

    if not blocks:
        return "수집된 뉴스 없음. 뉴스에 근거한 서술을 하지 말고, 뉴스를 확보하지 못했다고 밝혀라."
    if press_missing_at is not None:
        blocks.insert(press_missing_at,
                      "[국내 매체 수집] 받은 것 없음 — 국내 경제지·증권사 보도를 근거로 인용하지 마라.")
    return "\n\n".join(blocks)


def _bench_text(label: str, info: dict | None, unit: str = "") -> str:
    """매크로 지표 한 칸. **없으면 없다고, 있으면 있는 대로 적는다.**

    예전에는 `info.get('close', '?')` · `info.get('chg_pct', 0)` 이었다. 두 지표 중
    하나만 빠지면 `원/달러: ? (+0.00%)` 가 나갔다 — `?` 는 모델이 값처럼 인용하고
    (B2), `+0.00%` 는 '보합' 이라는 관측이다 (§1.3a).
    """
    if not info or info.get("close") is None:
        return f"{label}: 데이터 없음"
    chg = info.get("chg_pct")
    move = f"{chg:+.2f}%" if chg is not None else "전일 대비 불명"
    # 다른 출처에서 온 값(`__VOL`)만 기준일을 들고 온다 (`_volatility_entry`).
    when = f", {info['as_of']} 기준" if info.get("as_of") else ""
    return f"{label}: {info['close']}{unit} ({move}{when})"


def _macro_line(parts: list[tuple[str, dict | None, str]]) -> str:
    """매크로 지표 줄. 받은 지표가 **하나도** 없을 때만 '매크로 데이터 없음'.

    예전 미국 줄은 VIX 가 있는지만 보고 줄 전체를 정해서, VIX 가 빠지면 받아 온
    10년물 금리까지 '매크로 데이터 없음' 으로 사라졌다.
    """
    if not any(info and info.get("close") is not None for _, info, _ in parts):
        return "매크로 데이터 없음"
    return "  ".join(_bench_text(label, info, unit) for label, info, unit in parts)


def _build_prompt(holdings: dict, price_data: dict, news: dict,
                  market: str = "US", *, kr_press: str | None = None,
                  web_ctx: str = "") -> str:
    """브리프 프롬프트. 바깥을 보지 않는다 — 수집은 호출자가 끝내고 넘긴다.

    `kr_press` · `web_ctx` 는 `_news_section` 을 본다.
    """
    # 통화 포맷을 여기서 다시 만들지 않는다. `cur + 포맷 지정자` 로 조립하면
    # 원화 소수 자릿수 같은 규칙이 이 파일에만 빠지는 사본이 하나 더 생긴다 —
    # `_fmt_amount` 가 프론트와 갈렸던 것이 정확히 그렇게 시작했다 (§1.4).
    from backend.services.report_writer import _fmt_price

    is_kr    = market == "KR"
    cur_code = "KRW" if is_kr else "USD"

    def _money(v) -> str:
        return _fmt_price(v, cur_code)

    def _signed(v) -> str:
        """손익은 부호가 보여야 한다. 음수 부호는 `_fmt_price` 가 붙인다."""
        return f"+{_money(v)}" if v > 0 else _money(v)

    stock_keys = sorted(
        [k for k in price_data if not k.startswith("__")],
        key=lambda t: price_data[t]["chg_pct"],
    )
    # 등락을 못 구한 보유 종목 (`_fetch_price_data` 의 `__missing`). 스냅샷에서 빼면
    # 그 종목이 없었던 것처럼 되고 합계도 조용히 줄어든다 — 모델은 알 방법이 없다
    # (§1.3·B3). `ai_analysis.generate_daily_brief` 가 이미 같은 규칙으로 적는다.
    missing: dict = price_data.get("__missing") or {}
    date_str   = price_data.get("__date", datetime.now().strftime("%Y년 %m월 %d일"))
    total_val  = sum(price_data[t]["pos_val"] for t in stock_keys)
    total_pnl  = sum(price_data[t]["day_pnl"]  for t in stock_keys)
    cash_val   = holdings.get("CASH", {}).get("q", 0)

    snap_lines = [
        f"  {t}: 종가 {_money(price_data[t]['close'])}  전일대비 {price_data[t]['chg_pct']:+.2f}%  "
        f"1일 P&L {_signed(price_data[t]['day_pnl'])}  "
        # 섹터가 없으면 없다고 적는다. 값 자리에 'N/A'·'None' 을 넣으면 모델이 그걸
        # 섹터 이름처럼 옮긴다 (B2).
        f"섹터 {price_data[t].get('sector') or '정보 없음'}"
        for t in stock_keys
    ] + [f"  {t}: 데이터 없음 — {why}" for t, why in missing.items()]

    if missing:
        names = ", ".join(missing)
        total_lines = (f"전체 주식 평가액: {names} 의 평가액이 없어 합산 불가  현금: {_money(cash_val)}\n"
                       f"전일 총 P&L: {names} 데이터 없음 — 합산 불가")
    else:
        total_lines = (f"전체 주식 평가액: {_money(total_val)}  현금: {_money(cash_val)}\n"
                       f"전일 총 P&L: {_signed(total_pnl)}")

    if is_kr:
        bench_key, bench_name = "KOSPI", "KOSPI"
        macro_parts = [
            ("KOSDAQ",  price_data.get("__KOSDAQ"), ""),
            ("원/달러", price_data.get("__USDKRW"), ""),
        ]
    else:
        bench_key, bench_name = "SPY", "S&P 500"
        macro_parts = [
            ("VIX",     price_data.get("__VIX"), ""),
            ("10Y TNX", price_data.get("__TNX"), "%"),
        ]
    # 그 시장의 변동성 지수가 위 목록에 없으면(한국 VKOSPI) 여기서 더한다. 미국
    # 브리프에는 VIX 가 있는데 한국 브리프에는 변동성 지표가 하나도 없었다.
    # 못 받았으면 "VKOSPI: 데이터 없음" 으로 나간다 — 빼지 않는다.
    vol_label = _vol_outside_benchmarks(market)
    if vol_label:
        macro_parts.append((vol_label, price_data.get("__VOL"), ""))
    macro_line = _macro_line(macro_parts)
    # 벤치마크 등락도 같은 규칙이다. `.get('chg_pct', 0)` 이면 값이 없을 때 '보합' 이 된다.
    bench_chg = (price_data.get(f"__{bench_key}") or {}).get("chg_pct")
    spy_line = (f"{bench_key} 전일 변동: {bench_chg:+.2f}%" if bench_chg is not None
                else f"{bench_key} 데이터 없음")
    # 그리고 없으면 비교를 시키지 않는다. 스냅샷은 "KOSPI 데이터 없음" 이라고 적고
    # 출력 형식은 "[아웃퍼폼/언더퍼폼]" 을 채우라고 하면, 채울 곳은 기억뿐이다.
    # 보유 종목 일부의 등락이 없을 때도 같다 — 포트폴리오 쪽 합계가 '합산 불가' 다.
    if bench_chg is None:
        no_cmp = f"벤치마크({bench_name}) 등락 데이터가 없어"
    elif missing:
        no_cmp = f"보유 종목 일부({', '.join(missing)})의 등락이 없어"
    else:
        no_cmp = None
    bench_cmp = (
        f"전일 포트폴리오 전체 자산은 벤치마크({bench_name}) 대비 [아웃퍼폼/언더퍼폼] 했습니다. "
        "[구체적 수치 포함 1~2문장]"
        if no_cmp is None else
        f"{no_cmp} 대비 성과는 판단하지 않았습니다. [포트폴리오 자체 수치로 1~2문장]"
    )

    news_text = ""
    for t in stock_keys:
        items = news.get(t, [])
        if items:
            news_text += f"\n[{t} 관련 뉴스]\n"
            for n in items:
                news_text += f"  - \"{n['title']}\" ({n['publisher']}, {n['time']})\n"

    big_movers = [t for t in stock_keys if abs(price_data[t]["chg_pct"]) >= 3.0]
    # "전 종목 3% 미만" 은 등락을 모르는 종목까지 단정한다. 모르는 종목이 있으면 범위를 적는다.
    big_movers_str = (", ".join(big_movers) if big_movers
                      else "없음 (전 종목 3% 미만 변동)" if not missing
                      else "없음 (등락을 구한 종목 기준 — 데이터 없는 종목은 제외)")

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

    # 섹션 3 의 '매크로 변수' 는 **위 스냅샷의 매크로 지표**만 가리킨다. 예전에는
    # "금리/환율/VIX" 를 두 시장에 똑같이 물었는데 한국 스냅샷에는 VIX 가 없다
    # (`_BENCHMARKS["KR"]` 은 KOSPI·KOSDAQ·원/달러). 값 없이 물으면 모델이 채울
    # 곳은 기억뿐이고, 그렇게 채워지는 것도 한국 변동성이 아니라 미국 VIX 다.
    # 목록을 시장별로 다시 적으면 `_BENCHMARKS` 와 어긋나는 사본이 하나 더 생긴다.

    return f"""아래는 {date_str} 기준 포트폴리오 데이터와 관련 뉴스입니다.

=== 포트폴리오 스냅샷 ===
{chr(10).join(snap_lines)}
{total_lines}
{spy_line}
매크로 지표: {macro_line}
절대 변동 3% 이상 종목: {big_movers_str}

=== 관련 뉴스 ===
{_news_section(news_text, kr_press, web_ctx)}

=== 지시사항 ===
{style_line}
미사여구 없이 핵심만. 섹션 순서·제목·구분선(---)을 정확히 유지할 것.
절대 변동 3% 이상 종목은 반드시 섹션 2에 포함하고, 원인 분석은 뉴스 + 금융공학적 시각으로 작성하라.

=== 출력 형식 ===
# 📊 ALPHA TERMINAL DAILY BRIEF ({date_str} 정산({tz_label}))

## 1. 포트폴리오 전일 요약 (Portfolio Snapshot)
* **최고 상승 종목:** [Ticker] ([+X.XX%])
* **최대 하락 종목:** [Ticker] ([-X.XX%])
* **특이 사항:** {bench_cmp}

---

## 2. 주요 종목별 등락 원인 분석 (Why It Moved)
[변동성 종목 각각에 대해 아래 블록 반복. 상승은 🚀, 하락은 📉 이모지 사용.]

### 🚀 [Ticker] ([+X.XX%]) — [핵심 이유 한 줄 요약]
* **핵심 원인:** [구체적 원인 2문장]
* **주요 관련 뉴스:**
  - "[헤드라인]" (출처 / 시각)

---

## 3. 오늘 장 시작 전 매크로 및 섹터 헤드업 (Today's Watch Items)
* **[매크로 변수]:** [위 '매크로 지표' 가 보유 종목에 미칠 영향 1문장. 거기 없는 지표는 끌어오지 말 것]
* **[모니터링 리스크]:** [오늘 실적 발표 또는 경제지표가 있는 종목 경고 1문장]
* **[기회 포착]:** [현재 모멘텀 상 단기 주목할 포인트 1문장]
"""


def _perplexity_search(query: str, market: str = "US") -> str:
    """Perplexity sonar로 실시간 웹 검색. API 키 없거나 오류 시 빈 문자열 반환.

    두 경우 모두 "" 를 돌려주므로, 어느 쪽이었는지는 로그에만 남는다.
    호출자가 추측해서 기록하면 진단이 엉뚱한 곳으로 간다.
    """
    from backend.services import perplexity
    return perplexity.search(query, market=market, max_tokens=1500,
                             label="daily-report")


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
    # 키는 **수집 전에** 본다. 예전에는 3단계에서 봐서, 키가 없어도 시세·뉴스를
    # 다 모으고 한국이면 Perplexity 비용까지 쓴 뒤에 실패했다.
    if not anthropic_key:
        raise RuntimeError("ANTHROPIC_API_KEY가 설정되지 않았습니다.")

    _log("1/3 가격 데이터 수집 중...")
    price_data = _fetch_price_data(holdings, market)
    if not price_data:
        raise RuntimeError("가격 데이터를 가져오지 못했습니다.")
    # 보유 종목 등락을 **하나도** 못 구했으면 브리프를 만들지 않는다. 전 종목이
    # '데이터 없음' 인 브리프는 LLM 비용만 쓰고, 리포트 목록에 쓸모없는 한 건으로
    # 남는다. 사유를 그대로 돌려준다 — 장 마감 직후라 기준일 종가가 아직 DB 에
    # 없는 경우가 대표적이다 (get_close_df 가 뒤처진 티커를 백그라운드로 갱신하므로
    # 수집이 들어온 뒤에는 만들어진다).
    if not any(not k.startswith("__") for k in price_data):
        missing = price_data.get("__missing") or {}
        raise RuntimeError(
            f"{price_data.get('__date', '기준일')} 기준 보유 종목 등락을 하나도 구하지 못했습니다 — "
            + "; ".join(f"{t}: {why}" for t, why in missing.items())
        )

    _log("2/3  뉴스 헤드라인 수집 중...")
    news = _collect_news(price_data)
    # 한국 종목은 yfinance 뉴스가 사실상 비어 있어 국내 매체로 따로 채운다.
    #
    # **받은 것을 프롬프트까지 넘긴다.** 이 수집은 345f240(2026-09-11) 에서
    # `_generate_with_claude(extra_context=)` 와 함께 들어왔는데 호출부가 그 인자를
    # 넘기지 않아, 그날부터 한국 브리프마다 국내 매체 수집(Perplexity)을 부르고
    # 결과는 버렸다. 뉴스 섹션에는 "수집된 뉴스 없음" 이 나갔다.
    kr_press: str | None = None          # None = 수집하지 않는 시장
    if market == "KR":
        _stocks = [k for k in price_data if not k.startswith("__")]
        try:
            from backend.services.markets import name_map_for
            _names = name_map_for(_stocks, "KR")
        except Exception:
            # 이름 없이도 코드로 검색은 된다. 다만 국내 기사가 덜 걸리므로 남긴다.
            logger.warning("국내 매체 수집용 종목명 조회 실패 — 코드로 진행", exc_info=True)
            _names = {}
        kr_press = _collect_korean_news(_stocks, _names)
        if not kr_press:
            # 원인(키 미설정·호출 실패)은 perplexity.search 가 남겼다. 여기서는
            # 이 브리프가 국내 매체 없이 나간다는 결과를 남긴다.
            logger.warning("한국 브리프: 국내 매체 수집 결과 없음 — 프롬프트에 없다고 적는다")

    _log("3/3  AI 브리프 생성 중 (약 30~60초)...")
    report = _generate_with_claude(holdings, price_data, news, anthropic_key, _log, market,
                                   kr_press=kr_press)
    return report, price_data


def _generate_with_claude(holdings, price_data, news, api_key, log,
                          market: str = "US", kr_press: str | None = None) -> str:
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

    # 수집한 뉴스는 전부 `_build_prompt` 의 뉴스 섹션으로 들어간다. 여기서 프롬프트
    # 끝에 이어 붙이지 않는다 — 그러면 뉴스 섹션이 "뉴스 없음" 이라고 말하는
    # 프롬프트 뒤에 뉴스가 붙는다 (`_news_section` 참고).
    full_prompt = _build_prompt(holdings, price_data, news, market,
                                kr_press=kr_press, web_ctx=web_ctx)

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

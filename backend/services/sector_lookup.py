"""
services/sector_lookup.py
─────────────────────────
티커 → GICS 섹터. 단건 조회와 **공개 출처 기반 일괄 조회**를 함께 제공한다.

여기서 중요한 것은 이 조회가 **공개 정보만 쓴다**는 점이다. 예전에는 섹터가
필요한 자리에서 `holdings`(사용자 보유)의 `sector` 컬럼을 읽어 썼는데, 그러면
같은 티커 목록을 넣어도 **사용자마다 다른 결과**가 나온다. 그것이 자본시장법이
말하는 '개별성' 이고, 개별성이 붙는 순간 그 기능은 유사투자자문이 아니라
투자자문이 된다. 섹터는 종목의 공개 속성이므로 개인 데이터에서 읽을 이유가
없다 — 이 모듈이 그 자리를 대신한다.

캐시는 `common_cache` 의 `ticker_sectors:{market}` 한 곳이다 (사용자별이 아니다).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# GICS 표준 명칭으로 접는다. yfinance 는 같은 섹터를 여러 이름으로 준다
# ("Financial Services" / "Financials"), 그대로 두면 섹터별 묶음이 갈라진다.
_GICS = {
    "Technology":                "Technology",
    "Information Technology":    "Technology",
    "Healthcare":                "Healthcare",
    "Health Care":               "Healthcare",
    "Financials":                "Financials",
    "Financial Services":        "Financials",
    "Financial":                 "Financials",
    "Consumer Cyclical":         "Consumer Discretionary",
    "Consumer Discretionary":    "Consumer Discretionary",
    "Consumer Defensive":        "Consumer Staples",
    "Consumer Staples":          "Consumer Staples",
    "Energy":                    "Energy",
    "Industrials":               "Industrials",
    "Basic Materials":           "Materials",
    "Materials":                 "Materials",
    "Real Estate":               "Real Estate",
    "Utilities":                 "Utilities",
    "Communication Services":    "Communication Services",
    "Telecommunication Services": "Communication Services",
}

_CACHE_KEY = "ticker_sectors:{market}"
_CACHE_TTL = 30 * 86400        # 섹터는 거의 바뀌지 않는다

# 한 요청에서 새로 조회할 티커 수 상한. yfinance `.info` 는 종목당 0.5~1초라
# 500종목을 한 번에 채우면 그대로 타임아웃이다. 조금씩 채우고, 아직 모르는
# 것은 **모른다고** 돌려준다 (0 이나 'Other' 로 위장하지 않는다 — §1.3).
_MAX_FETCH_PER_CALL = 40


def normalize_sector(raw: str | None) -> str | None:
    """yfinance/위키 표기를 GICS 표준 명칭으로. 비었으면 None(=모름)."""
    s = (raw or "").strip()
    if not s:
        return None
    return _GICS.get(s, s)


def _fetch_sector(ticker: str) -> str:
    """yfinance로 종목 섹터 조회. 실패 시 'Other' 반환.

    옛 호출부를 위해 남겨 둔 형태다. 새 코드는 `sector_map()` 을 쓴다 —
    'Other' 는 '모름' 과 '기타 섹터' 를 같은 값으로 만든다.
    """
    return _fetch_one(ticker) or "Other"


def _fetch_one(ticker: str) -> str | None:
    """yfinance 단건 조회. 못 구하면 None.

    실패를 삼키되 **로그는 남긴다.** 예전에는 `except Exception: return "Other"`
    라 레이트리밋에 걸려 전 종목이 'Other' 가 되어도 화면에는 '기타 섹터'
    라는 정상값으로 보였다 (§1.3(b)).
    """
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info or {}
        return normalize_sector(info.get("sector") or info.get("sectorDisp"))
    except Exception:
        logger.warning("섹터 조회 실패 (%s) — 미분류로 남긴다", ticker, exc_info=True)
        return None


def _seed_us_from_wikipedia() -> dict[str, str]:
    """S&P500 구성표의 GICS Sector 열을 통째로 읽는다. 실패하면 빈 사전.

    `trading_signals.get_sp500_universe()` 가 읽는 것과 **같은 표**다. 거기서는
    Symbol 열만 쓰는데 섹터가 같은 표에 이미 들어 있어, 종목당 yfinance 를
    부르는 대신 한 번에 500종목을 채울 수 있다.
    """
    try:
        import pandas as pd
        import requests
        from io import StringIO

        resp = requests.get(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        resp.raise_for_status()
        table = pd.read_html(StringIO(resp.text))[0]
        if "Symbol" not in table.columns or "GICS Sector" not in table.columns:
            logger.warning("S&P500 표에 Symbol/GICS Sector 열이 없다 — 열: %s",
                           list(table.columns)[:8])
            return {}
        out: dict[str, str] = {}
        for sym, sec in zip(table["Symbol"], table["GICS Sector"]):
            t = str(sym).strip().replace(".", "-")
            s = normalize_sector(str(sec))
            if t and s:
                out[t] = s
        logger.info("S&P500 섹터 %d종목을 위키피디아에서 채웠다", len(out))
        return out
    except Exception:
        logger.warning("S&P500 섹터 일괄 조회 실패 — 종목별 조회로 넘어간다", exc_info=True)
        return {}


def sector_map(
    tickers: list[str],
    market: str = "US",
    max_fetch: int = _MAX_FETCH_PER_CALL,
) -> dict[str, str]:
    """티커 → GICS 섹터. **모르는 티커는 키 자체가 없다.**

    'Other' 같은 채움값을 쓰지 않는 이유는 §1.3(a) 다 — '기타 섹터로 분류된
    종목' 과 '아직 조회하지 못한 종목' 은 다른 사실이고, 화면이 그 둘을 구별해
    말할 수 있어야 한다. 호출부는 키가 없는 티커를 '미분류' 로 묶는다.

    캐시(30일)에 없는 것만 조회하고, 한 번에 `max_fetch` 개까지만 채운다.
    나머지는 다음 호출에서 채워진다 — 스캔은 스케줄러가 20분마다 돌리므로
    몇 번이면 전부 찬다. `max_fetch=0` 이면 캐시에 있는 것만 돌려준다.
    """
    from backend.db.market_cache import get_common, save_common

    key    = _CACHE_KEY.format(market=market)
    cached = get_common(key) or {}
    if not isinstance(cached, dict):
        logger.warning("섹터 캐시가 사전이 아니다 (%s: %s) — 새로 만든다", key, type(cached).__name__)
        cached = {}

    known   = dict(cached)
    missing = [t for t in tickers if t not in known]

    # 미국은 공개 구성표 한 번으로 대부분이 채워진다. 캐시가 비어 있을 때만 시도한다.
    if missing and market == "US" and not known:
        seeded = _seed_us_from_wikipedia()
        if seeded:
            known.update(seeded)
            missing = [t for t in tickers if t not in known]

    fetched = 0
    for t in missing:
        if fetched >= max_fetch:
            break
        s = _fetch_one(t)
        fetched += 1
        if s:
            known[t] = s

    if known != cached:
        save_common(key, known, ttl_seconds=_CACHE_TTL)

    if missing and fetched < len(missing):
        logger.info("섹터 미해결 %d종목 (이번에 %d개 조회, market=%s) — 다음 호출에서 채운다",
                    len(missing) - fetched, fetched, market)

    return {t: known[t] for t in tickers if t in known}

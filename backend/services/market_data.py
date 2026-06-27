"""
services/market_data.py
────────────────────────
yfinance / FRED 데이터 수집. Streamlit 의존 없음.
캐시는 functools.lru_cache + TTL 방식으로 처리.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import yfinance as yf


# ── TTL 캐시 (Streamlit st.cache_data 없이 동일 효과) ──────────────────────────

_cache: dict[str, tuple[float, Any]] = {}


def _cached(key: str, ttl: int, fn):
    now = time.time()
    if key in _cache:
        ts, val = _cache[key]
        if now - ts < ttl:
            return val
    val = fn()
    _cache[key] = (now, val)
    return val


def clear_cache():
    _cache.clear()


# ── 시세 ────────────────────────────────────────────────────────────────────────

ALWAYS_FETCH = [
    "^GSPC", "^IXIC", "^KS11", "^KQ11",
    "XLK", "XLF", "XLE", "XLY", "XLV", "XLI", "XLB",
    "BTC-USD", "GC=F", "^VIX", "^TNX", "^IRX", "CL=F",
    "USDKRW=X", "SPY",
]

GICS_SECTOR_ETFS = [
    ("TECHNOLOGY",          "XLK"),
    ("FINANCIALS",          "XLF"),
    ("COMMUNICATION",       "XLC"),
    ("CONSUMER_DISC",       "XLY"),
    ("HEALTHCARE",          "XLV"),
    ("INDUSTRIALS",         "XLI"),
    ("CONSUMER_STAPLES",    "XLP"),
    ("ENERGY",              "XLE"),
    ("UTILITIES",           "XLU"),
    ("MATERIALS",           "XLB"),
    ("REAL_ESTATE",         "XLRE"),
]

SECTOR_ETF_TICKERS = [etf for _, etf in GICS_SECTOR_ETFS]


def get_close_df(tickers: list[str], period: str = "5y", ttl: int = 300) -> pd.DataFrame:
    all_tickers = list(set(tickers + ALWAYS_FETCH))
    key = f"close_{','.join(sorted(all_tickers))}_{period}"

    def _fetch():
        data = yf.download(all_tickers, period=period, progress=False, auto_adjust=True)
        return data["Close"].ffill()

    return _cached(key, ttl, _fetch)


def get_sector_etf_df(ttl: int = 60) -> pd.DataFrame:
    def _fetch():
        data = yf.download(SECTOR_ETF_TICKERS, period="5d", progress=False, auto_adjust=True)
        return data["Close"].ffill()

    return _cached("sector_etf", ttl, _fetch)


def get_sector_changes() -> dict[str, float]:
    """{ 'XLK': 1.23, 'XLF': -0.45, ... } 형태로 섹터 ETF 등락률 반환."""
    try:
        df = get_sector_etf_df()
        if df.empty or len(df) < 2:
            return {}
        cur, prev = df.iloc[-1], df.iloc[-2]
        result = {}
        for _, etf in GICS_SECTOR_ETFS:
            if etf in df.columns:
                c, p = cur.get(etf), prev.get(etf)
                if pd.notna(c) and pd.notna(p) and p:
                    result[etf] = (float(c) / float(p) - 1) * 100
        return result
    except Exception:
        return {}


def get_sector_table() -> list[dict]:
    """섹터 ETF 상세 테이블: [{ sector, ticker, price, chg_pct }, ...]"""
    try:
        df = get_sector_etf_df()
        if df.empty or len(df) < 2:
            return []
        cur, prev = df.iloc[-1], df.iloc[-2]
        rows = []
        for label, etf in GICS_SECTOR_ETFS:
            if etf not in df.columns:
                continue
            c, p = cur.get(etf), prev.get(etf)
            if pd.isna(c) or pd.isna(p) or p == 0:
                continue
            rows.append({
                "sector": label,
                "ticker": etf,
                "price": round(float(c), 2),
                "chg_pct": round((float(c) / float(p) - 1) * 100, 2),
            })
        return rows
    except Exception:
        return []


# ── FRED 거시경제 ────────────────────────────────────────────────────────────────

def get_fred_macro(ttl: int = 3600) -> dict:
    def _fetch():
        try:
            import pandas_datareader.data as web
            start = datetime.now() - timedelta(days=90)
            df = web.DataReader(["FEDFUNDS", "UNRATE", "DGS10", "DGS2"], "fred", start).ffill().dropna()
            y10 = float(df["DGS10"].iloc[-1])
            y2  = float(df["DGS2"].iloc[-1])
            return {
                "fed_rate":     float(df["FEDFUNDS"].iloc[-1]),
                "unemployment": float(df["UNRATE"].iloc[-1]),
                "y10": y10,
                "y2":  y2,
                "spread_10_2": round(y10 - y2, 3),
                "source": "FRED",
            }
        except Exception:
            return {
                "fed_rate": 5.33, "unemployment": 3.9,
                "y10": 4.2, "y2": 4.5,
                "spread_10_2": round(4.2 - 4.5, 3),
                "source": "기본값(FRED 접속실패)",
            }

    return _cached("fred_macro", ttl, _fetch)


def get_doom_radar(ttl: int = 300) -> dict:
    """장단기 금리차 + 하이일드 스프레드 기반 위기 레이더."""
    def _fetch():
        try:
            import pandas_datareader.data as web
            start = datetime.now() - timedelta(days=400)
            df = web.DataReader(["T10Y2Y", "BAMLH0A0HYM2"], "fred", start).dropna()
            rate_spread = float(df["T10Y2Y"].iloc[-1])
            hy_spread   = float(df["BAMLH0A0HYM2"].iloc[-1])
        except Exception:
            rate_spread, hy_spread = 0.3, 3.5

        is_doom = (rate_spread < 0.0) or (hy_spread > 5.0)
        reasons = []
        if rate_spread < 0.0:
            reasons.append(f"10Y-2Y 역전({rate_spread:+.2f}%p)")
        if hy_spread > 5.0:
            reasons.append(f"HY스프레드 급등({hy_spread:.1f}%)")

        return {
            "is_doom":    is_doom,
            "rate_spread": rate_spread,
            "hy_spread":   hy_spread,
            "reason":      " / ".join(reasons) if reasons else "정상 범위",
        }

    return _cached("doom_radar", ttl, _fetch)


# ── 뉴스 ────────────────────────────────────────────────────────────────────────

def get_portfolio_news(tickers: list[str], max_per: int = 2, max_macro: int = 4, ttl: int = 600) -> list[dict]:
    key = f"news_{','.join(sorted(tickers))}"

    def _fetch():
        items = []

        def _extract(news_list, tag, max_n):
            out = []
            for n in news_list[:max_n]:
                content = n.get("content", n)
                title = content.get("title") or n.get("title")
                ts = content.get("pubDate") or n.get("providerPublishTime")
                if not title:
                    continue
                if isinstance(ts, str):
                    try:
                        pub_dt = pd.to_datetime(ts)
                    except Exception:
                        pub_dt = pd.Timestamp.now()
                elif isinstance(ts, (int, float)):
                    pub_dt = pd.to_datetime(ts, unit="s")
                else:
                    pub_dt = pd.Timestamp.now()
                out.append({"ticker": tag, "title": title.strip(), "time": pub_dt})
            return out

        for t in tickers:
            try:
                news = yf.Ticker(t).news or []
                items.extend(_extract(news, t, max_per))
            except Exception:
                continue

        for src in ["^GSPC", "^IXIC", "^TNX"]:
            try:
                news = yf.Ticker(src).news or []
                items.extend(_extract(news, "MACRO", max_macro))
            except Exception:
                continue

        items.sort(key=lambda x: x["time"], reverse=True)
        return [
            {
                "ticker":   x["ticker"],
                "title":    x["title"],
                "time":     x["time"].isoformat() if hasattr(x["time"], "isoformat") else str(x["time"]),
                "is_macro": x["ticker"] == "MACRO",
            }
            for x in items[:18]
        ]

    return _cached(key, ttl, _fetch)


# ── 실적/배당 ────────────────────────────────────────────────────────────────────

def get_earnings_dividends(tickers: list[str], ttl: int = 3600) -> list[dict]:
    key = f"earn_div_{','.join(sorted(tickers))}"

    def _fetch():
        result = []
        for t in tickers:
            earn_date = div_date = div_yield = "N/A"
            try:
                tk = yf.Ticker(t)
                try:
                    cal = tk.calendar
                    if isinstance(cal, dict) and "Earnings Date" in cal:
                        ed = cal["Earnings Date"]
                        if isinstance(ed, list) and ed:
                            earn_date = ed[0].strftime("%b %d")
                except Exception:
                    pass
                try:
                    divs = tk.dividends
                    if len(divs) > 0:
                        div_date = divs.index[-1].strftime("%b %d")
                    dy = (tk.info or {}).get("dividendYield")
                    if dy:
                        div_yield = f"{dy*100:.2f}%" if dy < 1 else f"{dy:.2f}%"
                except Exception:
                    pass
            except Exception:
                pass
            result.append({"ticker": t, "earn_date": earn_date, "div_date": div_date, "div_yield": div_yield})
        return result

    return _cached(key, ttl, _fetch)


# ── 시장 스냅샷 ─────────────────────────────────────────────────────────────────

def get_market_snapshot(close_df: pd.DataFrame) -> dict:
    """현재 주요 지수/자산 현재가 + 전일대비 등락률."""
    if close_df.empty or len(close_df) < 2:
        return {}

    cur, prev = close_df.iloc[-1], close_df.iloc[-2]

    def _val(ticker):
        if ticker not in close_df.columns:
            return None, None
        c = cur.get(ticker)
        p = prev.get(ticker)
        if pd.isna(c):
            return None, None
        chg = (float(c) / float(p) - 1) * 100 if (p and not pd.isna(p)) else None
        return float(c), chg

    sp500, sp500_chg       = _val("^GSPC")
    nasdaq, nasdaq_chg     = _val("^IXIC")
    kospi, kospi_chg       = _val("^KS11")
    vix, _                 = _val("^VIX")
    btc, _                 = _val("BTC-USD")
    gold, _                = _val("GC=F")
    wti, _                 = _val("CL=F")
    usd_krw, _             = _val("USDKRW=X")

    return {
        "sp500": sp500, "sp500_chg": sp500_chg,
        "nasdaq": nasdaq, "nasdaq_chg": nasdaq_chg,
        "kospi": kospi, "kospi_chg": kospi_chg,
        "vix": vix, "btc": btc, "gold": gold,
        "wti": wti, "usd_krw": usd_krw,
    }

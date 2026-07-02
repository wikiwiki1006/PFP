"""
services/market_data.py
────────────────────────
yfinance / FRED 데이터 수집. Streamlit 의존 없음.
캐시는 TTL 방식으로 처리.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import yfinance as yf

_logger = logging.getLogger(__name__)

# ── TTL 캐시 ──────────────────────────────────────────────────────────────────

_cache: dict[str, tuple[float, Any]] = {}

# 백그라운드 stale 갱신 중복 방지
_bg_in_progress: set[str] = set()
_bg_lock = threading.Lock()


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

SNAPSHOT_TICKERS = ["SPY", "QQQ", "BTC-USD", "^VIX", "NVDA", "AAPL", "MSFT"]

ALWAYS_FETCH = [
    "^GSPC", "^IXIC", "^KS11", "^KQ11",
    "XLK", "XLF", "XLE", "XLY", "XLV", "XLI", "XLB",
    "BTC-USD", "GC=F", "^VIX", "^TNX", "^IRX", "CL=F",
    "USDKRW=X", "SPY", "QQQ", "NVDA", "AAPL", "MSFT",
]

GICS_SECTOR_ETFS = [
    ("TECHNOLOGY",        "XLK"),
    ("FINANCIALS",        "XLF"),
    ("COMMUNICATION",     "XLC"),
    ("CONSUMER_DISC",     "XLY"),
    ("HEALTHCARE",        "XLV"),
    ("INDUSTRIALS",       "XLI"),
    ("CONSUMER_STAPLES",  "XLP"),
    ("ENERGY",            "XLE"),
    ("UTILITIES",         "XLU"),
    ("MATERIALS",         "XLB"),
    ("REAL_ESTATE",       "XLRE"),
]

SECTOR_ETF_TICKERS = [etf for _, etf in GICS_SECTOR_ETFS]


def _bg_refresh_tickers(stale: list[str], period: str) -> None:
    """stale 티커를 백그라운드 스레드에서 yfinance 수집 → DB 저장. 중복 실행 방지."""
    from backend.db.market_cache import _yf_download_batched, save_prices_to_db

    with _bg_lock:
        new_stale = [t for t in stale if t not in _bg_in_progress]
        if not new_stale:
            return
        _bg_in_progress.update(new_stale)

    def _worker():
        try:
            close_df = _yf_download_batched(new_stale, period=period, inter_batch_sleep=1.0)
            if not close_df.empty:
                save_prices_to_db(close_df.ffill().dropna(axis=1, how="all"))
                _logger.debug(f"백그라운드 갱신 완료: {len(new_stale)}개 티커")
        except Exception as e:
            _logger.warning(f"백그라운드 갱신 실패: {e}")
        finally:
            with _bg_lock:
                _bg_in_progress.difference_update(new_stale)

    threading.Thread(target=_worker, daemon=True).start()


def get_close_df(tickers: list[str], period: str = "2y", ttl: int = 300, include_market: bool = True) -> pd.DataFrame:
    """
    1) 메모리 캐시 (TTL 5분) 히트 → 즉시 반환
    2) DB 데이터 즉시 반환 + stale 티커는 백그라운드에서 비동기 갱신
       (사용자 요청이 yfinance를 기다리지 않음)
    3) DB에 데이터 없음 → yfinance 최초 수집 (blocking, 최초 1회만)
    4) DB 미연결 → yfinance 직접 수집

    include_market=True  → ALWAYS_FETCH(시장 지수) 를 tickers에 자동 추가
    include_market=False → 전달된 tickers만 사용
    """
    from backend.db.market_cache import get_prices_from_db, save_prices_to_db, get_stale_tickers
    from backend.db import is_available

    all_tickers = list(set(tickers + ALWAYS_FETCH)) if include_market else list(set(tickers))
    if not all_tickers:
        return pd.DataFrame()
    mem_key = f"close_{','.join(sorted(all_tickers))}_{period}"

    # ① 메모리 캐시 확인
    now = time.time()
    if mem_key in _cache:
        ts, val = _cache[mem_key]
        if now - ts < ttl:
            return val

    # ② DB 우선 경로 — 데이터 있으면 즉시 반환, stale은 백그라운드 갱신
    if is_available():
        db_df = get_prices_from_db(all_tickers, period)
        if db_df is not None and not db_df.empty:
            _cache[mem_key] = (now, db_df)
            # stale 티커를 백그라운드에서 비동기 갱신 (max_age 22h: daily 업데이트 주기 기준)
            stale = get_stale_tickers(all_tickers, max_age_hours=22)
            if stale:
                _bg_refresh_tickers(stale, period)
            return db_df

        # DB에 데이터 없음 → 최초 수집 (blocking, 이후엔 DB에서 서빙)
        all_stale = get_stale_tickers(all_tickers, max_age_hours=22)
        if all_stale:
            try:
                from backend.db.market_cache import _yf_download_batched
                fresh_df = _yf_download_batched(all_stale, period=period, inter_batch_sleep=0.5)
                if not fresh_df.empty:
                    save_prices_to_db(fresh_df.ffill().dropna(axis=1, how="all"))
            except Exception as e:
                _logger.warning(f"최초 yfinance 수집 실패: {e}")
        db_df = get_prices_from_db(all_tickers, period)
        if db_df is not None and not db_df.empty:
            _cache[mem_key] = (now, db_df)
            return db_df

    # ③ DB 없음 → yfinance 직접 수집 (폴백)
    try:
        from backend.db.market_cache import _yf_download_batched
        result = _yf_download_batched(all_tickers, period=period)
    except Exception as e:
        _logger.warning(f"yfinance 폴백 수집 실패: {e}")
        return pd.DataFrame()
    if not result.empty and is_available():
        save_prices_to_db(result)
    _cache[mem_key] = (now, result)
    return result


def _get_sector_etf_df_1mo(ttl: int = 300) -> pd.DataFrame:
    def _fetch():
        from backend.db.market_cache import _yf_lock
        with _yf_lock:
            data = yf.download(
                SECTOR_ETF_TICKERS, period="1mo", progress=False,
                auto_adjust=True, threads=False
            )
        if isinstance(data.columns, pd.MultiIndex):
            return data["Close"].ffill()
        return data.ffill()
    return _cached("sector_etf_1mo", ttl, _fetch)


def get_sector_etf_df(ttl: int = 60) -> pd.DataFrame:
    return _get_sector_etf_df_1mo(ttl)


def get_sector_changes() -> dict[str, float]:
    """{ 'XLK': 1.23, 'XLF': -0.45, ... } 형태로 섹터 ETF 1일 등락률 반환."""
    try:
        df = _get_sector_etf_df_1mo()
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
    """섹터 ETF 상세 테이블: [{ sector, etf, price, change_1d_pct, change_1w_pct, change_1m_pct }, ...]"""
    try:
        df = _get_sector_etf_df_1mo()
        if df.empty or len(df) < 2:
            return []

        cur = df.iloc[-1]
        prev_1d = df.iloc[-2]
        prev_1w = df.iloc[-6] if len(df) >= 6 else df.iloc[0]
        prev_1m = df.iloc[0]

        def _chg(c, p):
            if pd.isna(c) or pd.isna(p) or float(p) == 0:
                return 0.0
            return round((float(c) / float(p) - 1) * 100, 2)

        rows = []
        for label, etf in GICS_SECTOR_ETFS:
            if etf not in df.columns:
                continue
            c = cur.get(etf)
            if pd.isna(c):
                continue
            rows.append({
                "sector":        label,
                "etf":           etf,
                "price":         round(float(c), 2),
                "change_1d_pct": _chg(c, prev_1d.get(etf)),
                "change_1w_pct": _chg(c, prev_1w.get(etf)),
                "change_1m_pct": _chg(c, prev_1m.get(etf)),
            })
        return rows
    except Exception:
        return []


# ── FRED 거시경제 ────────────────────────────────────────────────────────────────

def get_fred_macro(ttl: int = 3600) -> dict:
    def _fetch():
        try:
            import pandas_datareader.data as web
            start = datetime.now() - timedelta(days=500)
            series = ["FEDFUNDS", "UNRATE", "DGS10", "DGS2", "CPIAUCSL", "A191RL1Q225SBEA", "BAMLH0A0HYM2"]
            df = web.DataReader(series, "fred", start)

            def _last(col):
                s = df[col].dropna() if col in df else pd.Series(dtype=float)
                return float(s.iloc[-1]) if len(s) else None

            fed_rate     = _last("FEDFUNDS") or 5.33
            unemployment = _last("UNRATE")   or 3.9
            y10          = _last("DGS10")    or 4.2
            y2           = _last("DGS2")     or 4.5
            t10y2y       = round(y10 - y2, 3)

            cpi = 0.0
            if "CPIAUCSL" in df:
                cpi_s = df["CPIAUCSL"].dropna()
                if len(cpi_s) >= 13:
                    cpi = round((float(cpi_s.iloc[-1]) / float(cpi_s.iloc[-13]) - 1) * 100, 2)
                elif len(cpi_s) > 0:
                    cpi = 3.4

            gdp = 0.0
            if "A191RL1Q225SBEA" in df:
                gdp_s = df["A191RL1Q225SBEA"].dropna()
                if len(gdp_s) > 0:
                    gdp = round(float(gdp_s.iloc[-1]), 2)

            hy_raw = _last("BAMLH0A0HYM2") or 3.5
            bamlh0a0hym2 = round(hy_raw * 100, 1)

            return {
                "fed_rate":      round(fed_rate, 2),
                "unemployment":  round(unemployment, 2),
                "cpi":           cpi,
                "gdp":           gdp,
                "y10":           round(y10, 3),
                "y2":            round(y2, 3),
                "t10y2y":        t10y2y,
                "bamlh0a0hym2":  bamlh0a0hym2,
                "source":        "FRED",
            }
        except Exception:
            return {
                "fed_rate": 5.33, "unemployment": 3.9,
                "cpi": 3.4, "gdp": 2.1,
                "y10": 4.2, "y2": 4.5,
                "t10y2y": round(4.2 - 4.5, 3),
                "bamlh0a0hym2": 350.0,
                "source": "fallback",
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
            "is_doom":     is_doom,
            "rate_spread": rate_spread,
            "hy_spread":   hy_spread,
            "reason":      " / ".join(reasons) if reasons else "정상 범위",
        }

    return _cached("doom_radar", ttl, _fetch)


# ── 뉴스 ────────────────────────────────────────────────────────────────────────

def get_portfolio_news(tickers: list[str], max_per: int = 2, max_macro: int = 4, ttl: int = 600) -> list[dict]:
    key = f"news_{','.join(sorted(tickers))}"

    def _fetch():
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _extract(news_list, tag, max_n):
            out = []
            for n in news_list[:max_n]:
                content = n.get("content", n)
                headline = content.get("title") or n.get("title") or n.get("headline")
                url = (
                    (content.get("canonicalUrl") or {}).get("url")
                    or content.get("url")
                    or n.get("link")
                    or n.get("url")
                    or ""
                )
                ts = content.get("pubDate") or n.get("providerPublishTime")
                if not headline:
                    continue
                if isinstance(ts, str):
                    try:
                        pub_unix = int(pd.to_datetime(ts).timestamp())
                    except Exception:
                        pub_unix = int(datetime.now().timestamp())
                elif isinstance(ts, (int, float)):
                    pub_unix = int(ts)
                else:
                    pub_unix = int(datetime.now().timestamp())
                out.append({"ticker": tag, "headline": headline.strip(), "url": url, "datetime": pub_unix})
            return out

        def _fetch_one(symbol, tag, max_n):
            try:
                news = yf.Ticker(symbol).news or []
                return _extract(news, tag, max_n)
            except Exception:
                return []

        # 종목 뉴스 + 매크로 뉴스를 한 번에 병렬 조회
        macro_sources = [("^GSPC", "MACRO", max_macro), ("^IXIC", "MACRO", max_macro), ("^TNX", "MACRO", max_macro)]
        tasks = [(t, t, max_per) for t in tickers] + macro_sources

        items = []
        with ThreadPoolExecutor(max_workers=min(len(tasks), 12)) as pool:
            futures = {pool.submit(_fetch_one, sym, tag, mx): (sym, tag) for sym, tag, mx in tasks}
            for fut in as_completed(futures):
                items.extend(fut.result())

        items.sort(key=lambda x: x["datetime"], reverse=True)
        return items[:18]

    return _cached(key, ttl, _fetch)


# ── 실적/배당 ────────────────────────────────────────────────────────────────────

def get_earnings_dividends(tickers: list[str], ttl: int = 3600) -> list[dict]:
    key = f"earn_div_{','.join(sorted(tickers))}"

    def _fetch_one(t: str) -> dict:
        earn_date = "N/A"
        div_date  = "-"
        div_yield = "0%"
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
                if dy and dy > 0:
                    pct = dy * 100 if dy < 0.10 else dy
                    div_yield = f"{pct:.2f}%"
                else:
                    div_yield = "0%"
            except Exception:
                pass
        except Exception:
            pass
        return {"ticker": t, "earn_date": earn_date, "div_date": div_date, "div_yield": div_yield}

    def _fetch():
        from concurrent.futures import ThreadPoolExecutor, as_completed
        results: dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=min(len(tickers), 8)) as pool:
            futures = {pool.submit(_fetch_one, t): t for t in tickers}
            for fut in as_completed(futures):
                row = fut.result()
                results[row["ticker"]] = row
        # 원래 순서 유지
        return [results[t] for t in tickers if t in results]

    return _cached(key, ttl, _fetch)


# ── 시장 스냅샷 ─────────────────────────────────────────────────────────────────

def get_market_snapshot(close_df: pd.DataFrame) -> dict:
    """WATCH_TICKERS 현재가/전일대비를 {prices: {ticker: {...}}, timestamp} 형태로 반환."""
    if close_df.empty or len(close_df) < 2:
        return {"prices": {}, "timestamp": datetime.now().isoformat()}

    cur, prev = close_df.iloc[-1], close_df.iloc[-2]
    prices = {}

    for ticker in SNAPSHOT_TICKERS:
        if ticker not in close_df.columns:
            continue
        c = cur.get(ticker)
        p = prev.get(ticker)
        if pd.isna(c):
            continue
        c_f = float(c)
        p_f = float(p) if p is not None and not pd.isna(p) else c_f
        change_1d = round(c_f - p_f, 2)
        change_1d_pct = round((c_f / p_f - 1) * 100, 4) if p_f != 0 else 0.0
        prices[ticker] = {
            "price":        round(c_f, 2),
            "change_1d":    change_1d,
            "change_1d_pct": change_1d_pct,
        }

    return {
        "prices":    prices,
        "timestamp": datetime.now().isoformat(),
    }

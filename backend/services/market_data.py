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
from collections import OrderedDict
from datetime import datetime, timedelta, timezone, time as _time
from typing import Any

import pandas as pd
import yfinance as yf

_logger = logging.getLogger(__name__)


def _is_us_market_open() -> bool:
    """NYSE 장중 여부 (UTC 13:30~20:00, 평일)."""
    now = datetime.now(timezone.utc)
    if now.weekday() >= 5:
        return False
    return _time(13, 30) <= now.time() < _time(20, 0)


# ── TTL 캐시 ──────────────────────────────────────────────────────────────────

# LRU + TTL 캐시.
# 키가 '정렬된 티커 집합 + 기간' 이라 사용자 포트폴리오 조합·extra_tickers(거래마다 변함)·
# news_*/earnings_* 마다 새 엔트리가 생긴다. 값은 수 MB짜리 DataFrame 이므로
# 상한이 없으면 프로세스 메모리가 무한정 증가한다.
_CACHE_MAX_ENTRIES = 256
_cache: "OrderedDict[str, tuple[float, Any]]" = OrderedDict()
_cache_lock = threading.Lock()

# 백그라운드 stale 갱신 중복 방지
_bg_in_progress: set[str] = set()
_bg_lock = threading.Lock()
# 티커별 마지막 시도 시각 — 데이터 공급자에 아직 없는 날짜(예: 당일 종가 미도착) 때문에
# 영구 stale 로 남는 티커가 캐시 미스마다 재수집을 트리거하는 것을 막는다.
_bg_last_attempt: dict[str, float] = {}
_BG_COOLDOWN = 900   # 15분


def _cache_get(key: str, ttl: int):
    """TTL 내면 값 반환하고 최근 사용으로 승격. 없거나 만료면 None."""
    with _cache_lock:
        hit = _cache.get(key)
        if hit is None:
            return None
        ts, val = hit
        if time.time() - ts >= ttl:
            _cache.pop(key, None)
            return None
        _cache.move_to_end(key)
        return val


def _cache_put(key: str, val) -> None:
    with _cache_lock:
        _cache[key] = (time.time(), val)
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX_ENTRIES:
            _cache.popitem(last=False)   # 가장 오래 안 쓴 항목 제거


def _cached(key: str, ttl: int, fn):
    val = _cache_get(key, ttl)
    if val is not None:
        return val
    val = fn()
    _cache_put(key, val)
    return val


# ── 시세 ────────────────────────────────────────────────────────────────────────

# Bloomberg 마퀴 표시 티커 (순서 = 표시 순서)
SNAPSHOT_TICKERS = [
    "^GSPC",    # S&P 500
    "^IXIC",    # Nasdaq
    "^KS11",    # KOSPI
    "^KQ11",    # KOSDAQ
    "^N225",    # Nikkei 225
    "BTC-USD",  # Bitcoin
    "USDKRW=X", # USD/KRW
    "JPYKRW=X", # YEN/KRW
    "CL=F",     # WTI Crude Oil
]

ALWAYS_FETCH = [
    "^GSPC", "^IXIC", "^KS11", "^KQ11", "^N225",
    "XLK", "XLF", "XLE", "XLY", "XLV", "XLI", "XLB",
    "BTC-USD", "GC=F", "^VIX", "^TNX", "^IRX", "CL=F",
    "USDKRW=X", "JPYKRW=X", "SPY", "QQQ", "NVDA", "AAPL", "MSFT",
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


def sector_etfs_for(market: str = "US") -> list[tuple[str, str]]:
    """해당 시장의 섹터 대표 ETF 목록.

    미국은 SPDR 11 섹터, 한국은 KODEX 업종 ETF 를 쓴다. 목록 자체는
    services/markets.py 가 갖고 있고 여기서는 꺼내 쓰기만 한다 — 시장 정의가
    두 곳으로 갈라지면 한쪽만 고치는 실수가 난다.
    """
    from backend.services.markets import get_market
    if (market or "US").upper() == "US":
        return GICS_SECTOR_ETFS
    return get_market(market).sector_etfs


# DB에 적재할 최소 이력 깊이. 어떤 엔드포인트가 촉발했든 이 깊이로 수집한다.
_CANONICAL_PERIOD = "2y"


def canonical_period(period: str) -> str:
    """수집 깊이 결정 — 호출자의 period 와 _CANONICAL_PERIOD 중 더 긴 쪽.

    /holdings-detail 이 period="1mo" 로 먼저 도착하면 그 티커는 1개월치만 저장되고
    updated_at 이 NOW() 로 찍힌다. 이후 /metrics 가 period="2y" 를 요청해도 그 티커는
    이미 '컬럼 존재 + 신선' 으로 판정돼 깊은 백필이 영원히 일어나지 않는다.
    저장 깊이를 호출자의 요청 기간과 분리해 이 굶주림을 없앤다.
    """
    from backend.db.market_cache import period_to_days
    try:
        return period if period_to_days(period) >= period_to_days(_CANONICAL_PERIOD) else _CANONICAL_PERIOD
    except Exception:
        return _CANONICAL_PERIOD


def _bg_refresh_tickers(stale: list[str], period: str) -> None:
    """stale 티커를 백그라운드 스레드에서 yfinance 수집 → DB 저장. 중복 실행 방지."""
    from backend.db.market_cache import _yf_download_batched, save_prices_to_db

    period = canonical_period(period)
    now = time.time()

    with _bg_lock:
        new_stale = [
            t for t in stale
            if t not in _bg_in_progress
            and now - _bg_last_attempt.get(t, 0.0) >= _BG_COOLDOWN
        ]
        if not new_stale:
            return
        _bg_in_progress.update(new_stale)
        for t in new_stale:
            _bg_last_attempt[t] = now

    def _worker():
        try:
            close_df = _yf_download_batched(new_stale, period=period, inter_batch_sleep=1.0)
            if not close_df.empty:
                # ffill 없이 저장: 장중 NaN close가 ffill로 전일 종가로 저장되는 현상 방지
                save_prices_to_db(close_df.dropna(axis=1, how="all"))
                _logger.debug(f"백그라운드 갱신 완료: {len(new_stale)}개 티커")
        except Exception as e:
            _logger.warning(f"백그라운드 갱신 실패: {e}")
        finally:
            with _bg_lock:
                _bg_in_progress.difference_update(new_stale)

    threading.Thread(target=_worker, daemon=True).start()


def get_close_df(
    tickers: list[str],
    period: str = "2y",
    ttl: int = 300,
    include_market: bool = True,
    fill: bool = True,
) -> pd.DataFrame:
    """
    1) 메모리 캐시 (TTL 5분) 히트 → 즉시 반환
    2) DB 데이터 즉시 반환 + stale 티커는 백그라운드에서 비동기 갱신
       (사용자 요청이 yfinance를 기다리지 않음)
    3) DB에 데이터 없음 → yfinance 최초 수집 (blocking, 최초 1회만)
    4) DB 미연결 → yfinance 직접 수집

    include_market=True  → ALWAYS_FETCH(시장 지수) 를 tickers에 자동 추가
    include_market=False → 전달된 tickers만 사용

    fill=True  (기본) — ffill + 주말 행 제거. 곡선·공분산 등 기존 소비자 전용.
    fill=False        — 실제 관측치만 남긴 희소 프레임. 일변동률 계산 전용이며
                        주말 행도 보존한다 (암호화폐·환율의 실제 주말 거래).
                        두 뷰는 메모리 캐시 키가 분리돼 서로를 오염시키지 않는다.
    """
    from backend.db.market_cache import get_prices_from_db, save_prices_to_db, get_stale_tickers
    from backend.db import is_available

    all_tickers = list(set(tickers + ALWAYS_FETCH)) if include_market else list(set(tickers))
    if not all_tickers:
        return pd.DataFrame()
    mem_key = f"close_{','.join(sorted(all_tickers))}_{period}{'' if fill else '_raw'}"

    # ① 메모리 캐시 확인 (LRU + TTL)
    now = time.time()
    _hit = _cache_get(mem_key, ttl)
    if _hit is not None:
        return _hit

    # ② DB 우선 경로 — 데이터 있으면 즉시 반환, stale은 백그라운드 갱신
    if is_available():
        db_df = get_prices_from_db(all_tickers, period, fill=fill)
        if db_df is not None and not db_df.empty:
            # DB에 전혀 없는 신규 티커(예: 방금 매수한 종목)는 즉시 동기 수집
            missing = [t for t in all_tickers if t not in db_df.columns]
            if missing:
                try:
                    from backend.db.market_cache import _yf_download_batched
                    # 호출자가 짧은 기간을 요청했더라도 DB 에는 깊게 적재한다
                    fresh = _yf_download_batched(
                        missing, period=canonical_period(period), inter_batch_sleep=0.5
                    )
                    if not fresh.empty:
                        save_prices_to_db(fresh.dropna(axis=1, how="all"))
                        db_df = pd.concat([db_df, fresh], axis=1)
                except Exception as e:
                    _logger.warning(f"신규 티커 즉시 수집 실패: {e}")
            # 주말(토·일) 행 제거 — 장외 데이터가 ffill로 전일과 동일해져 0% 변동률 오류 방지.
            # fill=False 뷰는 실제 관측치만 담고 있으므로 주말 행(암호화폐·환율의 진짜 거래)을 보존한다.
            if fill:
                db_df = db_df[db_df.index.dayofweek < 5]
            _cache_put(mem_key, db_df)
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
                fresh_df = _yf_download_batched(
                    all_stale, period=canonical_period(period), inter_batch_sleep=0.5
                )
                if not fresh_df.empty:
                    save_prices_to_db(fresh_df.dropna(axis=1, how="all"))
            except Exception as e:
                _logger.warning(f"최초 yfinance 수집 실패: {e}")
        db_df = get_prices_from_db(all_tickers, period, fill=fill)
        if db_df is not None and not db_df.empty:
            if fill:
                db_df = db_df[db_df.index.dayofweek < 5]
            _cache_put(mem_key, db_df)
            return db_df

    # ③ DB 없음 → yfinance 직접 수집 (폴백)
    try:
        from backend.db.market_cache import _yf_download_batched
        result = _yf_download_batched(all_tickers, period=period)
    except Exception as e:
        _logger.warning(f"yfinance 폴백 수집 실패: {e}")
        return pd.DataFrame()
    if result is None or result.empty:
        # 빈 프레임은 RangeIndex 라 .dayofweek 접근 시 AttributeError → 500 이 된다
        return pd.DataFrame()
    if is_available():
        save_prices_to_db(result)
    if fill and isinstance(result.index, pd.DatetimeIndex):
        result = result[result.index.dayofweek < 5]
    _cache_put(mem_key, result)
    return result


def _get_sector_etf_df_1mo(ttl: int = 300, market: str = "US") -> pd.DataFrame:
    tickers = [etf for _, etf in sector_etfs_for(market)]

    def _fetch():
        from backend.db.market_cache import _yf_sem
        with _yf_sem:
            data = yf.download(
                tickers, period="1mo", progress=False,
                auto_adjust=True, threads=False
            )
        df = data["Close"].ffill() if isinstance(data.columns, pd.MultiIndex) else data.ffill()
        # 주말(토·일) 행 제거 — ffill로 채워진 주말 행이 0% 변동률을 만드는 버그 방지
        return df[df.index.dayofweek < 5]

    # 캐시 키에 시장을 넣지 않으면 미국 섹터 값이 한국 화면에 그대로 나온다.
    return _cached(f"sector_etf_1mo:{market}", ttl, _fetch)


def get_sector_etf_df(ttl: int = 60, market: str = "US") -> pd.DataFrame:
    return _get_sector_etf_df_1mo(ttl, market)


def get_sector_changes(market: str = "US") -> dict[str, float]:
    """{ 'XLK': 1.23, 'XLF': -0.45, ... } 형태로 섹터 ETF 1일 등락률 반환."""
    try:
        df = _get_sector_etf_df_1mo(market=market)
        if df.empty or len(df) < 2:
            return {}
        df = df[df.index.dayofweek < 5]  # 주말 행 제거
        if df.empty or len(df) < 2:
            return {}
        cur, prev = df.iloc[-1], df.iloc[-2]
        result = {}
        for _, etf in sector_etfs_for(market):
            if etf in df.columns:
                c, p = cur.get(etf), prev.get(etf)
                if pd.notna(c) and pd.notna(p) and p:
                    result[etf] = (float(c) / float(p) - 1) * 100
        return result
    except Exception:
        return {}


def get_sector_table(market: str = "US") -> list[dict]:
    """섹터 ETF 상세 테이블: 1D/1W/1M/3M/6M 변동률 포함.

    1D/1W: _get_sector_etf_df_1mo() (yfinance 직접 — 장 중 실시간 반영)
    1M/3M/6M: get_close_df(6mo) (DB 일봉 — 긴 기간 정확도 우선)
    """
    try:
        df_1mo = _get_sector_etf_df_1mo(market=market)
        if df_1mo.empty or len(df_1mo) < 2:
            return []

        # 주말 행 제거 후, 장 마감 후 ffill 아티팩트(마지막 두 행 동일) 제거
        df_1mo = df_1mo[df_1mo.index.dayofweek < 5]
        if len(df_1mo) >= 2 and (df_1mo.iloc[-1] == df_1mo.iloc[-2]).all():
            df_1mo = df_1mo.iloc[:-1]
        if len(df_1mo) < 2:
            return []

        cur     = df_1mo.iloc[-1]
        prev_1d = df_1mo.iloc[-2]
        prev_1w = df_1mo.iloc[-6] if len(df_1mo) >= 6 else df_1mo.iloc[0]

        # 중장기 기간은 DB 6개월 일봉 사용.
        # 이 시장의 ETF 로 받아야 한다. 미국 상수를 그대로 쓰면 한국 ETF 가
        # 프레임에 없어 아래 조회가 전부 빗나가고, 1M/3M/6M 이 통째로 0.0 이 된다.
        etf_tickers = [etf for _, etf in sector_etfs_for(market)]
        df_6mo  = get_close_df(etf_tickers, period="6mo", ttl=300, include_market=False)
        prev_1m = df_6mo.iloc[-22]  if len(df_6mo) >= 22  else df_6mo.iloc[0] if not df_6mo.empty else prev_1d
        prev_3m = df_6mo.iloc[-66]  if len(df_6mo) >= 66  else df_6mo.iloc[0] if not df_6mo.empty else prev_1d
        prev_6m = df_6mo.iloc[-132] if len(df_6mo) >= 132 else df_6mo.iloc[0] if not df_6mo.empty else prev_1d

        def _chg(c, p):
            """계산 불가는 None. 0.0 으로 돌려주면 '보합'과 구분되지 않는다."""
            if p is None or pd.isna(c) or pd.isna(p) or float(p) == 0:
                return None
            return round((float(c) / float(p) - 1) * 100, 2)

        rows = []
        for label, etf in sector_etfs_for(market):
            if etf not in df_1mo.columns:
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
                # 기본값을 현재가(c)로 두면 변동률이 0% 로 조작된다 → None 으로 둔다.
                "change_1m_pct": _chg(c, prev_1m.get(etf)),
                "change_3m_pct": _chg(c, prev_3m.get(etf)),
                "change_6m_pct": _chg(c, prev_6m.get(etf)),
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

            # `or` 를 쓰면 안 된다. `_last` 는 값이 없을 때만 None 을 주는데
            # `or` 는 실측 0.0 도 거짓으로 보고 폴백으로 갈아치운다. 제로금리
            # (2008-2015, 2020-2022)에 FEDFUNDS 는 실제로 0 에 가까웠다 —
            # 그 시기를 조회하면 "기준금리 5.33%" 가 실측값인 척 나갔다.
            #
            # 그리고 읽지 못한 값은 하드코딩 상수로 메우지 않는다. 아래
            # `source` 가 "FRED" 인지만 보고 실측으로 취급하는 소비자가 있는데
            # (ai_analysis.build_macro_block), 성공 경로에서 상수를 섞으면
            # 그 판단이 조용히 무너진다. 없으면 None 이고, 무엇이 없었는지
            # `missing` 에 적는다.
            fed_rate     = _last("FEDFUNDS")
            unemployment = _last("UNRATE")
            y10          = _last("DGS10")
            y2           = _last("DGS2")
            t10y2y       = round(y10 - y2, 3) if (y10 is not None and y2 is not None) else None

            # CPI 는 전년동월비라 13개월이 필요하다. 모자라면 계산 불가지
            # 3.4% 가 아니다 — 예전에는 그 상수를 넣어 실측값처럼 내보냈다.
            cpi = None
            if "CPIAUCSL" in df:
                cpi_s = df["CPIAUCSL"].dropna()
                if len(cpi_s) >= 13:
                    cpi = round((float(cpi_s.iloc[-1]) / float(cpi_s.iloc[-13]) - 1) * 100, 2)

            gdp = None
            if "A191RL1Q225SBEA" in df:
                gdp_s = df["A191RL1Q225SBEA"].dropna()
                if len(gdp_s) > 0:
                    gdp = round(float(gdp_s.iloc[-1]), 2)

            hy_raw = _last("BAMLH0A0HYM2")
            bamlh0a0hym2 = round(hy_raw * 100, 1) if hy_raw is not None else None

            out = {
                "fed_rate":      round(fed_rate, 2) if fed_rate is not None else None,
                "unemployment":  round(unemployment, 2) if unemployment is not None else None,
                "cpi":           cpi,
                "gdp":           gdp,
                "y10":           round(y10, 3) if y10 is not None else None,
                "y2":            round(y2, 3) if y2 is not None else None,
                "t10y2y":        t10y2y,
                "bamlh0a0hym2":  bamlh0a0hym2,
                "source":        "FRED",
            }
            missing = [k for k, v in out.items() if k != "source" and v is None]
            if missing:
                _logger.warning(f"FRED 거시지표 일부 없음: {', '.join(missing)}")
            out["missing"] = missing
            return out
        except Exception as e:
            # 예전에는 여기서 하드코딩 상수를 돌려줬다. `source: "fallback"` 으로
            # 표시는 했지만 그 표시를 보는 소비자는 프롬프트 쪽 하나뿐이고,
            # 화면은 그 숫자를 그대로 그렸다 — 조회가 실패한 날에도 "기준금리
            # 5.33%" 가 정상처럼 떴다. 그럴듯한 가짜 숫자가 빈칸보다 나쁘다.
            _logger.warning(f"FRED 거시지표 조회 실패 — 값 없이 반환: {e}")
            keys = ["fed_rate", "unemployment", "cpi", "gdp",
                    "y10", "y2", "t10y2y", "bamlh0a0hym2"]
            return {**{k: None for k in keys}, "source": "fallback", "missing": keys}

    return _cached("fred_macro", ttl, _fetch)


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
        div_yield = None
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
                # yfinance 의 dividendYield 는 **이미 퍼센트**다 (AAPL 0.34 = 0.34%).
                # 예전에는 `dy * 100 if dy < 0.10 else dy` 로 추측했는데, 그건
                # 지금 우연히 맞을 뿐이다 — 실제로 0.05% 를 주는 종목이 오면
                # 5% 로 부풀린다. 값의 크기로 단위를 추측하지 않는다.
                #
                # 주의: 같은 info dict 안에서도 필드마다 단위가 다르다.
                # payoutRatio·profitMargins·returnOnEquity 는 여전히 분수다.
                # 이 수정을 그쪽에 일괄 적용하면 100 분의 1이 된다.
                dy = (tk.info or {}).get("dividendYield")
                if dy is None:
                    # '무배당' 과 '모름' 은 다르다. 0% 로 적으면 배당주를
                    # 무배당으로 오해해 후보에서 빼게 된다.
                    div_yield = None
                else:
                    div_yield = f"{float(dy):.2f}%"
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
    """WATCH_TICKERS 현재가/전일대비를 {prices: {ticker: {...}}, timestamp} 형태로 반환.
    장중: 마지막 행이 오늘 intraday면 현재가 사용. 장외: 전날 종가 기준.
    """
    if close_df.empty or len(close_df) < 2:
        return {"prices": {}, "timestamp": datetime.now().isoformat()}
    # 주말 행 제거: 장이 열리지 않는 날은 ffill 값이 이전 행과 동일해 0% 변동률 오류 발생
    close_df = close_df[close_df.index.dayofweek < 5]
    if close_df.empty or len(close_df) < 2:
        return {"prices": {}, "timestamp": datetime.now().isoformat()}

    today_utc = datetime.now(timezone.utc).date()
    last_date = close_df.index[-1].date() if hasattr(close_df.index[-1], 'date') else None

    if _is_us_market_open() and last_date == today_utc:
        # 장중: 마지막 행 = intraday 현재가, 두번째 = 전날 종가
        cur  = close_df.iloc[-1]
        prev = close_df.iloc[-2]
    elif (not _is_us_market_open()) and last_date == today_utc:
        # 장 마감 후 오늘 행이 이미 있음: 오늘 종가 vs 전날 종가
        cur  = close_df.iloc[-1]
        prev = close_df.iloc[-2]
    else:
        # 아직 오늘 행 없음 (주말·공휴일 등): 가장 최근 종가 vs 그 전날
        cur  = close_df.iloc[-1]
        prev = close_df.iloc[-2]

    prices = {}

    for ticker in SNAPSHOT_TICKERS:
        if ticker not in close_df.columns:
            continue
        c = cur.get(ticker)
        p = prev.get(ticker)
        # 현재가 NaN이면 유효한 최근 행으로 fallback
        if c is None or pd.isna(c):
            col = close_df[ticker].dropna()
            if col.empty:
                continue
            c = col.iloc[-1]
            p = col.iloc[-2] if len(col) >= 2 else c
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

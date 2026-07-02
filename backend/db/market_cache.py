"""
backend/db/market_cache.py
────────────────────────────
공통 시장 데이터 DB 캐시.

- market_prices   : 일별 종가 (2년치, 모든 사용자 공통)
- market_snapshot : 현재가 + 등락률 (1분마다 갱신)
- common_cache    : macro, doom_radar, sector 등 JSON 형태 공통 데이터
"""
from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import threading

import pandas as pd

from backend.db import get_conn, is_available

_yf_lock = threading.Lock()  # yfinance SQLite 캐시 동시접근 방지


def _yf_download_batched(
    tickers: list[str],
    period: str,
    batch_size: int = 50,
    inter_batch_sleep: float = 0.0,
    **kwargs,
) -> pd.DataFrame:
    """
    대량 티커를 batch_size 단위로 나눠 순차 다운로드.
    각 배치는 _yf_lock 획득 + threads=False로 실행해 fd 고갈을 방지한다.
    inter_batch_sleep > 0 이면 배치 사이에 슬립 — 사용자 요청이 _yf_lock을 획득할 기회를 준다.
    반환값은 Close 가격만 포함하는 DataFrame.
    """
    import yfinance as yf
    frames: list[pd.DataFrame] = []
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i : i + batch_size]
        try:
            with _yf_lock:
                data = yf.download(
                    batch, period=period, progress=False,
                    auto_adjust=True, threads=False, **kwargs
                )
            if data.empty:
                continue
            close = (
                data["Close"]
                if isinstance(data.columns, pd.MultiIndex)
                else data
            )
            frames.append(close)
        except Exception as e:
            logger.warning(f"배치 다운로드 실패 (sample: {batch[:3]}): {e}")
        if inter_batch_sleep > 0 and i + batch_size < len(tickers):
            time.sleep(inter_batch_sleep)
    return pd.concat(frames, axis=1) if frames else pd.DataFrame()

logger = logging.getLogger(__name__)

_PERIOD_DAYS: dict[str, int] = {
    "1d": 2, "5d": 10, "1mo": 40, "3mo": 100,
    "6mo": 200, "1y": 400, "2y": 800, "5y": 2000,
}
_STALE_HOURS = 12   # 가격 이력 재수집 기준 (시간)


def period_to_days(period: str) -> int:
    return _PERIOD_DAYS.get(period, 800)


# ── market_prices (일별 종가) ──────────────────────────────────────────────────

def get_prices_from_db(tickers: list[str], period: str = "2y") -> Optional[pd.DataFrame]:
    """DB → DatetimeIndex × ticker DataFrame. 데이터 없으면 None."""
    if not is_available() or not tickers:
        return None
    days = period_to_days(period)
    since = date.today() - timedelta(days=days + 30)
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT ticker, price_date, close_price
                       FROM market_prices
                       WHERE ticker = ANY(%s) AND price_date >= %s
                       ORDER BY price_date ASC""",
                    (tickers, since),
                )
                rows = cur.fetchall()
        if not rows:
            return None
        df = pd.DataFrame(rows, columns=["ticker", "date", "close"])
        df["date"] = pd.to_datetime(df["date"])
        # 중복 (ticker, date) 가 있으면 마지막 값 사용
        df = df.drop_duplicates(subset=["ticker", "date"], keep="last")
        pivot = df.pivot(index="date", columns="ticker", values="close")
        pivot.index.name = None
        pivot.columns.name = None
        return pivot
    except Exception as e:
        logger.warning(f"DB get_prices_from_db 실패: {e}")
        return None


def save_prices_to_db(df: pd.DataFrame):
    """close_df (DatetimeIndex × tickers) → market_prices upsert."""
    if not is_available() or df is None or df.empty:
        return
    try:
        from psycopg2.extras import execute_values

        rows = []
        for dt, row in df.iterrows():
            d = dt.date() if hasattr(dt, "date") else dt
            for ticker in df.columns:
                val = row.get(ticker)
                if val is not None and not pd.isna(val):
                    rows.append((str(ticker), d, float(val)))
        if not rows:
            return
        # 배치 크기 제한 (너무 크면 DB 타임아웃)
        batch = 5000
        with get_conn() as conn:
            with conn.cursor() as cur:
                for i in range(0, len(rows), batch):
                    execute_values(
                        cur,
                        """INSERT INTO market_prices(ticker, price_date, close_price)
                           VALUES %s
                           ON CONFLICT(ticker, price_date) DO UPDATE
                           SET close_price=EXCLUDED.close_price, updated_at=NOW()""",
                        rows[i:i + batch],
                    )
        logger.debug(f"market_prices 저장: {len(rows)}행")
    except Exception as e:
        logger.warning(f"DB save_prices_to_db 실패: {e}")


def get_stale_tickers(tickers: list[str], max_age_hours: int = _STALE_HOURS) -> list[str]:
    """DB 캐시가 없거나 오래된 ticker 목록 반환."""
    if not is_available():
        return list(tickers)
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT ticker, MAX(updated_at) AS last_updated
                       FROM market_prices
                       WHERE ticker = ANY(%s)
                       GROUP BY ticker""",
                    (tickers,),
                )
                rows = cur.fetchall()
        if not rows:
            return list(tickers)

        now_utc = datetime.now(tz=timezone.utc)
        fresh: set[str] = set()
        for ticker, last_updated in rows:
            if last_updated is None:
                continue
            # last_updated 가 naive datetime 이면 UTC로 간주
            if last_updated.tzinfo is None:
                last_updated = last_updated.replace(tzinfo=timezone.utc)
            age_h = (now_utc - last_updated).total_seconds() / 3600
            if age_h < max_age_hours:
                fresh.add(ticker)
        return [t for t in tickers if t not in fresh]
    except Exception as e:
        logger.warning(f"DB get_stale_tickers 실패, 전체 stale 처리: {e}")
        return list(tickers)


# ── market_snapshot (현재가) ───────────────────────────────────────────────────

def save_snapshot(snapshot: dict[str, dict]):
    """
    { ticker: {price, change_1d, change_1d_pct} } → market_snapshot upsert.
    """
    if not is_available() or not snapshot:
        return
    try:
        from psycopg2.extras import execute_values
        rows = [
            (t, v.get("price"), v.get("change_1d"), v.get("change_1d_pct"))
            for t, v in snapshot.items()
            if v.get("price") is not None
        ]
        if not rows:
            return
        with get_conn() as conn:
            with conn.cursor() as cur:
                execute_values(
                    cur,
                    """INSERT INTO market_snapshot(ticker, price, change_1d, change_1d_pct)
                       VALUES %s
                       ON CONFLICT(ticker) DO UPDATE
                       SET price=EXCLUDED.price,
                           change_1d=EXCLUDED.change_1d,
                           change_1d_pct=EXCLUDED.change_1d_pct,
                           updated_at=NOW()""",
                    rows,
                )
    except Exception as e:
        logger.warning(f"DB save_snapshot 실패: {e}")


def get_snapshot() -> dict[str, dict]:
    """market_snapshot 테이블에서 현재가 전체 조회."""
    if not is_available():
        return {}
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT ticker, price, change_1d, change_1d_pct, updated_at "
                    "FROM market_snapshot"
                )
                rows = cur.fetchall()
        return {
            r[0]: {
                "price":        r[1],
                "change_1d":    r[2],
                "change_1d_pct": r[3],
                "updated_at":   r[4].isoformat() if r[4] else None,
            }
            for r in rows
        }
    except Exception as e:
        logger.warning(f"DB get_snapshot 실패: {e}")
        return {}


def is_snapshot_fresh(max_age_seconds: int = 90) -> bool:
    """스냅샷이 max_age_seconds 이내에 갱신됐는지 확인."""
    if not is_available():
        return False
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT MAX(updated_at) FROM market_snapshot")
                row = cur.fetchone()
        if not row or row[0] is None:
            return False
        last = row[0]
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return (datetime.now(tz=timezone.utc) - last).total_seconds() < max_age_seconds
    except Exception:
        return False


# ── common_cache (macro, doom_radar, sector 등) ───────────────────────────────

def save_common(cache_type: str, data: Any, ttl_seconds: int = 3600):
    """JSON 직렬화 가능한 공통 데이터를 DB에 저장."""
    if not is_available():
        return
    expires = datetime.now(tz=timezone.utc) + timedelta(seconds=ttl_seconds)
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO common_cache(cache_type, data, updated_at, expires_at)
                       VALUES(%s,%s,NOW(),%s)
                       ON CONFLICT(cache_type) DO UPDATE
                       SET data=EXCLUDED.data,
                           updated_at=NOW(),
                           expires_at=EXCLUDED.expires_at""",
                    (cache_type, json.dumps(data), expires),
                )
    except Exception as e:
        logger.warning(f"DB save_common({cache_type}) 실패: {e}")


def get_common(cache_type: str) -> Optional[Any]:
    """만료되지 않은 공통 캐시 반환. 없거나 만료면 None."""
    if not is_available():
        return None
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT data FROM common_cache
                       WHERE cache_type=%s
                         AND (expires_at IS NULL OR expires_at > NOW())""",
                    (cache_type,),
                )
                row = cur.fetchone()
        if row is None or row[0] is None:
            return None
        val = row[0]
        # psycopg2가 JSONB를 이미 Python 객체로 파싱한 경우 그대로 반환
        if isinstance(val, (dict, list, bool, int, float)):
            return val
        # str인 경우: 이미 파싱된 Python 문자열이거나 raw JSON 문자열일 수 있음
        if isinstance(val, str):
            try:
                parsed = json.loads(val)
                return parsed
            except (json.JSONDecodeError, ValueError):
                return val  # JSON 파싱 실패 → raw 문자열 그대로
        return val
    except Exception as e:
        logger.warning(f"DB get_common({cache_type}) 실패: {e}")
    return None


# ── 스타트업 프리패치 ──────────────────────────────────────────────────────────

def prefetch_tickers(tickers: list[str], period: str = "2y"):
    """
    stale 티커를 yfinance 에서 수집해 DB 저장.
    스타트업 또는 12시간 주기 백그라운드 작업에서 호출.
    """
    import yfinance as yf

    stale = get_stale_tickers(tickers, max_age_hours=_STALE_HOURS)
    if not stale:
        logger.info(f"prefetch: 모든 {len(tickers)}개 티커 신선 — 스킵")
        return

    logger.info(f"prefetch: {len(stale)}/{len(tickers)}개 티커 yfinance 수집 (배치 50)")
    try:
        import logging as _logging
        _yf_log = _logging.getLogger("yfinance")
        _prev_level = _yf_log.level
        _yf_log.setLevel(_logging.CRITICAL)
        try:
            close_df = _yf_download_batched(stale, period=period)
        finally:
            _yf_log.setLevel(_prev_level)

        if close_df.empty:
            logger.warning("prefetch: yfinance 빈 응답")
            return
        close_df = close_df.ffill().dropna(axis=1, how="all")
        if not close_df.empty:
            save_prices_to_db(close_df)
            logger.info(f"prefetch 완료: {close_df.shape[1]}개 티커 저장")
    except Exception as e:
        logger.warning(f"prefetch yfinance 실패: {e}")

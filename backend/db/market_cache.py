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

import os
import threading

import pandas as pd

from backend.db import get_conn, is_available

# yfinance 동시 다운로드 허용 수.
#
# 예전에는 Lock 이었다. yfinance 가 내부적으로 SQLite(타임존·쿠키 캐시)를 쓰고
# 동시 접근에서 "database is locked" 가 나며, threads=True 로 두면 fd 가 금방
# 고갈되기 때문이다. 다만 배타 락은 **모든 사용자의 시세 조회를 한 줄로 세운다** —
# A 가 받는 동안 B 는 통째로 기다린다. 동시 접속이 늘면 그대로 병목이 된다.
#
# 세마포어로 바꿔 여러 건이 동시에 나가되 총량은 묶어 둔다. 배치 안에서는
# 여전히 threads=False 라 배치 하나가 여는 소켓 수는 그대로다.
# 값은 환경변수로 조절할 수 있게 해 두었다 — 인스턴스 사양이 바뀌면 같이 조정한다.
_YF_CONCURRENCY = max(1, int(os.getenv("YF_CONCURRENCY", "4")))
_yf_sem = threading.BoundedSemaphore(_YF_CONCURRENCY)

# 타임존 캐시를 쓰기 가능한 곳으로 고정한다. 지정하지 않으면 홈 디렉터리에
# 만들려 하는데, 컨테이너에서는 그게 실패하거나 인스턴스마다 달라진다.
_TZ_CACHE_DIR = os.getenv("YF_TZ_CACHE_DIR", "/tmp/py-yfinance")
try:
    import yfinance as _yf_mod
    os.makedirs(_TZ_CACHE_DIR, exist_ok=True)
    _yf_mod.set_tz_cache_location(_TZ_CACHE_DIR)
except Exception:  # 캐시를 못 쓰면 매번 조회할 뿐 동작에는 지장이 없다
    pass


def _yf_download_batched(
    tickers: list[str],
    period: str,
    batch_size: int = 50,
    inter_batch_sleep: float = 0.0,
    **kwargs,
) -> pd.DataFrame:
    """
    대량 티커를 batch_size 단위로 나눠 다운로드.
    각 배치는 _yf_sem 슬롯을 잡고 threads=False 로 실행해 fd 고갈을 방지한다.
    슬롯이 여러 개라 서로 다른 요청의 배치는 동시에 나간다.
    inter_batch_sleep > 0 이면 배치 사이에 슬립 — 스케줄러처럼 티커가 많은 작업이
    슬롯을 계속 붙들지 않게 해, 사용자 요청이 끼어들 틈을 준다.
    반환값은 Close 가격만 포함하는 DataFrame.
    """
    import yfinance as yf
    frames: list[pd.DataFrame] = []
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i : i + batch_size]
        try:
            with _yf_sem:
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

def get_prices_from_db(
    tickers: list[str], period: str = "2y", fill: bool = True
) -> Optional[pd.DataFrame]:
    """DB → DatetimeIndex × ticker DataFrame. 데이터 없으면 None.

    fill=True  (기본) — 곡선·공분산 계산용. 누락 셀을 ffill 한다.
    fill=False        — **일변동률 계산 전용**. 실제 관측치만 남긴 희소 프레임.
                        pivot 인덱스는 요청한 티커들의 거래일 합집합이므로,
                        ffill 하면 24/7 자산 하나가 인덱스를 오늘까지 끌어와
                        미국 주식에 가짜 종가를 만들어낸다 (0% 변동률 버그의 원인).
    """
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
        # 티커마다 업데이트 주기가 달라 최신 행에 NaN이 생길 수 있음.
        # ffill로 마지막 유효 가격을 이월해 price=0 반환을 방지한다.
        if fill:
            pivot = pivot.ffill()
        return pivot
    except Exception as e:
        logger.warning(f"DB get_prices_from_db 실패: {e}")
        return None


def save_prices_to_db(df: pd.DataFrame):
    """close_df (DatetimeIndex × tickers) → market_prices upsert.

    캘린더 가드: 미국 증시 캘린더를 따르는 티커에 대해
      ① NYSE 비거래일(주말·공휴일) 행
      ② 아직 종가가 확정되지 않은 당일(16:00 ET 이전) 행 — 장중 부분 봉
    은 저장하지 않는다. 이 함수가 market_prices 의 **유일한 SQL 기록 지점**이므로,
    어떤 호출자가 ffill 된 프레임을 넘기더라도 위조 종가가 영구 저장되지 않는다.
    (암호화폐·환율·선물·해외 종목은 자기 캘린더로 실제 거래되므로 그대로 저장한다.)
    """
    if not is_available() or df is None or df.empty:
        return
    try:
        from psycopg2.extras import execute_values
        from backend.services.market_calendar import (
            is_us_trading_day, last_completed_session, uses_us_session_calendar,
        )

        us_cal   = {t: uses_us_session_calendar(str(t)) for t in df.columns}
        last_ses = last_completed_session()

        rows = []
        skipped = 0
        for dt, row in df.iterrows():
            d = dt.date() if hasattr(dt, "date") else dt
            day_is_session = is_us_trading_day(d)
            for ticker in df.columns:
                val = row.get(ticker)
                if val is None or pd.isna(val):
                    continue
                if us_cal[ticker]:
                    # 비거래일이거나, 아직 종가 미확정인 당일 → 저장 금지
                    if not day_is_session or d > last_ses:
                        skipped += 1
                        continue
                rows.append((str(ticker), d, float(val)))
        if skipped:
            logger.debug(f"market_prices 캘린더 가드로 {skipped}개 셀 저장 스킵")
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
    """DB 캐시가 없거나 오래된 ticker 목록 반환.

    updated_at(마지막 '기록 시각')만 보면, 새 종가를 하나도 저장하지 못한
    페이지 로드도 신선도를 갱신해버려 오늘 종가가 영원히 DB에 못 들어온다.
    → 실제 보유 데이터의 최신 **날짜**(MAX(price_date))가 기대치에 도달했는지도 함께 본다.
    기대치는 캘린더별로 다르다: 미국 주식은 마지막 확정 거래일, 24시간 자산은 어제.
    """
    if not is_available():
        return list(tickers)
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT ticker, MAX(updated_at) AS last_updated,
                              MAX(price_date)  AS last_date
                       FROM market_prices
                       WHERE ticker = ANY(%s)
                       GROUP BY ticker""",
                    (tickers,),
                )
                rows = cur.fetchall()
        if not rows:
            return list(tickers)

        from backend.services.market_calendar import (
            last_completed_session, uses_us_session_calendar,
        )
        us_expected = last_completed_session()
        # 24시간 자산은 어제까지 있으면 충분 (오늘 봉은 아직 진행 중)
        other_expected = date.today() - timedelta(days=1)

        now_utc = datetime.now(tz=timezone.utc)
        fresh: set[str] = set()
        for ticker, last_updated, last_date in rows:
            if last_updated is None:
                continue
            if last_updated.tzinfo is None:
                last_updated = last_updated.replace(tzinfo=timezone.utc)
            age_h = (now_utc - last_updated).total_seconds() / 3600
            if age_h >= max_age_hours:
                continue
            expected = us_expected if uses_us_session_calendar(ticker) else other_expected
            if last_date is not None and last_date < expected:
                continue   # 기록 시각은 최신이지만 데이터 날짜가 뒤처짐 → stale
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
        # ffill 금지: ALWAYS_FETCH 는 미국 주식 + 암호화폐 + 환율 + 해외 지수를
        # 한 배치로 받으므로 인덱스가 모든 캘린더의 합집합이 된다. 여기서 ffill 하면
        # 미국 주식이 거래하지 않은 날짜에 전일 종가가 복제돼 DB에 영구 저장된다.
        close_df = close_df.dropna(axis=1, how="all")
        if not close_df.empty:
            save_prices_to_db(close_df)
            logger.info(f"prefetch 완료: {close_df.shape[1]}개 티커 저장")
    except Exception as e:
        logger.warning(f"prefetch yfinance 실패: {e}")

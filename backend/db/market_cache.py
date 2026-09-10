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

logger = logging.getLogger(__name__)

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


def _yf_cache_db_has_tables(path: str) -> bool:
    """sqlite 파일에 테이블이 하나라도 있으면 True. 열 수 없어도 손상으로 간주해 False."""
    import sqlite3
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
        try:
            cur = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'")
            return cur.fetchone()[0] > 0
        finally:
            conn.close()
    except Exception:
        return False


def _repair_yf_cache_dir(cache_dir: str) -> None:
    """yfinance 타임존/쿠키/ISIN 캐시(sqlite, peewee) 손상 복구.

    비정상 종료(강제 kill 등)로 WAL 체크포인트가 유실되면 .db 파일은 남아 있는데
    테이블이 하나도 없는 상태가 될 수 있다. yfinance 는 이를 스스로 감지하지 않고
    'no such table: _tz_kv' 를 그대로 던진다 — 그리고 파일이 이미 존재하니 다시
    만들지도 않아, 한 번 이 상태가 되면 재시작해도 영구히 재발한다(실제로 겪은 버그:
    백엔드 프로세스를 강제 종료했더니 이후 모든 종목 검색이 이 오류로 깨졌다).
    시작 시 sqlite3 표준 라이브러리만으로 가볍게 확인해, 손상됐으면 지워서
    yfinance 가 다음 접근에서 깨끗하게 새로 만들게 한다.
    """
    for name in ("tkr-tz.db", "cookies.db", "isin-tkr.db"):
        path = os.path.join(cache_dir, name)
        if not os.path.isfile(path) or _yf_cache_db_has_tables(path):
            continue
        for suffix in ("", "-shm", "-wal"):
            try:
                os.remove(path + suffix)
            except OSError:
                pass
        logger.warning(f"yfinance 캐시 손상 감지 → 재생성: {path}")


# 타임존 캐시를 쓰기 가능한 곳으로 고정한다. 지정하지 않으면 홈 디렉터리에
# 만들려 하는데, 컨테이너에서는 그게 실패하거나 인스턴스마다 달라진다.
_TZ_CACHE_DIR = os.getenv("YF_TZ_CACHE_DIR", "/tmp/py-yfinance")
try:
    import yfinance as _yf_mod
    os.makedirs(_TZ_CACHE_DIR, exist_ok=True)
    _repair_yf_cache_dir(_TZ_CACHE_DIR)
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


def _yf_download_ohlcv_batched(
    tickers: list[str],
    period: str,
    batch_size: int = 50,
    inter_batch_sleep: float = 0.0,
    **kwargs,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """`_yf_download_batched` 와 동일한 배치/세마포어 구조.

    다른 점은 같은 응답에서 Close 와 Volume 을 함께 꺼내 `(close_df, volume_df)` 로
    반환한다는 것뿐이다. 트레이딩 신호 스캔이 거래량을 필요로 하는데, 종가 수집과
    별도로 한 번 더 내려받으면 yfinance 호출이 두 배가 되므로 한 응답에서 같이 처리한다.
    """
    import yfinance as yf
    close_frames: list[pd.DataFrame] = []
    vol_frames:   list[pd.DataFrame] = []
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
            if isinstance(data.columns, pd.MultiIndex):
                lvl0 = data.columns.get_level_values(0)
                if "Close"  in lvl0: close_frames.append(data["Close"])
                if "Volume" in lvl0: vol_frames.append(data["Volume"])
            else:
                # 단일 티커 응답 — 컬럼이 평면
                if "Close" in data.columns:
                    close_frames.append(data[["Close"]].rename(columns={"Close": batch[0]}))
                if "Volume" in data.columns:
                    vol_frames.append(data[["Volume"]].rename(columns={"Volume": batch[0]}))
        except Exception as e:
            logger.warning(f"OHLCV 배치 다운로드 실패 (sample: {batch[:3]}): {e}")
        if inter_batch_sleep > 0 and i + batch_size < len(tickers):
            time.sleep(inter_batch_sleep)
    close_df = pd.concat(close_frames, axis=1) if close_frames else pd.DataFrame()
    vol_df   = pd.concat(vol_frames,   axis=1) if vol_frames   else pd.DataFrame()
    return close_df, vol_df


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


def get_volume_from_db(
    tickers: list[str], period: str = "1y"
) -> Optional[pd.DataFrame]:
    """DB → DatetimeIndex × ticker 거래량 DataFrame. volume 이 있는 행만.

    ffill 하지 않는다 — 거래량은 0/결측의 의미가 달라 이월하면 왜곡된다.
    거래량이 아직 적재되지 않은 티커는 컬럼 자체가 빠진다.
    """
    if not is_available() or not tickers:
        return None
    days = period_to_days(period)
    since = date.today() - timedelta(days=days + 30)
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT ticker, price_date, volume
                       FROM market_prices
                       WHERE ticker = ANY(%s) AND price_date >= %s AND volume IS NOT NULL
                       ORDER BY price_date ASC""",
                    (tickers, since),
                )
                rows = cur.fetchall()
        if not rows:
            return None
        df = pd.DataFrame(rows, columns=["ticker", "date", "volume"])
        df["date"] = pd.to_datetime(df["date"])
        df = df.drop_duplicates(subset=["ticker", "date"], keep="last")
        pivot = df.pivot(index="date", columns="ticker", values="volume")
        pivot.index.name = None
        pivot.columns.name = None
        return pivot
    except Exception as e:
        logger.warning(f"DB get_volume_from_db 실패: {e}")
        return None


def save_prices_to_db(df: pd.DataFrame, volume_df: Optional[pd.DataFrame] = None):
    """close_df (DatetimeIndex × tickers) → market_prices upsert.

    캘린더 가드: 각 티커가 따르는 증시 캘린더에 대해
      ① 비거래일(주말·공휴일) 행
      ② 아직 종가가 확정되지 않은 당일 행 — 장중 부분 봉
    은 저장하지 않는다. 미국은 NYSE 캘린더와 16:00 ET, 한국은 KRX 캘린더와
    15:30 KST 가 기준이다. 이 함수가 market_prices 의 **유일한 SQL 기록 지점**
    이므로, 어떤 호출자가 ffill 된 프레임을 넘기더라도 위조 종가가 영구 저장되지
    않는다.

    한국 종목도 가드해야 한다. .KS/.KQ 는 미국 캘린더를 따르지 않는다는 이유로
    가드를 통째로 건너뛰고 있었는데, 그러면 장중 09:00~15:30 에 수집한 부분 봉이
    그날의 확정 종가로 DB 에 박힌다.
    (암호화폐·환율·선물은 자기 캘린더로 24시간 거래되므로 그대로 저장한다.)

    volume_df 를 함께 주면 같은 (ticker, price_date) 행에 거래량을 붙인다. 종가와
    동일한 캘린더 가드를 통과한 행만 저장된다. 종가만 넘어온 호출은 기존 거래량을
    지우지 않는다 (COALESCE) — 그래서 거래량 없는 경로(_yf_download_batched)가
    같은 행을 덮어써도 손실이 없다.
    """
    if not is_available() or df is None or df.empty:
        return
    try:
        from psycopg2.extras import execute_values
        from backend.services.market_calendar import (
            is_us_trading_day, last_completed_session, uses_us_session_calendar,
            is_kr_trading_day, last_completed_kr_session, uses_kr_session_calendar,
        )

        us_cal   = {t: uses_us_session_calendar(str(t)) for t in df.columns}
        kr_cal   = {t: uses_kr_session_calendar(str(t)) for t in df.columns}
        last_ses = last_completed_session()
        # 한국 티커가 없으면 KRX 캘린더를 굳이 받아오지 않는다 (yfinance 호출 1회).
        last_kr  = last_completed_kr_session() if any(kr_cal.values()) else None

        def _vol_at(ticker, dt) -> "float | None":
            if volume_df is None or ticker not in volume_df.columns:
                return None
            try:
                v = volume_df.at[dt, ticker]
            except (KeyError, TypeError):
                return None
            if v is None or pd.isna(v):
                return None
            fv = float(v)
            return fv if fv >= 0 else None

        rows = []
        skipped = 0
        for dt, row in df.iterrows():
            d = dt.date() if hasattr(dt, "date") else dt
            day_is_session = is_us_trading_day(d)
            kr_is_session  = is_kr_trading_day(d) if last_kr is not None else False
            for ticker in df.columns:
                val = row.get(ticker)
                if val is None or pd.isna(val):
                    continue
                if us_cal[ticker]:
                    # 비거래일이거나, 아직 종가 미확정인 당일 → 저장 금지
                    if not day_is_session or d > last_ses:
                        skipped += 1
                        continue
                elif kr_cal[ticker]:
                    if not kr_is_session or d > last_kr:
                        skipped += 1
                        continue
                rows.append((str(ticker), d, float(val), _vol_at(ticker, dt)))
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
                        """INSERT INTO market_prices(ticker, price_date, close_price, volume)
                           VALUES %s
                           ON CONFLICT(ticker, price_date) DO UPDATE
                           SET close_price=EXCLUDED.close_price,
                               volume=COALESCE(EXCLUDED.volume, market_prices.volume),
                               updated_at=NOW()""",
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
            last_completed_session, last_completed_kr_session,
            uses_us_session_calendar, uses_kr_session_calendar,
        )
        us_expected = last_completed_session()
        # 한국 종목을 '어제' 기준으로 보면 안 된다. KRX 는 15:30 KST 에 닫히므로
        # 그 시각을 지난 당일 종가가 이미 확정돼 있고, 반대로 연휴 중에는 어제가
        # 거래일이 아니라 영원히 미달로 잡힌다.
        kr_expected = last_completed_kr_session() if any(
            uses_kr_session_calendar(str(t)) for t in tickers
        ) else None
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
            if uses_us_session_calendar(ticker):
                expected = us_expected
            elif kr_expected is not None and uses_kr_session_calendar(ticker):
                expected = kr_expected
            else:
                expected = other_expected
            if last_date is not None and last_date < expected:
                continue   # 기록 시각은 최신이지만 데이터 날짜가 뒤처짐 → stale
            fresh.add(ticker)
        return [t for t in tickers if t not in fresh]
    except Exception as e:
        logger.warning(f"DB get_stale_tickers 실패, 전체 stale 처리: {e}")
        return list(tickers)


def _expected_sessions(since: date, until: date, calendar: str) -> int:
    """[since, until] 구간의 예상 거래일 수.

    거래일 수는 시장마다 다르다. 같은 220일이라도 미국은 약 150일, 한국은
    약 146일이다 — 설·추석·광복절·개천절 등 한국 공휴일이 더 많다. 그래서
    '150행 이상'처럼 상수로 판정하면 한국 종목은 **영원히 미달**로 남아,
    이미 다 받아 둔 종목을 수집기가 매번 다시 내려받고 신호 스캔은 한 번도
    갱신되지 않는다. 실제 캘린더에서 세어 이 문제를 없앤다.
    """
    from backend.services.market_calendar import is_us_trading_day, is_kr_trading_day

    if calendar == "OTHER":
        return (until - since).days + 1     # 암호화폐·환율은 매일 거래

    check = is_kr_trading_day if calendar == "KR" else is_us_trading_day
    n, d = 0, since
    while d <= until:
        if check(d):
            n += 1
        d += timedelta(days=1)
    return n


def get_volume_stale_tickers(
    tickers: list[str], min_rows: int | None = None, lookback_days: int = 220
) -> list[str]:
    """거래량 이력이 부족한 티커 목록.

    - 최근 lookback_days 안의 non-null volume 행이 기대 거래일의 90% 미만 → stale
    - 최신 volume 날짜가 그 시장의 마지막 확정 세션보다 뒤처짐            → stale
    최초 배포 직후엔 전 종목이 여기 걸려 한 번만 백필된다. 이후엔 종가가 매일
    stale 이라 같은 다운로드에 거래량이 따라오므로 자연히 비게 된다.

    min_rows 를 주면 캘린더 계산 대신 그 값을 쓴다(테스트용). 기본값 None 일
    때는 시장별 거래일 수에서 자동으로 정한다 — 상수를 쓰면 거래일이 적은
    시장이 영구히 stale 로 남는다.
    """
    if not is_available() or not tickers:
        return list(tickers)
    since = date.today() - timedelta(days=lookback_days)
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT ticker,
                              COUNT(volume) AS vcount,
                              MAX(price_date) FILTER (WHERE volume IS NOT NULL) AS last_vol
                       FROM market_prices
                       WHERE ticker = ANY(%s) AND price_date >= %s
                       GROUP BY ticker""",
                    (tickers, since),
                )
                rows = cur.fetchall()
        from backend.services.market_calendar import (
            last_completed_session, last_completed_kr_session,
            uses_kr_session_calendar, uses_us_session_calendar,
        )
        have: dict[str, tuple] = {r[0]: (r[1], r[2]) for r in rows}

        def _calendar_of(ticker: str) -> str:
            if uses_us_session_calendar(ticker):
                return "US"
            return "KR" if uses_kr_session_calendar(ticker) else "OTHER"

        today = date.today()
        # 캘린더별 기준값은 티커마다 다시 계산하면 비싸다 (거래일을 하루씩 센다).
        # 이 목록에 실제로 등장하는 캘린더만 한 번씩 구해 재사용한다.
        needed = {_calendar_of(t) for t in tickers}
        floor: dict[str, int] = {
            cal: int(_expected_sessions(since, today, cal) * 0.9) if min_rows is None else min_rows
            for cal in needed
        }
        last_ok: dict[str, date] = {}
        for cal in needed:
            if cal == "US":
                last_ok[cal] = last_completed_session()
            elif cal == "KR":
                last_ok[cal] = last_completed_kr_session()
            else:
                last_ok[cal] = today - timedelta(days=1)

        stale: list[str] = []
        for t in tickers:
            rec = have.get(t)
            if rec is None:
                stale.append(t); continue
            cal = _calendar_of(t)
            vcount, last_vol = rec
            if (vcount or 0) < floor[cal] or last_vol is None or last_vol < last_ok[cal]:
                stale.append(t)
        return stale
    except Exception as e:
        logger.warning(f"DB get_volume_stale_tickers 실패, 전체 stale 처리: {e}")
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

"""
backend/db/scheduler.py
─────────────────────────
공통 시장 데이터 백그라운드 갱신 스케줄러.

갱신 주기:
  - market_snapshot (현재가)       : 60초마다
  - market_prices (일별 종가)      : 12시간마다 (stale 티커만)
  - macro_data / doom_radar       : 60분마다
  - sector_data                   : 5분마다
"""
from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)

_stop_event = threading.Event()
_thread: threading.Thread | None = None

# 주기 (초)
_SNAPSHOT_INTERVAL      = 60        # 1분
_SECTOR_INTERVAL        = 300       # 5분
_MACRO_INTERVAL         = 3600      # 1시간
_HISTORY_INTERVAL       = 43200     # 12시간
_BB_SCAN_INTERVAL       = 21600     # 6시간 (Timing Engine: S&P500 볼린저 스캔)
_MACRO_SPREAD_INTERVAL  = 86400     # 24시간 (Timing Engine: 금리차/HY스프레드 백분위)


def start():
    global _thread
    _stop_event.clear()
    _thread = threading.Thread(target=_loop, name="pfp-scheduler", daemon=True)
    _thread.start()
    logger.info("백그라운드 스케줄러 시작 (snapshot 60s / sector 5m / macro 1h / history 12h / bb_scan 6h / macro_spread 24h)")


def stop():
    _stop_event.set()
    logger.info("백그라운드 스케줄러 정지 요청")


def _loop():
    last_sector       = 0.0
    last_macro        = 0.0
    last_history      = 0.0
    last_bb_scan      = 0.0
    last_macro_spread = 0.0

    while not _stop_event.is_set():
        now = time.time()

        # ① 스냅샷: 60초마다 (현재가 + 오늘치 종가)
        _run_safe("snapshot", _update_snapshot)

        # ② 섹터: 5분마다
        if now - last_sector >= _SECTOR_INTERVAL:
            _run_safe("sector", _update_sector)
            last_sector = now

        # ③ 매크로 + 도약 레이더: 1시간마다
        if now - last_macro >= _MACRO_INTERVAL:
            _run_safe("macro", _update_macro)
            last_macro = now

        # ④ 가격 이력: 12시간마다 (stale 티커만)
        if now - last_history >= _HISTORY_INTERVAL:
            _run_safe("history", _update_history)
            last_history = now

        # ⑤ Timing Engine: S&P500 볼린저 스캔, 6시간마다
        if now - last_bb_scan >= _BB_SCAN_INTERVAL:
            _run_safe("bb_scan", _update_bb_scan)
            last_bb_scan = now

        # ⑥ Timing Engine: 금리차/HY스프레드 백분위, 24시간마다
        if now - last_macro_spread >= _MACRO_SPREAD_INTERVAL:
            _run_safe("macro_spread", _update_macro_spread_history)
            last_macro_spread = now

        _stop_event.wait(_SNAPSHOT_INTERVAL)


def _run_safe(name: str, fn):
    try:
        fn()
    except Exception as e:
        logger.warning(f"스케줄러 작업 '{name}' 실패: {e}")


# ── 개별 갱신 함수 ─────────────────────────────────────────────────────────────

def _update_snapshot():
    """ALWAYS_FETCH 티커의 최근 2일치 데이터 수집 → snapshot + prices 동시 갱신."""
    import math
    import yfinance as yf
    from backend.services.market_data import ALWAYS_FETCH, SECTOR_ETF_TICKERS
    from backend.db.market_cache import save_snapshot, save_prices_to_db, _yf_lock

    tickers = list(set(ALWAYS_FETCH + SECTOR_ETF_TICKERS))
    try:
        with _yf_lock:
            data = yf.download(tickers, period="2d", progress=False, auto_adjust=True, threads=False)
        if data.empty:
            return
        # ffill 없이 raw 데이터 사용: 오늘이 NaN인 종목(장 미종료)도 올바르게 처리
        close_raw = (
            data["Close"]
            if hasattr(data.columns, "levels")
            else data
        )

        # DB 저장: 전부 NaN인 행 제외
        close_for_db = close_raw.dropna(how="all")
        if not close_for_db.empty:
            save_prices_to_db(close_for_db)

        # 스냅샷: 티커별 마지막 2개 유효값으로 변동률 계산
        snap = {}
        for t in close_raw.columns:
            series = close_raw[t].dropna()
            if series.empty:
                continue
            c_f = float(series.iloc[-1])
            if not math.isfinite(c_f):
                continue
            p_f = float(series.iloc[-2]) if len(series) >= 2 else c_f
            if not math.isfinite(p_f):
                p_f = c_f
            snap[str(t)] = {
                "price":         round(c_f, 4),
                "change_1d":     round(c_f - p_f, 4),
                "change_1d_pct": round((c_f / p_f - 1) * 100, 4) if p_f else 0.0,
            }
        if snap:
            save_snapshot(snap)
        logger.debug(f"snapshot 갱신 완료: {len(snap)}개 티커")
    except Exception as e:
        logger.warning(f"_update_snapshot yfinance 실패: {e}")


def _update_sector():
    """섹터 ETF 성과 데이터 갱신 → common_cache 저장."""
    from backend.services.market_data import get_sector_table, get_sector_changes
    from backend.db.market_cache import save_common

    try:
        table   = get_sector_table()
        changes = get_sector_changes()
        save_common("sector_table",   table,   ttl_seconds=_SECTOR_INTERVAL * 2)
        save_common("sector_changes", changes, ttl_seconds=_SECTOR_INTERVAL * 2)
        logger.debug("sector 갱신 완료")
    except Exception as e:
        logger.warning(f"_update_sector 실패: {e}")


def _update_macro():
    """FRED 매크로 갱신 → common_cache 저장."""
    from backend.services.market_data import get_fred_macro
    from backend.db.market_cache import save_common

    try:
        macro = get_fred_macro(ttl=0)
        save_common("macro_data", macro, ttl_seconds=_MACRO_INTERVAL * 2)
        logger.debug("macro 갱신 완료")
    except Exception as e:
        logger.warning(f"_update_macro 실패: {e}")


def _update_history():
    """2년치 가격 이력 stale 티커 재수집."""
    from backend.services.market_data import ALWAYS_FETCH, SECTOR_ETF_TICKERS
    from backend.db.market_cache import prefetch_tickers

    tickers = list(set(ALWAYS_FETCH + SECTOR_ETF_TICKERS))
    logger.info(f"가격 이력 갱신 시작: {len(tickers)}개 티커")
    prefetch_tickers(tickers, period="2y")
    logger.info("가격 이력 갱신 완료")


def _update_bb_scan():
    """Timing Engine: S&P500 전체 3년 볼린저 밴드 스캔 → common_cache 저장."""
    import pandas as pd
    from backend.services.market_data import get_close_df
    from backend.services.trading_signals import get_sp500_universe, bollinger_scan_full_universe
    from backend.db.market_cache import save_common, _yf_download_batched

    universe = get_sp500_universe()
    close_df = _yf_download_batched(universe, period="5y")
    if close_df.empty:
        logger.warning("bb_scan: 데이터 없음, 스킵")
        return
    close_df = close_df.ffill()
    cutoff   = close_df.index.max() - pd.Timedelta(days=365 * 3)
    trimmed  = close_df[close_df.index >= cutoff]
    valid_cols = [c for c in universe if c in trimmed.columns]
    result = bollinger_scan_full_universe(trimmed[valid_cols], top_n=10)
    save_common("bb_scan_sp500", result, ttl_seconds=_BB_SCAN_INTERVAL * 2)
    logger.info(f"bb_scan 갱신 완료: {result.get('scanned', 0)}개 종목 스캔")


def _update_macro_spread_history():
    """Timing Engine: 금리차/HY스프레드 과거 백분위 기반 Low/Normal/High 분류 → common_cache 저장."""
    from backend.services.trading_signals import compute_macro_spread_levels
    from backend.db.market_cache import save_common

    result = compute_macro_spread_levels()
    save_common("market_situation", result, ttl_seconds=_MACRO_SPREAD_INTERVAL * 2)
    logger.info("macro_spread 갱신 완료")


# ── 사용자 개인 데이터 즉시 갱신 (API 요청 시 호출) ───────────────────────────

def refresh_user_prices(tickers: list[str]):
    """
    사용자가 새로고침 버튼을 눌렀을 때 개인 포트폴리오 티커의 최신 가격 강제 수집.
    """
    import yfinance as yf
    from backend.db.market_cache import save_prices_to_db, save_snapshot, _yf_lock
    from backend.services.market_data import _cache   # in-memory 캐시 무효화용

    if not tickers:
        return
    try:
        import math
        with _yf_lock:
            data = yf.download(tickers, period="5d", progress=False, auto_adjust=True, threads=False)
        if data.empty:
            return
        close_raw = (
            data["Close"]
            if hasattr(data.columns, "levels")
            else data
        )
        close_for_db = close_raw.dropna(how="all")
        if not close_for_db.empty:
            save_prices_to_db(close_for_db)

        # 스냅샷: 티커별 마지막 2개 유효값으로 변동률 계산
        snap = {}
        for t in close_raw.columns:
            series = close_raw[t].dropna()
            if series.empty:
                continue
            c_f = float(series.iloc[-1])
            if not math.isfinite(c_f):
                continue
            p_f = float(series.iloc[-2]) if len(series) >= 2 else c_f
            if not math.isfinite(p_f):
                p_f = c_f
            snap[str(t)] = {
                "price":         round(c_f, 4),
                "change_1d":     round(c_f - p_f, 4),
                "change_1d_pct": round((c_f / p_f - 1) * 100, 4) if p_f else 0.0,
            }
        if snap:
            save_snapshot(snap)

        # in-memory 캐시 무효화 (해당 티커 포함 키 제거)
        keys_to_del = [k for k in list(_cache.keys()) if any(t in k for t in tickers)]
        for k in keys_to_del:
            _cache.pop(k, None)
        logger.info(f"사용자 개인 데이터 갱신 완료: {tickers}")
    except Exception as e:
        logger.warning(f"refresh_user_prices 실패: {e}")

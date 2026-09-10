"""
backend/db/scheduler.py
─────────────────────────
공통 시장 데이터 백그라운드 갱신 스케줄러.

갱신 주기:
  - market_snapshot (현재가)        : 60초마다
  - market_prices (일별 종가)       : 12시간마다 (stale 티커만)
  - macro_data / doom_radar        : 60분마다
  - sector_data                    : 5분마다
  - S&P500 전 종목 가격 수집         : KST 06:00 (UTC 21:00) 하루 1회 — 미국 장 마감(EDT 16:00) 1시간 후
    - 서버가 꺼져 있었으면 기동 시 즉시 실행 (trigger_sp500_if_missed)
    - 수집 중에도 사용자 요청은 DB 조회 또는 yfinance 직접 호출로 정상 서비스
  - pairs trading 사전 계산         : SP500 수집 완료 직후 (인기 종목 20개)
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone, timedelta

import pandas as pd

logger = logging.getLogger(__name__)

_stop_event = threading.Event()
_thread: threading.Thread | None = None

# SP500 / 한국 일일 수집 중복 실행 방지 플래그
_sp500_updating = threading.Event()
_kr_updating = threading.Event()

# 주기 (초)
_SNAPSHOT_INTERVAL      = 60        # 1분
_SECTOR_INTERVAL        = 300       # 5분
_MACRO_INTERVAL         = 3600      # 1시간
_HISTORY_INTERVAL       = 43200     # 12시간
_SIGNAL_SCAN_INTERVAL   = 21600     # 6시간 (Timing Engine: S&P500 매매신호 스캔 재계산)
_MACRO_SPREAD_INTERVAL  = 86400     # 24시간 (Timing Engine: 금리차/HY스프레드 백분위)
_RTC_INTERVAL           = 300       # 5분  (24시간 자산: 원유·금·금리·환율·암호화폐)
_SLICE_INTERVAL         = 60        # 1분  (티어1 전량 갱신 — 장중만)
_UNIVERSE_INTERVAL      = 86400     # 24시간 (상장 티커 목록 동기화)
_CLOSE_SEED_INTERVAL    = 900       # 15분 (장 마감 후 확정 종가 반영)

# 일일 수집 시각 — **ET 기준** 17:00 (미국 장 마감 1시간 후).
# 예전에는 UTC 21:00 고정이었는데, 이는 EDT 에서만 17:00 이고 EST(11~3월)에는
# 정확히 16:00:00 ET 가 된다. last_completed_session() 이 16:00 을 '마감'으로
# 보므로(>=), 아직 확정되지 않은 당일 봉을 공식 종가로 저장하게 된다.
_DAILY_COLLECT_ET_HOUR = 17

# 한국 일일 수집 시각 — **KST 기준** 16:00 (KRX 마감 15:30 KST 30분 후).
# 미국과 같은 이유로 시장 자체 시간대를 기준으로 삼는다 — ET 로 환산한 고정
# 시각을 쓰면 서머타임 전환 때 KST 기준 수집 시각이 매번 흔들린다.
_DAILY_COLLECT_KST_HOUR = 16

# pairs 사전 계산 대상 인기 종목 (기본 파라미터 threshold=5%, top_n=5)
_PAIRS_PRECOMPUTE_TICKERS = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA", "JPM", "JNJ", "V",
    "XOM", "UNH", "PG", "MA", "HD", "CVX", "MRK", "ABBV", "LLY", "PEP",
]


def _universe_for(market: str) -> list[str]:
    """수집·스캔 대상 종목. 미국은 S&P500, 한국은 시총 상위(KOSPI200·KOSDAQ150).

    한국 유니버스는 캐시에 있으면 읽고, 비어 있으면 즉석에서 만든다.
    네이버 시총 순위는 7 요청 · 2초면 끝나 요청 경로에서 만들어도 부담이 없다.
    이게 없으면 새 환경은 누군가 수동으로 채워 줄 때까지 한국 화면이 계속 빈다.
    """
    if market == "KR":
        from backend.services.korea_universe import get_scan_universe, rebuild_scan_universe
        u = get_scan_universe()
        if not u:
            logger.info("한국 유니버스 없음 — 즉석 생성")
            rebuild_scan_universe()
            u = get_scan_universe()
        return u
    from backend.services.trading_signals import get_sp500_universe
    return get_sp500_universe()


def _pairs_targets(market: str, universe: list[str]) -> list[str]:
    """페어트레이딩을 미리 계산해 둘 종목.

    미국은 대형주 20개를 고정해 뒀지만 한국은 그런 목록이 없다. 유니버스가
    이미 시총 내림차순이므로 앞 20개가 곧 대형주다.
    """
    return universe[:20] if market == "KR" else _PAIRS_PRECOMPUTE_TICKERS


def start():
    global _thread
    _stop_event.clear()
    _thread = threading.Thread(target=_loop, name="pfp-scheduler", daemon=True)
    _thread.start()
    try:
        from backend.services.market_calendar import et_to_kst_label, now_kst
        daily = et_to_kst_label(_DAILY_COLLECT_ET_HOUR)
        now_s = now_kst().strftime("%Y-%m-%d %H:%M KST")
    except Exception:
        daily, now_s = f"{_DAILY_COLLECT_ET_HOUR}:00 ET", "?"
    logger.info(
        f"백그라운드 스케줄러 시작 [{now_s}] — "
        f"티어1 1분(장중) / 마퀴 1분 / 24h자산 5분 / 섹터 5분 / 종가반영 15분 / "
        f"매크로 1시간 / 이력백필 12시간 / 신호스캔 6시간 / "
        f"S&P500일별 {daily} / 유니버스 24시간"
    )


def stop():
    _stop_event.set()
    logger.info("백그라운드 스케줄러 정지 요청")


def trigger_sp500_if_missed():
    """서버 시작 시 호출 — KST 03:00 수집을 놓쳤거나 거래량이 아직 없으면 즉시 실행."""
    if _sp500_updating.is_set():
        logger.info("SP500 수집이 이미 진행 중 — 스킵")
        return
    if _sp500_update_due():
        logger.info("SP500 업데이트 누락 감지 → 백그라운드 즉시 수집 시작")
        threading.Thread(target=_run_sp500_with_guard, daemon=True).start()
    elif _sp500_volume_backfill_due():
        logger.info("SP500 거래량 미적재 감지 → 백그라운드 즉시 백필 시작")
        threading.Thread(target=_run_sp500_with_guard, daemon=True).start()
    else:
        logger.info("SP500 업데이트 최신 상태 — 스킵")


def _sp500_volume_backfill_due() -> bool:
    """S&P500 거래량이 아직 대부분 비어 있으면 True (신호 스캔 1차 필터가 거래량을 요구).

    최초 배포 직후 한 번만 참이 된다 — 수집이 성공하면 커버리지가 채워져 다시 거짓.
    부분 실패(일부 티커 누락)로는 재시도 폭주가 나지 않도록 커버리지 80% 를 기준으로 둔다.
    """
    try:
        from backend.services.trading_signals import get_sp500_universe
        from backend.db.market_cache import get_volume_stale_tickers

        universe = get_sp500_universe()
        if not universe:
            return False
        stale = get_volume_stale_tickers(universe)
        return len(stale) > len(universe) * 0.2
    except Exception as e:
        logger.warning(f"_sp500_volume_backfill_due 확인 실패: {e}")
        return False


def _sp500_update_due() -> bool:
    """가장 최근 ET 17:00 이후 SP500 수집이 아직 안 됐으면 True.

    UTC 고정 시각이 아니라 ET 기준으로 계산해 서머타임 전환에도
    '장 마감 1시간 후'라는 의도가 연중 유지되도록 한다.
    """
    from backend.db.market_cache import get_common
    from backend.services.market_calendar import now_et

    et_now = now_et()
    target_et = et_now.replace(
        hour=_DAILY_COLLECT_ET_HOUR, minute=0, second=0, microsecond=0
    )
    if et_now < target_et:
        target_et -= timedelta(days=1)
    target = target_et.astimezone(timezone.utc)

    last_ts = get_common("sp500_price_update_last")
    if not last_ts:
        return True
    try:
        last_dt = datetime.fromisoformat(str(last_ts))
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        return last_dt < target
    except Exception:
        return True


def _in_extended_hours() -> bool:
    """프리마켓~애프터마켓(04:00~20:00 ET). 이 시간대에는 가격이 움직인다."""
    try:
        from backend.services.market_calendar import is_us_extended_hours
        return is_us_extended_hours()
    except Exception:
        return False


# ── 실시간 시세 수집 (유니버스 / 스트리밍 / 순환 폴링) ──────────────────────────

_quote_stream = None


def _sync_universe():
    from backend.services.ticker_universe import sync_universe, set_tiers
    from backend.services.market_data import ALWAYS_FETCH, SECTOR_ETF_TICKERS
    from backend.services.live_quotes import ROUND_THE_CLOCK

    res = sync_universe()
    logger.info(f"유니버스 동기화: {res}")

    # 티어 1 = 1분마다 갱신: S&P500 + 보유 종목 + 지수·섹터 ETF + 24시간 자산.
    # 나머지 약 11,000종목은 티어 3 — 사용자가 검색/보유할 때 온디맨드로만 수집한다
    # (전 종목 1분 폴링은 1회 8.3분이 걸려 물리적으로 불가능).
    hot = set(ALWAYS_FETCH) | set(SECTOR_ETF_TICKERS) | set(ROUND_THE_CLOCK)
    try:
        from backend.services.trading_signals import get_sp500_universe
        hot |= set(get_sp500_universe())
    except Exception as e:
        logger.warning(f"S&P500 목록 조회 실패: {e}")
    try:
        from backend.db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT DISTINCT ticker FROM holdings WHERE ticker <> 'CASH'")
                hot |= {r[0] for r in cur.fetchall()}
    except Exception:
        pass
    set_tiers(tier1=hot)
    logger.info(f"티어1(1분 갱신) 대상: {len(hot)}종목")


def _start_quote_stream():
    """티어 1 종목 실시간 스트리밍 시작. 성공 시 True."""
    global _quote_stream
    if _quote_stream is not None:
        return True
    from backend.services.ticker_universe import get_tier
    from backend.services.live_quotes import QuoteStream, ROUND_THE_CLOCK
    from backend.services.market_data import ALWAYS_FETCH, SECTOR_ETF_TICKERS

    # 지수(^GSPC)·선물(CL=F)·환율(USDKRW=X)은 상장 주식이 아니라 ticker_universe 에
    # 없으므로 tier 조회만으로는 누락된다. 항상 합집합으로 구독한다.
    syms = sorted(
        set(get_tier(1))
        | set(ALWAYS_FETCH) | set(SECTOR_ETF_TICKERS) | set(ROUND_THE_CLOCK)
    )
    if not syms:
        return False
    qs = QuoteStream(flush_interval=_SNAPSHOT_INTERVAL)
    qs.start(syms)
    _quote_stream = qs
    return True


def _update_round_the_clock():
    from backend.services.live_quotes import refresh_round_the_clock
    n = refresh_round_the_clock()
    logger.debug(f"24시간 자산 갱신: {n}개")


def _update_tier1():
    from backend.services.live_quotes import refresh_tier1
    n = refresh_tier1()
    logger.debug(f"티어1(S&P500+보유+지수) 갱신: {n}개")


def _seed_closes():
    from backend.services.live_quotes import seed_closing_prices
    n = seed_closing_prices()
    logger.debug(f"확정 종가 반영: {n}개")


def _run_sp500_with_guard():
    """SP500 수집을 _sp500_updating 플래그로 감싸 동시 실행 방지."""
    _sp500_updating.set()
    try:
        _run_safe("sp500_daily", _update_daily_prices)
    finally:
        _sp500_updating.clear()


def _loop():
    last_sector       = 0.0
    last_macro        = 0.0
    last_history      = 0.0
    last_signal_scan  = 0.0
    last_macro_spread = 0.0
    last_rtc          = 0.0     # 24시간 자산 (5분)
    last_universe     = 0.0     # 유니버스 목록 동기화 (24시간)
    last_slice        = 0.0     # 유니버스 순환 갱신 (60초, 장중만)
    last_close_seed   = 0.0     # 장 마감 후 종가 반영
    stream_started    = False

    while not _stop_event.is_set():
        now = time.time()
        extended_hours = _in_extended_hours()

        # ⓪ 상장 티커 유니버스 동기화: 24시간마다 (NASDAQ Trader 공개 파일)
        if now - last_universe >= _UNIVERSE_INTERVAL:
            _run_safe("universe", _sync_universe)
            last_universe = now

        # ⓪-b 실시간 스트리밍: 최초 1회 기동 (체결 시 푸시 → 폴링 불필요)
        if not stream_started:
            stream_started = _run_safe("quote_stream", _start_quote_stream) is not False

        # ① 스냅샷: 60초마다 (현재가 + 오늘치 종가)
        _run_safe("snapshot", _update_snapshot)

        # ①-b 24시간 자산(원유·금·금리·환율·암호화폐): 5분마다, 장 개폐 무관
        if now - last_rtc >= _RTC_INTERVAL:
            _run_safe("round_the_clock", _update_round_the_clock)
            last_rtc = now

        # ①-c 티어1(S&P500 + 보유종목 + 지수·섹터ETF): 1분마다 전량 갱신.
        #      정규장뿐 아니라 프리·애프터마켓(04:00~20:00 ET)에도 가격이 움직이므로
        #      market_open 이 아니라 확장 거래시간 기준으로 돈다. 실측 515종목 24.5초.
        #      나머지 약 11,000종목은 검색/보유 시 온디맨드 수집 (live_quotes.get_quotes).
        if extended_hours and now - last_slice >= _SLICE_INTERVAL:
            _run_safe("tier1", _update_tier1)
            last_slice = now

        # ①-d 확정 종가 반영: 거래가 완전히 끝난 시간대에만.
        #      프리마켓에 실행하면 움직이는 시간외 가격을 낡은 종가로 덮어쓴다.
        if not extended_hours and now - last_close_seed >= _CLOSE_SEED_INTERVAL:
            _run_safe("close_seed", _seed_closes)
            last_close_seed = now

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

        # ⑤ Timing Engine: S&P500 매매신호 스캔 재계산(순수 DB 읽기), 6시간마다.
        #    일별 거래량 수집을 놓쳐도 캐시가 갱신되도록 하는 폴백 — 정상 경로는
        #    _update_daily_prices 완료 직후 연쇄 실행이다.
        if now - last_signal_scan >= _SIGNAL_SCAN_INTERVAL:
            _run_safe("signal_scan", _update_signal_scan)
            last_signal_scan = now

        # ⑥ Timing Engine: 금리차/HY스프레드 백분위, 24시간마다
        if now - last_macro_spread >= _MACRO_SPREAD_INTERVAL:
            _run_safe("macro_spread", _update_macro_spread_history)
            last_macro_spread = now

        # ⑦ S&P500 전 종목 가격+거래량 수집: KST 06:00 이후 하루 1회 (별도 스레드).
        #    거래량이 아직 비어 있으면(최초 배포 직후) 시각과 무관하게 1회 백필.
        if not _sp500_updating.is_set() and (_sp500_update_due() or _sp500_volume_backfill_due()):
            threading.Thread(target=_run_sp500_with_guard, daemon=True).start()

        _stop_event.wait(_SNAPSHOT_INTERVAL)


def _run_safe(name: str, fn, *args):
    """작업 실행 후 반환값 전달. 실패 시 False (호출자가 재시도 여부 판단)."""
    try:
        return fn(*args)
    except Exception as e:
        logger.warning(f"스케줄러 작업 '{name}' 실패: {e}")
        return False


# ── 개별 갱신 함수 ─────────────────────────────────────────────────────────────

def _update_snapshot():
    """
    SNAPSHOT_TICKERS(7개)만 2일치 다운로드 → snapshot 갱신.
    lock 점유 시간을 최소화해 사용자 요청과 경합하지 않도록 한다.
    ALWAYS_FETCH + SECTOR_ETF 가격 이력은 _update_history(12h)가 담당.
    """
    import math
    import yfinance as yf
    from backend.services.market_data import SNAPSHOT_TICKERS
    from backend.db.market_cache import save_snapshot, save_prices_to_db, _yf_sem

    from backend.services.price_series import daily_change

    try:
        with _yf_sem:
            # period="7d": SNAPSHOT_TICKERS 는 미국 지수 + 해외 지수 + 암호화폐 + 선물이
            # 섞여 있어 인덱스가 캘린더 합집합이 된다. 2일치로는 대부분의 티커에
            # 유효 관측치가 1개뿐이라 직전 종가를 못 찾고 변동률이 전부 0%가 됐다.
            data = yf.download(
                SNAPSHOT_TICKERS, period="7d",
                progress=False, auto_adjust=True, threads=False,
            )
        if data.empty:
            return
        close_raw = (
            data["Close"]
            if isinstance(data.columns, pd.MultiIndex)
            else data
        )
        close_for_db = close_raw.dropna(how="all")
        if not close_for_db.empty:
            save_prices_to_db(close_for_db)

        snap = {}
        for t in close_raw.columns:
            # 백엔드 전체가 동일한 '마지막 두 실제 관측치' 규칙을 쓰도록 primitive 재사용
            dc = daily_change(close_raw, str(t))
            if dc is None or not math.isfinite(dc.price):
                continue  # 값을 못 구하면 0.0 을 쓰지 말고 건너뛴다
            snap[str(t)] = {
                "price":         round(dc.price, 4),
                "change_1d":     round(dc.chg_val, 4),
                "change_1d_pct": round(dc.chg_pct, 4),
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


def _update_daily_prices(max_tickers: int | None = None, market: str = "US") -> dict:
    """
    해당 시장 전 종목의 2년치 종가 + 거래량을 market_prices DB에 저장.
    미국은 S&P 500(~500개), 한국은 시총 상위(KOSPI200 + KOSDAQ150 = 350개).

    max_tickers 를 주면 이번 호출에서 그만큼만 처리한다. Cloud Run 은 요청이
    끝나면 CPU 를 회수하므로 한 번에 500 종목을 받다가 타임아웃되면 아무것도
    저장되지 않는다. 나눠 받으면 매 호출이 진척을 남기고, 남은 종목은 다음
    호출이 이어받는다 (stale 목록이 줄어들기 때문에).

    - 배치 50개, 배치 사이 2초 슬립 → _yf_sem 슬롯을 놓는 구간에 사용자 요청 처리 가능
    - 종가 stale(max_age 20h) ∪ 거래량 stale 티커만 수집 → 신선한 티커는 yfinance 미호출
      거래량은 같은 yf.download 응답에서 꺼내므로 추가 네트워크 비용이 없다.
      최초 배포 직후 1회만 전 종목이 거래량 stale → 2년치 OHLCV 백필.
    - 완료 후 매매신호 스캔(_update_signal_scan) + pairs 사전 계산(_precompute_pairs) 연속 실행
    """
    import logging as _logging
    from backend.db.market_cache import (
        get_stale_tickers, get_volume_stale_tickers,
        _yf_download_ohlcv_batched, save_prices_to_db, save_common,
    )

    # 미국 키는 예전 이름을 유지한다. 이미 쌓인 값이 있어 이름을 바꾸면
    # 수집이 한 번 더 처음부터 도는 것처럼 보인다.
    stamp_key = "sp500_price_update_last" if market == "US" else f"price_update_last:{market}"

    universe = _universe_for(market)
    if not universe:
        logger.warning(f"[{market}] 유니버스가 비어 있어 가격 수집을 건너뛴다")
        return {"stale": 0, "processed": 0, "remaining": 0, "scan_refreshed": False}

    stale = sorted(
        set(get_stale_tickers(universe, max_age_hours=20))
        | set(get_volume_stale_tickers(universe))
    )
    if not stale:
        save_common(
            stamp_key,
            datetime.now(tz=timezone.utc).isoformat(),
            ttl_seconds=86400 * 2,
        )
        logger.info(f"[{market}] 전체 신선 — DB 스킵, 타임스탬프 갱신")
        _run_safe("signal_scan", _update_signal_scan, market)
        _run_safe("precompute_pairs", _precompute_pairs, market)
        return {"stale": 0, "processed": 0, "remaining": 0, "scan_refreshed": True}

    total_stale = len(stale)
    if max_tickers is not None and max_tickers > 0:
        stale = stale[:max_tickers]
    logger.info(
        f"[{market}] 가격+거래량 수집 시작: {len(stale)}/{total_stale}개 처리 "
        f"(전체 유니버스 {len(universe)})"
    )
    _yf_log = _logging.getLogger("yfinance")
    _prev = _yf_log.level
    _yf_log.setLevel(_logging.CRITICAL)
    try:
        close_df, volume_df = _yf_download_ohlcv_batched(
            stale, period="2y", inter_batch_sleep=2.0
        )
    finally:
        _yf_log.setLevel(_prev)

    if close_df.empty:
        logger.warning(f"[{market}] 가격 수집: yfinance 빈 응답")
        return {"stale": total_stale, "processed": 0,
                "remaining": total_stale, "scan_refreshed": False}

    save_prices_to_db(close_df.dropna(axis=1, how="all"), volume_df)
    save_common(
        stamp_key,
        datetime.now(tz=timezone.utc).isoformat(),
        ttl_seconds=86400 * 2,
    )
    processed = close_df.shape[1]
    remaining = max(0, total_stale - len(stale))
    logger.info(f"[{market}] 가격+거래량 수집 완료: {processed}개 저장 (남은 stale {remaining})")

    # 아직 받을 종목이 남았으면 스캔은 미룬다 — 반쪽 데이터로 갱신하면
    # 그 결과가 6시간 캐시에 박혀 다음 수집분이 반영되지 않는다.
    scan_refreshed = remaining == 0
    if scan_refreshed:
        _run_safe("signal_scan", _update_signal_scan, market)
        _run_safe("precompute_pairs", _precompute_pairs, market)

    return {"stale": total_stale, "processed": processed,
            "remaining": remaining, "scan_refreshed": scan_refreshed}


def _precompute_pairs(market: str = "US"):
    """
    인기 종목 20개의 페어트레이딩 결과를 common_cache에 사전 저장 (TTL 25h).
    이미 캐시가 있는 종목은 스킵. pairs-auto 엔드포인트가 이 캐시를 우선 반환.
    """
    from backend.services.trading_signals import pairs_auto_detail
    from backend.services.market_data import get_close_df
    from backend.db.market_cache import get_common, save_common

    universe = _universe_for(market)
    if not universe:
        logger.warning(f"precompute_pairs[{market}]: 유니버스 비어 있음 — 스킵")
        return
    targets  = _pairs_targets(market, universe)
    close_df = get_close_df(universe, period="2y", include_market=False)
    if close_df is None or close_df.empty:
        logger.warning(f"precompute_pairs[{market}]: 가격 데이터 없음 — 스킵")
        return

    computed = 0
    for ticker in targets:
        if ticker not in close_df.columns:
            continue
        cache_key = f"pairs_precomputed::{ticker}::5.0::5"
        if get_common(cache_key):
            continue  # 신선한 캐시 있음
        try:
            candidates = [c for c in universe if c != ticker and c in close_df.columns]
            result = pairs_auto_detail(ticker, close_df, candidates, threshold_pct=5.0, top_n=5)
            save_common(cache_key, result, ttl_seconds=90000)  # 25h TTL
            computed += 1
        except Exception as e:
            logger.warning(f"precompute_pairs {ticker} 실패: {e}")

    logger.info(f"pairs 사전계산[{market}] 완료: {computed}/{len(targets)}개")


def _update_signal_scan(market: str = "US"):
    """Timing Engine: SMA 1차 필터 + MACD/RSI 스코어링 → common_cache 저장.

    DB(market_prices)의 종가·거래량만 읽어 계산한다 — yfinance 호출이 전혀 없다.
    (거래량 적재는 _update_daily_prices 가 담당하므로 이중 수집하지 않는다.)
    """
    from backend.db.market_cache import get_prices_from_db, get_volume_from_db, save_common
    from backend.services.trading_signals import sma_macd_rsi_scan

    universe  = _universe_for(market)
    if not universe:
        logger.warning(f"signal_scan[{market}]: 유니버스 비어 있음, 스킵")
        return
    close_df  = get_prices_from_db(universe, "1y", fill=True)
    if close_df is None or close_df.empty:
        logger.warning(f"signal_scan[{market}]: 종가 데이터 없음, 스킵")
        return
    volume_df = get_volume_from_db(universe, "1y")
    valid = [c for c in universe if c in close_df.columns]
    result = sma_macd_rsi_scan(close_df[valid], volume_df, top_n=10)
    # 라우터가 읽는 키와 같아야 한다. 예전에는 'signal_scan_sp500' 로 저장하고
    # 라우터는 'signal_scan:US' 를 읽어, 미리 계산해 둔 결과가 한 번도 쓰이지
    # 않고 매 요청이 즉석 계산을 다시 하고 있었다.
    save_common(f"signal_scan:{market}", result, ttl_seconds=_SIGNAL_SCAN_INTERVAL * 5)
    # 완화 단계가 적용됐으면(level > 0) 로그에 남긴다 — '진짜 통과 종목이 없는 시장
    # 상황'인지 '수집 타이밍 등으로 인한 일시적 결핍'인지 나중에 원인을 추적할 때 쓴다.
    long_lv, short_lv = result.get("long_filter_level", 0), result.get("short_filter_level", 0)
    relax_note = ""
    if long_lv or short_lv:
        relax_note = f" [완화 적용: 매수 L{long_lv}({result.get('long_filter_note')}) / 매도 L{short_lv}({result.get('short_filter_note')})]"
    logger.info(
        f"신호 스캔[{market}] 갱신 완료: {result.get('scanned', 0)}개 스캔 · "
        f"매수 {len(result.get('long_picks', []))} / 매도 {len(result.get('short_picks', []))}{relax_note}"
    )


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
    from backend.db.market_cache import save_prices_to_db, save_snapshot, _yf_sem
    from backend.services.market_data import _cache   # in-memory 캐시 무효화용

    if not tickers:
        return
    try:
        import math
        from backend.services.market_data import canonical_period
        with _yf_sem:
            # 5d 로 받아 저장하면 updated_at 만 새로 찍혀 '신선' 판정이 나고,
            # 그 티커는 깊은 이력 백필을 영영 못 받는다 → 항상 표준 깊이로 수집.
            data = yf.download(
                tickers, period=canonical_period("5d"),
                progress=False, auto_adjust=True, threads=False,
            )
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

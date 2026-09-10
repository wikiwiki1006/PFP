"""
services/live_prices.py
───────────────────────
장중 실시간 현재가 조회 + 가격 프레임에 주입.

routers/portfolio.py 에 섞여 있던 시세 수집 헬퍼를 분리했다.
라우터는 HTTP 처리만 담당하고, 수집 규칙은 이 모듈에서만 관리한다.
"""
from __future__ import annotations

import logging
import time as _time
from datetime import datetime, timedelta, timezone

import pandas as pd

logger = logging.getLogger(__name__)

try:
    from zoneinfo import ZoneInfo as _ZI
    _ET_TZ = _ZI("America/New_York")
except Exception:            # pragma: no cover
    try:
        import pytz as _pytz  # type: ignore
        _ET_TZ = _pytz.timezone("America/New_York")
    except Exception:
        _ET_TZ = None

# prices: {ticker: (price, fetched_at)} — 티커별 신선도를 개별 관리
_live_px: dict = {"prices": {}, "next_attempt": 0.0, "day": None}
_LIVE_TTL        = 60    # 이 시간이 지난 가격은 실시간으로 취급하지 않는다
_LIVE_RETRY_OK   = 5     # 정상 수집 후 최소 재시도 간격(초)
_LIVE_RETRY_FAIL = 300   # 실패 시 재시도 간격 — 장애 중 재시도 폭주 방지


def _is_market_open() -> bool:
    """미국 주식 시장 개장 여부. 공휴일까지 반영하는 공용 캘린더에 위임한다.

    (이전 구현은 주말만 확인해 공휴일에 '개장'으로 오판했고, 그 결과
     유령 행 제거 로직이 통째로 비활성화되는 버그가 있었다.)
    """
    from backend.services.market_calendar import is_us_market_open
    return is_us_market_open()



def _get_live_prices(tickers: list[str]) -> dict[str, float]:
    """실시간 현재가 조회. 티커별 개별 타임스탬프로 신선도를 판정한다.

    예전에는 dict 전체를 하나의 전역 ts 로 감쌌다. 그래서
      · 이번 다운로드에 포함되지 않은 티커도 '신선'으로 남아 계속 재발행됐고
      · 실패해도 ts 만 갱신돼 오래된 가격이 is_live=true 로 나갔다.
    또한 '데이터 신선도'와 '재시도 간격'을 분리한다 — 실패 시 ts 를 올리지 않으면
    yfinance 장애 중 모든 요청이 매번 재시도해 오히려 지연·차단이 심해진다.
    """
    import yfinance as yf
    from backend.db.market_cache import _yf_sem

    now = _time.time()
    # ET 날짜가 바뀌면 전량 폐기 (어제 가격이 오늘 실시간으로 주입되는 것 방지)
    today_key = datetime.now(_ET_TZ).date().isoformat() if _ET_TZ else ""
    if _live_px.get("day") != today_key:
        _live_px["day"] = today_key
        _live_px["prices"] = {}
        _live_px["next_attempt"] = 0.0

    cached: dict[str, tuple[float, float]] = _live_px["prices"]

    # 만료됐거나 없는 티커만 재수집
    need = [t for t in tickers
            if t not in cached or now - cached[t][1] >= _LIVE_TTL]
    if not need or now < _live_px.get("next_attempt", 0.0):
        return {t: cached[t][0] for t in tickers
                if t in cached and now - cached[t][1] < _LIVE_TTL}

    try:
        # 최근 30분의 2분봉만 다운로드 (당일 전체 1분봉 대비 데이터량 대폭 감소)
        if _ET_TZ is not None:
            end_dt = datetime.now(_ET_TZ)
        else:
            end_dt = datetime.now(timezone.utc) + timedelta(hours=-4)
        start_dt = end_dt - timedelta(minutes=30)
        # 스케줄러의 배치 다운로드와 동일한 락·설정을 사용해 fd 고갈을 막는다
        with _yf_sem:
            data = yf.download(
                need, start=start_dt, end=end_dt, interval="2m",
                progress=False, auto_adjust=True, threads=False,
            )
        if data is not None and not data.empty:
            close = (
                data["Close"]
                if isinstance(data.columns, pd.MultiIndex)
                else data[["Close"]].rename(columns={"Close": need[0]})
                if len(need) == 1
                else data
            )
            fetched = _time.time()
            for t in need:
                if t in close.columns:
                    col = close[t].dropna()
                    if not col.empty:
                        cached[t] = (float(col.iloc[-1]), fetched)
        _live_px["next_attempt"] = _time.time() + _LIVE_RETRY_OK
    except Exception:
        # 침묵하지 않는다 — 이 상태가 안 보여서 오래된 가격이 실시간으로 표시됐다.
        # print 로는 운영 로그 파이프라인에서 사라져 결국 침묵과 같았다 (§1.3).
        logger.warning("실시간 시세 수집 실패 (%d개) — 직전 캐시 값으로 표시된다",
                       len(need), exc_info=True)
        _live_px["next_attempt"] = _time.time() + _LIVE_RETRY_FAIL

    now2 = _time.time()
    return {t: cached[t][0] for t in tickers
            if t in cached and now2 - cached[t][1] < _LIVE_TTL}



def _ensure_prev_close(df: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    """DB에 전일 데이터가 없어 2행 미만인 종목을 yfinance 5d 조회로 보완."""
    thin = [t for t in tickers if t not in df.columns or df[t].dropna().shape[0] < 2]
    if not thin:
        return df
    try:
        import yfinance as yf
        extra = yf.download(thin, period="5d", progress=False, auto_adjust=True)
        if extra.empty:
            return df
        close = extra["Close"] if isinstance(extra.columns, pd.MultiIndex) else extra
        if isinstance(close, pd.Series):
            close = close.to_frame(name=thin[0])
        df = df.copy()
        for t in thin:
            if t in close.columns:
                col = close[t].dropna()
                if len(col) >= 2:
                    df = df.combine_first(col.rename(t).to_frame())
        df.sort_index(inplace=True)
    except Exception:
        pass
    return df



def _inject_live(df: pd.DataFrame) -> pd.DataFrame:
    """장중에만 ET 날짜 기준 오늘 행에 실시간 현재가 주입.
    DB에 오늘 부분(intraday) 행이 있으면 먼저 제거해 전일 종가가 prev로 오도록 보장."""
    if df.empty or not _is_market_open():
        return df
    live = _get_live_prices(list(df.columns))
    if not live:
        return df
    df = df.copy()

    # 서버 로컬 타임이 아닌 ET 날짜 기준 (KST 서버에서도 미국 거래일과 일치)
    if _ET_TZ is not None:
        today_et = pd.Timestamp(datetime.now(_ET_TZ).date())
    else:
        today_et = pd.Timestamp((datetime.now(timezone.utc) + timedelta(hours=-4)).date())

    # DB/스냅샷에 오늘 intraday 행이 저장돼 있으면 제거 → prev = 전일 종가가 됨
    if today_et in df.index:
        df = df.drop(today_et)

    for t, p in live.items():
        if t in df.columns and p > 0:
            df.loc[today_et, t] = p
    df.sort_index(inplace=True)
    df.ffill(inplace=True)
    return df



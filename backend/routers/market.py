"""
routers/market.py
──────────────────
시장 데이터 API (시세, 섹터, FRED 거시경제, 뉴스)
"""
from __future__ import annotations

from typing import Optional

import logging

from fastapi import APIRouter, Depends, Query

from backend.services.markets import get_market, market_param
from backend.services.market_data import (
    get_close_df,
    get_sector_table,
    get_sector_changes,
    get_fred_macro,
    get_portfolio_news,
    get_earnings_dividends,
    get_market_snapshot,
    )

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/market", tags=["market"])


# 스냅샷을 '지금 값'으로 인정하는 한계. 지수는 장중 계속 움직이므로 짧게 잡는다.
_SNAPSHOT_MAX_AGE_SEC = 15 * 60


def _fresh(updated_at: "str | None") -> bool:
    """스냅샷 한 건이 아직 쓸 만한지."""
    if not updated_at:
        return False
    from datetime import datetime, timezone
    try:
        ts = datetime.fromisoformat(str(updated_at))
    except ValueError:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age = (datetime.now(tz=timezone.utc) - ts).total_seconds()
    return age <= _SNAPSHOT_MAX_AGE_SEC


@router.get("/snapshot")
def market_snapshot():
    """S&P500, NASDAQ, KOSPI, VIX, BTC, Gold 등 주요 지수 현재가 + 등락률."""
    from datetime import datetime
    from backend.db.market_cache import get_snapshot as _db_snap
    from backend.services.market_data import SNAPSHOT_TICKERS

    # ① 실시간 시세를 먼저 본다.
    #
    # 예전에는 market_snapshot 테이블을 그냥 반환했다. 그 테이블은 프로세스 내
    # 스케줄러가 60초마다 채우는데 서버리스에서는 그게 꺼져 있어(인스턴스가 0 으로
    # 내려가 돌지 못한다) 며칠 묵은 값이 상단에 계속 떠 있었다 — 한국장이 열려
    # 있는데 KOSPI 가 5일 전 수치였다.
    #
    # 폴백이던 일봉 계산도 답이 아니다. 전일 종가라 장중에는 틀리고 등락률이
    # 0% 로 나와 '보합'처럼 보인다. live_quotes 는 종목 상세가 이미 쓰는 경로로,
    # 지수도 웹소켓/폴링으로 받아 온다.
    try:
        from backend.services.live_quotes import get_quotes
        live = get_quotes(list(SNAPSHOT_TICKERS), max_age=60,
                          backfill=False, interval="5m")
        prices = {t: v for t, v in (live or {}).items()
                  if isinstance(v, dict) and v.get("price") is not None}
        if prices:
            return {"prices": prices, "timestamp": datetime.now().isoformat()}
    except Exception as e:
        logger.warning(f"실시간 스냅샷 실패, DB 로 폴백: {e}")

    # ② 실시간을 못 받으면 DB 스냅샷 — 단, 신선한 것만.
    snap = _db_snap()
    if snap:
        stale_ok = {t: v for t, v in snap.items()
                    if t in SNAPSHOT_TICKERS and _fresh(v.get("updated_at"))}
        if stale_ok:
            return {"prices": stale_ok, "timestamp": datetime.now().isoformat()}

    # ③ 마지막 수단: 일봉 기반 계산 (장중이면 전일 종가)
    close_df = get_close_df([], period="5d", ttl=60)
    return get_market_snapshot(close_df)


@router.get("/prices")
def get_prices(
    tickers: str = Query(..., description="콤마 구분 티커 목록. 예: NVDA,AAPL,MSFT"),
    period: str = Query(default="1y", description="yfinance period. 예: 5d, 1mo, 1y, 5y"),
):
    """
    지정 티커들의 종가 시계열 반환.
    최근 종가 + 전일 대비 등락률도 함께 포함.
    """
    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    if not ticker_list:
        return []

    close_df = get_close_df(ticker_list, period=period, ttl=60)
    if close_df.empty or len(close_df) < 2:
        return []

    result = []
    for ticker in ticker_list:
        if ticker not in close_df.columns:
            continue
        series = close_df[ticker].dropna()
        if len(series) < 2:
            continue
        cur  = float(series.iloc[-1])
        prev = float(series.iloc[-2])
        chg  = (cur / prev - 1) * 100 if prev else 0.0
        result.append({
            "ticker":   ticker,
            "current":  round(cur, 2),
            "prev":     round(prev, 2),
            "chg_pct":  round(chg, 4),
            "series": [
                {"date": d.strftime("%Y-%m-%d"), "close": round(float(v), 2)}
                for d, v in series.items()
            ],
        })
    return result


@router.get("/sectors")
def get_sectors(market: str = Depends(market_param)):
    """섹터 ETF 등락률 테이블. 미국은 SPDR 11 섹터, 한국은 KODEX 업종."""
    return get_sector_table(market)


@router.get("/sectors/changes")
def sectors_changes(market: str = Depends(market_param)):
    """{ XLK: 1.23, XLF: -0.45, ... } 등락률 맵."""
    return get_sector_changes(market)


@router.get("/macro")
def macro_data(market: str = Depends(market_param)):
    """거시경제 지표. 미국은 FRED, 한국은 한국은행 ECOS + FRED 조합."""
    if market == "KR":
        from backend.services.korea_macro import get_korea_macro
        return get_korea_macro()
    return get_fred_macro()



@router.get("/news")
def portfolio_news(
    tickers: str = Query(..., description="콤마 구분 티커. 예: NVDA,AAPL"),
    max_per: int = Query(default=2, ge=1, le=5),
):
    """보유 종목 + MACRO 뉴스 최신순 정렬."""
    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    return get_portfolio_news(ticker_list, max_per=max_per)


@router.get("/earnings")
def earnings_dividends(
    tickers: str = Query(..., description="콤마 구분 티커"),
):
    """종목별 다음 실적일 + 최근 배당일 + 배당수익률."""
    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    return get_earnings_dividends(ticker_list)


@router.get("/ticker-names")
def ticker_names(tickers: str = "", market: str = Depends(market_param)):
    """티커 → 표시용 이름 사전.

    한국 종목은 코드(005930.KS)만으로 어느 회사인지 알 수 없어 화면 곳곳에서
    이름이 필요하다. 화면마다 종목 수만큼 왕복하면 목록이 느려지므로 한 번에
    받아 프론트에서 캐시한다.

    tickers 를 주면 그것만, 비우면 그 시장의 전체 사전을 돌려준다
    (한국은 스캔 유니버스 350종목 — 응답이 작아 통째로 보내도 된다).
    """
    if market != "KR":
        # 미국은 티커가 곧 이름 역할을 해서 이 사전이 필요 없다.
        return {}
    try:
        from backend.services.korea_universe import name_map
        names = name_map()
    except Exception as e:
        logger.warning(f"종목명 사전 조회 실패: {e}")
        return {}

    wanted = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    if not wanted:
        return names
    return {t: names[t] for t in wanted if t in names}

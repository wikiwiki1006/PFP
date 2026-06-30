"""
routers/market.py
──────────────────
시장 데이터 API (시세, 섹터, FRED 거시경제, 뉴스)
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from backend.services.market_data import (
    get_close_df,
    get_sector_table,
    get_sector_changes,
    get_fred_macro,
    get_doom_radar,
    get_portfolio_news,
    get_earnings_dividends,
    get_market_snapshot,
    ALWAYS_FETCH,
)

router = APIRouter(prefix="/api/market", tags=["market"])


@router.get("/snapshot")
def market_snapshot():
    """S&P500, NASDAQ, KOSPI, VIX, BTC, Gold 등 주요 지수 현재가 + 등락률."""
    from datetime import datetime
    from backend.db.market_cache import get_snapshot as _db_snap
    from backend.services.market_data import SNAPSHOT_TICKERS

    snap = _db_snap()  # 스케줄러가 60초마다 올바르게 계산한 값
    if snap:
        prices = {t: v for t, v in snap.items() if t in SNAPSHOT_TICKERS}
        if prices:
            return {"prices": prices, "timestamp": datetime.now().isoformat()}

    # DB 스냅샷 없으면 폴백 계산
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
def get_sectors():
    """11개 GICS 섹터 ETF 등락률 테이블."""
    return get_sector_table()


@router.get("/sectors/changes")
def sectors_changes():
    """{ XLK: 1.23, XLF: -0.45, ... } 등락률 맵."""
    return get_sector_changes()


@router.get("/macro")
def macro_data():
    """FRED 거시경제 지표 (Fed Rate, 실업률, 10Y/2Y 금리)."""
    return get_fred_macro()


@router.get("/doom-radar")
def doom_radar():
    """
    매크로 저승사자 레이더 — 장단기 금리차 + 하이일드 스프레드 위기 감지.
    is_doom=true 이면 모든 매수 신호 주의.
    """
    return get_doom_radar()


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


_TICKER_LABELS: dict[str, str] = {
    "^GSPC":    "S&P 500",
    "^IXIC":    "NASDAQ",
    "^KS11":    "KOSPI",
    "^KQ11":    "KOSDAQ",
    "XLK":      "Technology",
    "XLF":      "Financials",
    "XLC":      "Communication",
    "XLY":      "Cons. Disc",
    "XLV":      "Healthcare",
    "XLI":      "Industrials",
    "XLP":      "Cons. Staples",
    "XLE":      "Energy",
    "XLU":      "Utilities",
    "XLB":      "Materials",
    "XLRE":     "Real Estate",
    "^VIX":     "VIX",
    "^TNX":     "10Y Bond",
    "^IRX":     "3M Bond",
    "GC=F":     "Gold",
    "BTC-USD":  "Bitcoin",
    "CL=F":     "Crude Oil",
    "USDKRW=X": "USD/KRW",
    "SPY":      "S&P ETF",
    "QQQ":      "NASDAQ ETF",
}


@router.get("/correlation")
def correlation_matrix(
    tickers: Optional[str] = Query(default=None, description="콤마 구분 티커 (없으면 시장 지수 기본값)"),
    period: str = Query(default="1y"),
):
    """지정 자산들의 수익률 상관관계 행렬."""
    DEFAULT_TICKERS = ["^GSPC", "^IXIC", "XLK", "XLF", "XLE", "^VIX", "^TNX", "GC=F", "BTC-USD", "CL=F"]
    ticker_list = (
        [t.strip().upper() for t in tickers.split(",") if t.strip()]
        if tickers else DEFAULT_TICKERS
    )

    close_df = get_close_df(ticker_list, period=period, ttl=300)
    avail = [t for t in ticker_list if t in close_df.columns]
    if len(avail) < 2:
        return {"error": "상관관계 계산에 필요한 데이터 부족"}

    corr = close_df[avail].pct_change().corr()
    return {
        "tickers": avail,
        "labels":  [_TICKER_LABELS.get(t, t) for t in avail],
        "matrix":  [[round(corr.iloc[i, j], 4) for j in range(len(avail))] for i in range(len(avail))],
    }

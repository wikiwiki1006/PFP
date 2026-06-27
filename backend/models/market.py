from __future__ import annotations
from typing import Optional
from pydantic import BaseModel


class PriceBar(BaseModel):
    date: str
    close: float


class TickerPrice(BaseModel):
    ticker: str
    current: float
    prev_close: float
    chg_pct: float
    series: list[PriceBar] = []


class MacroData(BaseModel):
    fed_rate: float
    unemployment: float
    y10: float
    y2: float
    spread_10_2: float
    usd_krw: float
    source: str


class SectorChange(BaseModel):
    sector: str
    ticker: str
    price: float
    chg_pct: float


class NewsItem(BaseModel):
    ticker: str
    title: str
    time: str
    is_macro: bool = False


class EarningsDividend(BaseModel):
    ticker: str
    earn_date: str
    div_date: str
    div_yield: str


class DoomRadar(BaseModel):
    is_doom: bool
    rate_spread: float
    hy_spread: float
    reason: str


class MarketSnapshot(BaseModel):
    sp500: Optional[float] = None
    sp500_chg: Optional[float] = None
    nasdaq: Optional[float] = None
    nasdaq_chg: Optional[float] = None
    kospi: Optional[float] = None
    kospi_chg: Optional[float] = None
    vix: Optional[float] = None
    btc: Optional[float] = None
    gold: Optional[float] = None
    wti: Optional[float] = None
    usd_krw: Optional[float] = None

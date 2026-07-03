from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field


class HoldingItem(BaseModel):
    q: float = Field(..., description="보유 수량")
    avg: float = Field(..., description="평균 매입단가")
    sector: str = Field(default="기타")
    div: str = Field(default="N/A", description="최근 배당일")
    prev_eps: float = Field(default=0.0)
    cur_eps: float = Field(default=0.0)
    earn_date: str = Field(default="N/A", description="다음 실적 발표일")
    date: Optional[str] = Field(default=None, description="CASH 최초 입금 날짜 (YYYY-MM-DD)")


class Holdings(BaseModel):
    holdings: dict[str, HoldingItem]

    @classmethod
    def from_raw(cls, raw: dict) -> "Holdings":
        data = raw.get("my_holdings", raw)
        return cls(holdings={k: HoldingItem(**v) for k, v in data.items()})

    def to_raw(self) -> dict:
        return {"my_holdings": {k: v.model_dump() for k, v in self.holdings.items()}}


class TradeType(str):
    ADD = "ADD"
    SOLD = "SOLD"
    UPDATE = "UPDATE"


class TradeRecord(BaseModel):
    date: str = Field(..., description="거래일 (YYYY-MM-DD)")
    ticker: str
    type: str = Field(..., description="ADD | SOLD | UPDATE")
    q: float = Field(..., description="수량")
    price: Optional[float] = None
    memo: Optional[str] = None


class AddTradeRequest(BaseModel):
    ticker: str
    type: str
    q: float
    price: Optional[float] = None
    memo: Optional[str] = None
    date: Optional[str] = None   # YYYY-MM-DD, 없으면 오늘


class UpdateHoldingRequest(BaseModel):
    q: float
    avg: float
    sector: Optional[str] = None
    date: Optional[str] = None   # CASH DEPOSIT/WITHDRAW 날짜 (YYYY-MM-DD)


class PortfolioMetrics(BaseModel):
    total_equity: float
    total_cost: float
    total_return_pct: float
    today_change_val: float
    today_change_pct: float
    portfolio_beta: float
    vix: float
    perf_1w: float
    perf_1m: float
    alpha_vs_sp500: Optional[float] = None


class EquityCurvePoint(BaseModel):
    date: str
    portfolio: float
    sp500: Optional[float] = None
    nasdaq: Optional[float] = None


class HoldingDetail(BaseModel):
    ticker: str
    avg: float
    shares: float
    price: float
    chg_pct: float
    pnl_pct: float
    value: float
    weight_pct: float

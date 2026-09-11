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


# Holdings · TradeType · TradeRecord 도 지웠다. 셋 다 미사용이다.
#
# Holdings.from_raw 는 `raw.get("my_holdings", raw)` 로 pfp/data/holdings.json
# 을 읽던 시절의 유물이다. 그 파일 경로는 99181ab 에서 걷어냈다.


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


# PortfolioMetrics · EquityCurvePoint · HoldingDetail 을 지웠다.
#
# 셋 다 어디서도 import 되지 않았다. 라우터는 이 파일에서 HoldingItem ·
# AddTradeRequest · UpdateHoldingRequest · PortfolioSetupRequest 넷만 쓴다.
#
# 안 쓰이는 것보다 **틀린 채로 안 쓰이는 것**이 문제였다. PortfolioMetrics 는
# total_return_pct·today_change_pct·vix 를 `float` 로 선언하고 있었는데, 셋 다
# 지금은 None 이 올 수 있다. response_model 로 붙어 있었다면 그 변경들이 전부
# 500 이 됐을 것이다 — 안 쓰여서 안 터졌고, 안 쓰여서 아무도 갱신하지 않았다.
# 죽은 페이지는 최소한 build 를 깨서 존재를 알렸는데 이건 그것도 안 한다.
#
# 응답 계약을 강제하고 싶으면 response_model 을 실제로 붙여야 한다. 그건
# 별도 작업이다 — 지금은 nullable 전환이 진행 중이라 시점이 나쁘다.


class SetupHolding(BaseModel):
    """최초 등록 시 입력하는 보유 종목 한 줄."""
    ticker: str
    q:      float
    price:  float          # 매수 단가 (USD)
    date:   str            # 매수일 YYYY-MM-DD


class PortfolioSetupRequest(BaseModel):
    """포트폴리오 최초 등록 / 새로 등록.

    replace=True 면 기존 보유·거래 이력을 모두 지우고 새로 만든다.
    """
    holdings: list[SetupHolding] = []
    cash:     float = 0.0        # 현재 현금 잔고 (USD)
    replace:  bool = False

from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field


class MacroAnalysisRequest(BaseModel):
    event: str = Field(..., description="분석할 거시경제 이벤트")
    # 기본은 저비용 모델. 심층 분석(sonnet)은 호출부가 명시적으로 지정한다 —
    # 기본값을 비싼 쪽에 두면 실수로 비용이 몇 배가 된다.
    model: str = Field(default="claude-haiku-4-5", description="사용할 Claude 모델")
    mode: str = Field(default="fast", description="fast(3) | standard(5) | full(9)")
    provider: str = Field(default="claude", description="claude | gpt")
    portfolio: Optional[dict] = None


class AgentResult(BaseModel):
    id: int
    label: str
    text: str
    ok: bool


class VerdictCard(BaseModel):
    title: str
    icon: str
    color: str
    headline: str
    summary: str
    details: str


class MacroAnalysisResponse(BaseModel):
    event: str
    agents: list[AgentResult]
    verdict_cards: Optional[list[VerdictCard]] = None
    portfolio_actions: Optional[list[dict]] = None


class PortfolioAction(BaseModel):
    ticker: str
    action: str
    reason: str
    urgency: str


class SignalItem(BaseModel):
    ticker: str
    method: str
    score: float
    entry: float
    target: float
    stop: float
    upside: Optional[float] = None
    downside: Optional[float] = None
    reason: str


class SignalScanResponse(BaseModel):
    long_picks: list[SignalItem]
    short_picks: list[SignalItem]
    scanned: int
    doom: dict

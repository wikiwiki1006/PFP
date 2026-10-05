from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field


class MacroAnalysisRequest(BaseModel):
    event: str = Field(..., description="분석할 거시경제 이벤트")
    # 기본은 저비용 모델. 심층 분석(sonnet)은 호출부가 명시적으로 지정한다 —
    # 기본값을 비싼 쪽에 두면 실수로 비용이 몇 배가 된다.
    model: str = Field(default="claude-haiku-4-5", description="사용할 Claude 모델")
    mode: str = Field(default="fast", description="fast(3) | standard(5) | full(9)")
    # `provider` 를 뺐다. GPT 경로(call_gpt·_GPT_URL·GPT_API_KEY)가 전부
    # 제거돼 선택지가 하나뿐이었고, 남겨 두면 "고를 수 있다" 는 계약이
    # 화면·API 에 계속 남는다.
    #
    # 옛 클라이언트 호환은 확인했다. 이 모델은 extra 를 지정하지 않아
    # pydantic v2 기본값(`ignore`)을 따르므로, `provider` 를 보내던 요청은
    # 422 가 아니라 **조용히 무시되고 통과**한다.
    # 분석 대상 종목. **보유 종목이 아니라 조회자가 직접 입력한 목록이다.**
    # 예전에는 `portfolio: Optional[dict]` 였고 화면이 holdings 를 통째로
    # 보내 수량·평균단가까지 프롬프트에 실렸다 — 재산상황이 담긴 개별
    # 자문이 되는 경로라 티커 목록으로 좁혔다.
    tickers: Optional[list[str]] = None


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
    ticker_impacts: Optional[list[dict]] = None


class TickerImpact(BaseModel):
    ticker: str
    impact: str      # 긍정 | 부정 | 중립 — 이벤트가 그 기업에 주는 방향
    reason: str
    horizon: str     # 단기(1개월) | 중기(3개월) | 장기(1년)


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

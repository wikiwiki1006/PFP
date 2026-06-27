"""
routers/macro.py
─────────────────
9-에이전트 거시경제 분석 + 데일리 브리프 API
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional

from backend.models.macro import MacroAnalysisRequest, MacroAnalysisResponse
from backend.services.ai_analysis import (
    run_macro_agents,
    parse_verdict_cards,
    parse_portfolio_actions,
    get_ai_analyst_feedback,
    generate_daily_brief,
    ANALYSIS_MODES,
    MODEL_OPTIONS,
)
from backend.services.market_data import (
    get_close_df,
    get_sector_changes,
    get_fred_macro,
    get_portfolio_news,
    GICS_SECTOR_ETFS,
)
from backend.services.portfolio_calculator import calculate_metrics, build_equity_curve

router = APIRouter(prefix="/api/macro", tags=["macro"])

_DATA_DIR = Path(__file__).parent.parent.parent / "pfp" / "data"
_DB_FILE  = _DATA_DIR / "holdings.json"
_LOG_FILE = _DATA_DIR / "trade_log.json"


def _load_holdings() -> dict:
    if not _DB_FILE.exists():
        return {}
    with open(_DB_FILE) as f:
        raw = json.load(f)
    return raw.get("my_holdings", raw)


def _load_trade_log() -> list:
    if not _LOG_FILE.exists():
        return []
    with open(_LOG_FILE) as f:
        return json.load(f)


# ── 거시경제 분석 ─────────────────────────────────────────────────────────────

@router.post("/analyze")
def analyze_macro(req: MacroAnalysisRequest):
    """
    9-에이전트 거시경제 이벤트 분석.

    mode: "fast" (3 agents) | "standard" (5) | "full" (9)
    model_key: "sonnet" | "haiku"
    """
    if not req.event.strip():
        raise HTTPException(status_code=400, detail="이벤트를 입력하세요.")

    portfolio = req.portfolio or _load_holdings()

    agent_results = run_macro_agents(
        event=req.event,
        portfolio=portfolio,
        model_key="sonnet" if "sonnet" in req.model else "haiku",
        mode=req.mode,
    )

    # Final Verdict (id=9) JSON 파싱
    verdict_cards = None
    portfolio_actions = None
    for ag in agent_results:
        if ag["id"] == 9:
            verdict_cards = parse_verdict_cards(ag["text"])
        if ag["id"] == 8:
            portfolio_actions = parse_portfolio_actions(ag["text"])

    return {
        "event":              req.event,
        "agents":             agent_results,
        "verdict_cards":      verdict_cards,
        "portfolio_actions":  portfolio_actions,
    }


@router.get("/modes")
def list_modes():
    """사용 가능한 분석 모드 + 모델 목록."""
    return {
        "modes":  {k: v for k, v in ANALYSIS_MODES.items()},
        "models": list(MODEL_OPTIONS.keys()),
    }


# ── AI Analyst 피드백 ─────────────────────────────────────────────────────────

class AnalystFeedbackRequest(BaseModel):
    vix: float
    portfolio_beta: float
    today_chg_pct: float
    sector_summary: Optional[str] = ""


@router.post("/analyst-feedback")
def analyst_feedback(req: AnalystFeedbackRequest):
    """Claude Haiku 기반 1~2문장 실시간 시장 피드백."""
    text = get_ai_analyst_feedback(
        vix=req.vix,
        portfolio_beta=req.portfolio_beta,
        today_chg_pct=req.today_chg_pct,
        sector_summary=req.sector_summary or "",
    )
    return {"feedback": text}


@router.get("/analyst-feedback/auto")
def analyst_feedback_auto():
    """포트폴리오 + 시장 데이터를 자동으로 수집해 AI 피드백 생성."""
    holdings  = _load_holdings()
    trade_log = _load_trade_log()

    if not holdings:
        raise HTTPException(status_code=400, detail="보유 종목 없음")

    tickers  = [t for t in holdings if t != "CASH"]
    close_df = get_close_df(tickers, period="5d", ttl=60)

    equity_curve = build_equity_curve(holdings, trade_log, close_df)
    metrics      = calculate_metrics(holdings, close_df, equity_curve)

    sector_chgs = get_sector_changes()
    label_map   = {etf: label for label, etf in GICS_SECTOR_ETFS}
    sector_list = sorted(
        [(label_map.get(etf, etf), chg) for etf, chg in sector_chgs.items()],
        key=lambda x: x[1], reverse=True,
    )
    sector_summary = ", ".join(f"{s}({c:+.1f}%)" for s, c in sector_list[:2]) if sector_list else "데이터 없음"

    text = get_ai_analyst_feedback(
        vix=metrics.get("vix", 18.0),
        portfolio_beta=metrics.get("portfolio_beta", 1.0),
        today_chg_pct=metrics.get("today_change_pct", 0.0),
        sector_summary=sector_summary,
    )
    return {"feedback": text, "metrics_snapshot": metrics}


# ── 데일리 브리프 ─────────────────────────────────────────────────────────────

@router.post("/daily-brief")
def daily_brief(portfolio: Optional[dict] = None):
    """오늘의 포트폴리오 브리프 마크다운 생성 (Claude Sonnet)."""
    holdings  = portfolio or _load_holdings()
    trade_log = _load_trade_log()

    if not holdings:
        raise HTTPException(status_code=400, detail="보유 종목 없음")

    tickers  = [t for t in holdings if t != "CASH"]
    close_df = get_close_df(tickers, period="5d", ttl=60)

    # 가격 데이터 수집
    price_data = {}
    if not close_df.empty and len(close_df) >= 2:
        cur, prev = close_df.iloc[-1], close_df.iloc[-2]
        for t in tickers:
            if t not in close_df.columns:
                continue
            p = float(cur.get(t, 0))
            pp = float(prev.get(t, p))
            price_data[t] = {
                "price":   round(p, 2),
                "chg_pct": round((p / pp - 1) * 100 if pp else 0, 4),
                "pnl_pct": round((p / holdings[t]["avg"] - 1) * 100 if holdings[t]["avg"] else 0, 4),
            }

    macro_data = get_fred_macro()
    news_items = get_portfolio_news(tickers, max_per=2)

    md = generate_daily_brief(holdings, price_data, macro_data, news_items)
    return {"markdown": md}

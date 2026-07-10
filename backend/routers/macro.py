"""
routers/macro.py
─────────────────
9-에이전트 거시경제 분석 + 데일리 브리프 API
"""
from __future__ import annotations

import json
from pathlib import Path

from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

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

class LiveMetrics(BaseModel):
    vix: Optional[float] = None
    portfolio_beta: Optional[float] = None
    today_chg_pct: Optional[float] = None


@router.post("/analyst-feedback/auto")
def analyst_feedback_auto(live: LiveMetrics = LiveMetrics()):
    """포트폴리오 섹터 기반 AI 피드백 생성."""
    from backend.db.portfolio_repo import (
        get_holdings as _db_get_holdings,
        get_trade_log as _db_get_trade_log,
    )
    from backend.db import is_available as _db_ok

    if _db_ok():
        holdings  = _db_get_holdings("default")
        trade_log = _db_get_trade_log("default")
    else:
        holdings  = _load_holdings()
        trade_log = _load_trade_log()

    if not holdings:
        raise HTTPException(status_code=400, detail="보유 종목 없음")

    tickers  = [t for t in holdings if t != "CASH"]
    close_df = get_close_df(tickers, period="5d", ttl=60)

    equity_curve = build_equity_curve(holdings, trade_log, close_df)
    metrics      = calculate_metrics(holdings, close_df, equity_curve)

    # 포트폴리오 보유 종목 섹터 비중 계산
    sector_weights: dict[str, float] = {}
    total_cost = sum(
        h.get("q", 0) * h.get("avg", 0)
        for t, h in holdings.items() if t != "CASH"
    )
    if total_cost > 0:
        for t, h in holdings.items():
            if t == "CASH":
                continue
            sec = h.get("sector") or "Other"
            cost = h.get("q", 0) * h.get("avg", 0)
            sector_weights[sec] = sector_weights.get(sec, 0) + cost / total_cost

    # 보유 종목 섹터별 ETF 1일 변동률 조회
    sector_chgs = get_sector_changes()
    _etf_to_label = {etf: label for label, etf in GICS_SECTOR_ETFS}
    _label_to_etf = {label: etf for label, etf in GICS_SECTOR_ETFS}

    # 보유 섹터를 비중 내림차순 정렬
    held_sectors = sorted(sector_weights.items(), key=lambda x: x[1], reverse=True)

    # 섹터별 비중 + 오늘 변동률 조합
    sector_lines = []
    for sec, wt in held_sectors[:4]:
        # GICS label → ETF lookup (대소문자 무시)
        etf_chg = None
        for label, etf in GICS_SECTOR_ETFS:
            if label.lower() in sec.lower() or sec.lower() in label.lower():
                etf_chg = sector_chgs.get(etf)
                break
        chg_str = f"{etf_chg:+.1f}%" if etf_chg is not None else "N/A"
        sector_lines.append(f"{sec}({wt*100:.0f}%, 오늘{chg_str})")

    portfolio_sector_summary = " / ".join(sector_lines) if sector_lines else "섹터 데이터 없음"

    text = get_ai_analyst_feedback(
        vix=live.vix if live.vix is not None else metrics.get("vix", 20.0),
        portfolio_beta=live.portfolio_beta if live.portfolio_beta is not None else metrics.get("portfolio_beta", 1.0),
        today_chg_pct=live.today_chg_pct if live.today_chg_pct is not None else metrics.get("today_change_pct", 0.0),
        sector_summary=portfolio_sector_summary,
        is_portfolio_sectors=True,
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

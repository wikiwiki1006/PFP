"""
routers/portfolio.py
─────────────────────
포트폴리오 CRUD + 에쿼티 커브 + 핵심 지표 API
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.models.portfolio import (
    Holdings, HoldingItem, TradeRecord,
    AddTradeRequest, UpdateHoldingRequest,
)
from backend.services.market_data import get_close_df
from backend.services.portfolio_calculator import (
    build_equity_curve,
    equity_curve_to_records,
    calculate_metrics,
    get_holdings_detail,
)

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])

_DATA_DIR = Path(__file__).parent.parent.parent / "pfp" / "data"
_DB_FILE  = _DATA_DIR / "holdings.json"
_LOG_FILE = _DATA_DIR / "trade_log.json"


# ── 헬퍼: JSON I/O ─────────────────────────────────────────────────────────────

def _load_holdings() -> dict:
    if not _DB_FILE.exists():
        return {}
    with open(_DB_FILE) as f:
        raw = json.load(f)
    return raw.get("my_holdings", raw)


def _save_holdings(holdings: dict):
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(_DB_FILE, "w") as f:
        json.dump({"my_holdings": holdings}, f, indent=4)


def _load_trade_log() -> list:
    if not _LOG_FILE.exists():
        return []
    with open(_LOG_FILE) as f:
        return json.load(f)


def _save_trade_log(log: list):
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=4, ensure_ascii=False)


# ── 엔드포인트 ─────────────────────────────────────────────────────────────────

@router.get("/holdings")
def get_holdings():
    """현재 보유 종목 전체 반환."""
    return _load_holdings()


@router.put("/holdings/{ticker}")
def update_holding(ticker: str, body: UpdateHoldingRequest):
    """특정 종목 수량/평균단가 업데이트."""
    holdings = _load_holdings()
    ticker = ticker.upper()

    if ticker not in holdings:
        raise HTTPException(status_code=404, detail=f"{ticker} 미보유")

    holdings[ticker]["q"]   = body.q
    holdings[ticker]["avg"] = body.avg
    if body.sector:
        holdings[ticker]["sector"] = body.sector

    _save_holdings(holdings)
    return {"ok": True, "ticker": ticker}


@router.post("/holdings/{ticker}")
def add_holding(ticker: str, item: HoldingItem):
    """신규 종목 추가."""
    holdings = _load_holdings()
    ticker = ticker.upper()

    if ticker in holdings:
        raise HTTPException(status_code=409, detail=f"{ticker} 이미 존재. PUT으로 수정하세요.")

    holdings[ticker] = item.model_dump()
    _save_holdings(holdings)
    return {"ok": True, "ticker": ticker}


@router.delete("/holdings/{ticker}")
def delete_holding(ticker: str):
    """종목 삭제."""
    holdings = _load_holdings()
    ticker = ticker.upper()

    if ticker not in holdings:
        raise HTTPException(status_code=404, detail=f"{ticker} 미보유")

    del holdings[ticker]
    _save_holdings(holdings)
    return {"ok": True, "ticker": ticker}


# ── 거래 이력 ─────────────────────────────────────────────────────────────────

@router.get("/trades")
def get_trades():
    return _load_trade_log()


@router.post("/trades")
def add_trade(body: AddTradeRequest):
    """거래 이력 추가 + holdings 자동 업데이트. BUY→ADD, SELL→SOLD 자동 변환."""
    holdings  = _load_holdings()
    trade_log = _load_trade_log()
    ticker = body.ticker.upper()

    _type_map = {"BUY": "ADD", "SELL": "SOLD"}
    trade_type = _type_map.get(body.type.upper(), body.type.upper())

    if ticker not in holdings and trade_type == "ADD":
        holdings[ticker] = {"q": 0.0, "avg": float(body.price or 0), "sector": "Other"}

    record = {
        "date":   datetime.now().strftime("%Y-%m-%d"),
        "ticker": ticker,
        "type":   trade_type,
        "q":      body.q,
        "price":  body.price,
        "memo":   body.memo,
    }
    trade_log.append(record)

    if ticker in holdings:
        q = float(body.q)
        price = float(body.price or 0)
        if trade_type == "ADD":
            prev_q = holdings[ticker]["q"]
            prev_avg = holdings[ticker].get("avg", price)
            new_q = prev_q + q
            holdings[ticker]["avg"] = round((prev_avg * prev_q + price * q) / new_q, 4) if new_q > 0 else price
            holdings[ticker]["q"] = round(new_q, 6)
        elif trade_type == "SOLD":
            holdings[ticker]["q"] = max(0.0, round(holdings[ticker]["q"] - q, 6))
        elif trade_type == "UPDATE":
            holdings[ticker]["q"] = q

    _save_holdings(holdings)
    _save_trade_log(trade_log)
    return {"ok": True, "record": record}


# ── 분석 데이터 ────────────────────────────────────────────────────────────────

@router.get("/metrics")
def get_metrics():
    """포트폴리오 핵심 지표 (총 평가액, P&L, 베타, VIX 등)."""
    holdings  = _load_holdings()
    trade_log = _load_trade_log()

    if not holdings:
        raise HTTPException(status_code=400, detail="보유 종목 없음")

    tickers = [t for t in holdings if t != "CASH"]
    close_df = get_close_df(tickers, period="1y", ttl=300)

    equity_curve = build_equity_curve(holdings, trade_log, close_df)
    return calculate_metrics(holdings, close_df, equity_curve)


@router.get("/equity-curve")
def get_equity_curve(benchmark: Optional[str] = "sp500"):
    """
    에쿼티 커브 시계열 반환.
    benchmark: "sp500" | "nasdaq" | "none"
    """
    holdings  = _load_holdings()
    trade_log = _load_trade_log()

    if not holdings:
        raise HTTPException(status_code=400, detail="보유 종목 없음")

    tickers = [t for t in holdings if t != "CASH"]
    close_df = get_close_df(tickers, period="2y", ttl=300)

    equity_curve = build_equity_curve(holdings, trade_log, close_df)
    records = equity_curve_to_records(equity_curve, close_df)
    return records


@router.get("/holdings-detail")
def get_holdings_detail_endpoint():
    """종목별 현재가/수익률/비중 상세 테이블."""
    holdings = _load_holdings()
    if not holdings:
        return []

    tickers = [t for t in holdings if t != "CASH"]
    close_df = get_close_df(tickers, period="5d", ttl=60)
    return get_holdings_detail(holdings, close_df)


@router.get("/sector-weights")
def get_sector_weights():
    """섹터별 평가액 비중을 {sector: weight_fraction} 형태로 반환."""
    holdings = _load_holdings()
    if not holdings:
        return {}

    tickers = [t for t in holdings if t != "CASH"]
    close_df = get_close_df(tickers, period="5d", ttl=60) if tickers else None

    rows: dict[str, float] = {}
    for t, info in holdings.items():
        price = 1.0 if t == "CASH" else (
            float(close_df.iloc[-1].get(t, 0)) if close_df is not None and t in close_df.columns else 0.0
        )
        val = price * info["q"]
        sector = info.get("sector", "Other")
        rows[sector] = rows.get(sector, 0) + val

    total = sum(rows.values())
    if total <= 0:
        return {}
    return {s: round(v / total, 4) for s, v in sorted(rows.items(), key=lambda x: -x[1])}

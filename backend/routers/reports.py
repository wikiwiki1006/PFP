"""
routers/reports.py
───────────────────
데일리 브리프 · LENS 종목 레포트 · 산업 레포트 API.
레포트를 DB(reports 테이블)에 저장하고 파일도 함께 유지.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel

from backend.db.portfolio_repo import get_holdings
from backend.db.reports_repo import (
    save_report, list_reports, get_report_content,
)
from backend.services.report_writer import INDUSTRIES, write_equity_report, write_industry_report
from backend.services.daily_report import generate_daily_report
from backend.services.telegram_sender import send_file_bytes, send_message

router = APIRouter(prefix="/api/reports", tags=["reports"])


def _uid(x_user_id: Optional[str]) -> str:
    return (x_user_id or "default").strip() or "default"


# ── 데일리 브리프 ─────────────────────────────────────────────────────────────────

@router.post("/daily-brief")
async def daily_brief(x_user_id: Optional[str] = Header(default=None)):
    uid = _uid(x_user_id)
    holdings = get_holdings(uid)
    if not holdings:
        raise HTTPException(status_code=400, detail="보유 종목 없음")

    logs: list[str] = []
    try:
        report, price_data = generate_daily_report(holdings, log=logs.append)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    date_str = datetime.now().strftime("%Y%m%d_%H%M")
    filename = f"daily_brief_{date_str}.md"
    save_report(filename, report, report_type="daily_brief",
                metadata={"user_id": uid}, user_id=uid)

    return {
        "report":     report,
        "price_data": {k: v for k, v in price_data.items() if not k.startswith("__")},
        "file_path":  filename,
        "logs":       logs,
    }


@router.get("/daily-brief/history")
def daily_brief_history(x_user_id: Optional[str] = Header(default=None)):
    uid = _uid(x_user_id)
    rows = list_reports(uid, report_type="daily_brief", limit=20)
    return [{"name": r["name"], "path": r["name"], "size": r.get("size", 0)} for r in rows]


@router.get("/daily-brief/file/{filename}")
def get_daily_brief_file(filename: str):
    if not filename.startswith("daily_brief_"):
        raise HTTPException(status_code=400, detail="잘못된 파일명")
    content = get_report_content(filename)
    if content is None:
        raise HTTPException(status_code=404, detail="파일 없음")
    return {"content": content, "name": filename}


# ── LENS 종목 레포트 ──────────────────────────────────────────────────────────────

class EquityReportRequest(BaseModel):
    ticker: str
    company_name: str
    send_telegram: bool = False


@router.post("/equity-research")
async def equity_research(
    req: EquityReportRequest,
    x_user_id: Optional[str] = Header(default=None),
):
    uid = _uid(x_user_id)
    ticker = req.ticker.upper()
    try:
        sections = write_equity_report(ticker, req.company_name)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    date_str = datetime.now().strftime("%Y%m%d_%H%M")
    filename = f"lens_{ticker}_{date_str}.md"
    raw = sections.get("_raw", "")
    save_report(filename, raw, report_type="equity_research",
                metadata={"ticker": ticker, "company": req.company_name},
                user_id=uid)

    telegram_sent = False
    if req.send_telegram and raw:
        telegram_sent = send_file_bytes(
            raw.encode("utf-8"), f"LENS_{ticker}_{date_str}.md",
            caption=f"📊 LENS CAPITAL RESEARCH\n\n{ticker} — {req.company_name}\n레포트가 완성되었습니다.",
        )

    return {
        "ticker":        ticker,
        "company_name":  req.company_name,
        "sections":      {k: v for k, v in sections.items() if not k.startswith("_")},
        "raw":           raw,
        "file_path":     filename,
        "telegram_sent": telegram_sent,
    }


# ── 산업 레포트 ───────────────────────────────────────────────────────────────────

class IndustryReportRequest(BaseModel):
    industry_id: str
    send_telegram: bool = False


@router.get("/industries")
def list_industries():
    return [
        {
            "id":       k,
            "name_kr":  v["name_kr"],
            "name_en":  v["name_en"],
            "tagline":  v["tagline"],
            "benchmark":v["benchmark"],
            "coverage": v["coverage"],
            "icon":     v["icon"],
        }
        for k, v in INDUSTRIES.items()
    ]


@router.post("/industry-research")
async def industry_research(
    req: IndustryReportRequest,
    x_user_id: Optional[str] = Header(default=None),
):
    uid = _uid(x_user_id)
    if req.industry_id not in INDUSTRIES:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 산업: {req.industry_id}")

    try:
        sections = write_industry_report(req.industry_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    date_str = datetime.now().strftime("%Y%m%d_%H%M")
    ind_name = INDUSTRIES[req.industry_id]["name_en"].replace(" ", "_")
    filename = f"lens_industry_{ind_name}_{date_str}.md"
    raw = sections.get("_raw", "")
    save_report(filename, raw, report_type="industry_research",
                metadata={"industry_id": req.industry_id},
                user_id=uid)

    telegram_sent = False
    if req.send_telegram and raw:
        meta = INDUSTRIES[req.industry_id]
        telegram_sent = send_file_bytes(
            raw.encode("utf-8"), filename,
            caption=f"📊 LENS CAPITAL RESEARCH\n\n{meta['name_kr']} 산업 레포트가 완성되었습니다.",
        )

    return {
        "industry_id":   req.industry_id,
        "sections":      {k: v for k, v in sections.items() if not k.startswith("_")},
        "raw":           raw,
        "file_path":     filename,
        "telegram_sent": telegram_sent,
    }


# ── 레포트 이력 (전체) ───────────────────────────────────────────────────────────

@router.get("/history")
def report_history(x_user_id: Optional[str] = Header(default=None)):
    uid = _uid(x_user_id)
    rows = list_reports(uid, limit=30)
    return [
        {
            "name":    r["name"],
            "type":    r["type"],
            "size_kb": round(r.get("size", 0) / 1024, 1),
            "mtime":   r.get("created_at"),
        }
        for r in rows
    ]


@router.get("/file/{filename}")
def get_report_file(filename: str):
    content = get_report_content(filename)
    if content is None:
        raise HTTPException(status_code=404, detail="파일 없음")
    return {"content": content, "name": filename}


# ── 텔레그램 ──────────────────────────────────────────────────────────────────────

class TelegramMessageRequest(BaseModel):
    text: str


@router.post("/telegram/message")
def telegram_message(req: TelegramMessageRequest):
    ok = send_message(req.text)
    return {"ok": ok}


@router.get("/telegram/status")
def telegram_status():
    import os
    return {
        "configured":     bool(os.getenv("TELEGRAM_BOT_TOKEN")) and bool(os.getenv("TELEGRAM_CHAT_ID")),
        "bot_token_set":  bool(os.getenv("TELEGRAM_BOT_TOKEN")),
        "chat_id_set":    bool(os.getenv("TELEGRAM_CHAT_ID")),
    }

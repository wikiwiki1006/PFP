"""
routers/reports.py
───────────────────
데일리 브리프 · LENS 종목 레포트 · 산업 레포트 API
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.services.report_writer import INDUSTRIES, write_equity_report, write_industry_report
from backend.services.daily_report import generate_daily_report
from backend.services.telegram_sender import send_file_bytes, send_message

router = APIRouter(prefix="/api/reports", tags=["reports"])

_DATA_DIR    = Path(__file__).parent.parent.parent / "pfp" / "data"
_DB_FILE     = _DATA_DIR / "holdings.json"
_OUTPUTS_DIR = Path(__file__).parent.parent.parent / "outputs"
_OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)


def _load_holdings() -> dict:
    if not _DB_FILE.exists():
        return {}
    with open(_DB_FILE) as f:
        raw = json.load(f)
    return raw.get("my_holdings", raw)


# ── 데일리 브리프 ────────────────────────────────────────────────────────────────

@router.post("/daily-brief")
async def daily_brief():
    """보유 종목 기반 AI 데일리 브리프 생성 (30~60초 소요)."""
    holdings = _load_holdings()
    if not holdings:
        raise HTTPException(status_code=400, detail="보유 종목 없음")

    logs: list[str] = []
    try:
        report, price_data = generate_daily_report(holdings, log=logs.append)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    from datetime import datetime
    date_str = datetime.now().strftime("%Y%m%d_%H%M")
    out_path = _OUTPUTS_DIR / f"daily_brief_{date_str}.md"
    out_path.write_text(report, encoding="utf-8")

    return {
        "report":     report,
        "price_data": {k: v for k, v in price_data.items() if not k.startswith("__")},
        "file_path":  str(out_path),
        "logs":       logs,
    }


@router.get("/daily-brief/history")
def daily_brief_history():
    """이전에 생성된 데일리 브리프 목록 반환."""
    files = sorted(_OUTPUTS_DIR.glob("daily_brief_*.md"), reverse=True)[:20]
    return [
        {"name": f.name, "path": str(f), "size": f.stat().st_size}
        for f in files
    ]


@router.get("/daily-brief/file/{filename}")
def get_daily_brief_file(filename: str):
    """특정 데일리 브리프 파일 내용 반환."""
    path = _OUTPUTS_DIR / filename
    if not path.exists() or not path.name.startswith("daily_brief_"):
        raise HTTPException(status_code=404, detail="파일 없음")
    return {"content": path.read_text(encoding="utf-8"), "name": filename}


# ── LENS 종목 레포트 ─────────────────────────────────────────────────────────────

class EquityReportRequest(BaseModel):
    ticker: str
    company_name: str
    send_telegram: bool = False


@router.post("/equity-research")
async def equity_research(req: EquityReportRequest):
    """종목 AI 리서치 레포트 생성 (2~3분 소요)."""
    ticker  = req.ticker.upper()
    try:
        sections = write_equity_report(ticker, req.company_name)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    from datetime import datetime
    date_str  = datetime.now().strftime("%Y%m%d_%H%M")
    out_path  = _OUTPUTS_DIR / f"lens_{ticker}_{date_str}.md"
    out_path.write_text(sections.get("_raw", ""), encoding="utf-8")

    telegram_sent = False
    if req.send_telegram and sections.get("_raw"):
        content = sections["_raw"].encode("utf-8")
        telegram_sent = send_file_bytes(
            content, f"LENS_{ticker}_{date_str}.md",
            caption=f"📊 LENS CAPITAL RESEARCH\n\n{ticker} — {req.company_name}\n레포트가 완성되었습니다.",
        )

    return {
        "ticker":        ticker,
        "company_name":  req.company_name,
        "sections":      {k: v for k, v in sections.items() if not k.startswith("_")},
        "raw":           sections.get("_raw", ""),
        "file_path":     str(out_path),
        "telegram_sent": telegram_sent,
    }


# ── 산업 레포트 ──────────────────────────────────────────────────────────────────

class IndustryReportRequest(BaseModel):
    industry_id: str
    send_telegram: bool = False


@router.get("/industries")
def list_industries():
    """지원 산업 목록 반환."""
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
async def industry_research(req: IndustryReportRequest):
    """산업 AI 리서치 레포트 생성 (2~3분 소요)."""
    if req.industry_id not in INDUSTRIES:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 산업: {req.industry_id}")

    try:
        sections = write_industry_report(req.industry_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    from datetime import datetime
    date_str = datetime.now().strftime("%Y%m%d_%H%M")
    ind_name = INDUSTRIES[req.industry_id]["name_en"].replace(" ", "_")
    out_path = _OUTPUTS_DIR / f"lens_industry_{ind_name}_{date_str}.md"
    out_path.write_text(sections.get("_raw", ""), encoding="utf-8")

    telegram_sent = False
    if req.send_telegram and sections.get("_raw"):
        meta = INDUSTRIES[req.industry_id]
        content = sections["_raw"].encode("utf-8")
        telegram_sent = send_file_bytes(
            content, out_path.name,
            caption=f"📊 LENS CAPITAL RESEARCH\n\n{meta['name_kr']} 산업 레포트가 완성되었습니다.",
        )

    return {
        "industry_id":   req.industry_id,
        "sections":      {k: v for k, v in sections.items() if not k.startswith("_")},
        "raw":           sections.get("_raw", ""),
        "file_path":     str(out_path),
        "telegram_sent": telegram_sent,
    }


# ── 레포트 이력 ──────────────────────────────────────────────────────────────────

@router.get("/history")
def report_history():
    """생성된 레포트 전체 목록 (최근 30개)."""
    files = sorted(
        list(_OUTPUTS_DIR.glob("lens_*.md")) + list(_OUTPUTS_DIR.glob("daily_brief_*.md")),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )[:30]
    return [
        {
            "name":    f.name,
            "type":    "daily" if f.name.startswith("daily_brief") else "industry" if "industry" in f.name else "equity",
            "size_kb": round(f.stat().st_size / 1024, 1),
            "mtime":   f.stat().st_mtime,
        }
        for f in files
    ]


@router.get("/file/{filename}")
def get_report_file(filename: str):
    """레포트 파일 내용 반환."""
    path = _OUTPUTS_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="파일 없음")
    return {"content": path.read_text(encoding="utf-8"), "name": filename}


# ── 텔레그램 ─────────────────────────────────────────────────────────────────────

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
        "configured": bool(os.getenv("TELEGRAM_BOT_TOKEN")) and bool(os.getenv("TELEGRAM_CHAT_ID")),
        "bot_token_set": bool(os.getenv("TELEGRAM_BOT_TOKEN")),
        "chat_id_set":   bool(os.getenv("TELEGRAM_CHAT_ID")),
    }

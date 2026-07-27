"""
routers/reports.py
───────────────────
데일리 브리프 · LENS 종목 레포트 · 산업 레포트 API.
레포트를 DB(reports 테이블)에 저장.
백그라운드 잡 시스템: POST → job_id 즉시 반환, 완료 후 GET /job/{id} 폴링.
"""
from __future__ import annotations

import asyncio
import json
import re
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Optional

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


# ── 백그라운드 잡 스토어 ──────────────────────────────────────────────────────────
# { job_id: { "status": "pending"|"done"|"error"|"cancelled", "result"?: dict, "message"?: str, "_ts": float } }

_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()
_MAX_JOBS = 50


def _job_set(job_id: str, data: dict) -> None:
    with _jobs_lock:
        _jobs[job_id] = {**data, "_ts": time.time()}
        if len(_jobs) > _MAX_JOBS:
            oldest = min(_jobs, key=lambda k: _jobs[k]["_ts"])
            del _jobs[oldest]


def _job_get(job_id: str) -> dict | None:
    with _jobs_lock:
        j = _jobs.get(job_id)
        return {k: v for k, v in j.items() if k != "_ts"} if j else None


# ── 데일리 브리프 ─────────────────────────────────────────────────────────────────

@router.post("/daily-brief")
async def daily_brief(x_user_id: Optional[str] = Header(default=None)):
    uid = _uid(x_user_id)
    holdings = get_holdings(uid)
    if not holdings:
        raise HTTPException(status_code=400, detail="보유 종목 없음")

    logs: list[str] = []
    try:
        report, price_data = await asyncio.to_thread(
            generate_daily_report, holdings, logs.append
        )
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


# ── LENS 종목 레포트 (기존 동기 엔드포인트 유지) ─────────────────────────────────

class EquityReportRequest(BaseModel):
    ticker: str
    company_name: str = ""   # 하위 호환 유지용 — 내부적으로 무시됨
    send_telegram: bool = False


@router.post("/equity-research")
async def equity_research(
    req: EquityReportRequest,
    x_user_id: Optional[str] = Header(default=None),
):
    uid = _uid(x_user_id)
    ticker = req.ticker.upper()
    try:
        write_result = await asyncio.to_thread(write_equity_report, ticker)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    company_name = write_result.get("company_name", req.company_name or ticker)
    raw          = write_result.get("raw", "")
    sections     = write_result.get("sections", {})

    date_str = datetime.now().strftime("%Y%m%d_%H%M")
    filename = f"lens_{ticker}_{date_str}.md"
    save_report(filename, raw, report_type="equity_research",
                metadata={"ticker": ticker, "company": company_name},
                user_id=uid)

    telegram_sent = False
    if req.send_telegram and raw:
        telegram_sent = send_file_bytes(
            raw.encode("utf-8"), f"LENS_{ticker}_{date_str}.md",
            caption=f"📊 LENS CAPITAL RESEARCH\n\n{ticker} — {company_name}\n레포트가 완성되었습니다.",
        )

    return {
        "ticker":        ticker,
        "company_name":  company_name,
        "sections":      sections,
        "raw":           raw,
        "file_path":     filename,
        "telegram_sent": telegram_sent,
    }


# ── 산업 레포트 (기존 동기 엔드포인트 유지) ──────────────────────────────────────

class IndustryReportRequest(BaseModel):
    industry_id: str
    send_telegram: bool = False


@router.get("/industries")
def list_industries():
    return [
        {
            "id":        k,
            "name_kr":   v["name_kr"],
            "name_en":   v["name_en"],
            "tagline":   v["tagline"],
            "benchmark": v["benchmark"],
            "coverage":  v["coverage"],
            "icon":      v["icon"],
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
        write_result = await asyncio.to_thread(write_industry_report, req.industry_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    raw      = write_result.get("raw", "")
    sections = write_result.get("sections", {})

    date_str = datetime.now().strftime("%Y%m%d_%H%M")
    ind_name = INDUSTRIES[req.industry_id]["name_en"].replace(" ", "_")
    filename = f"lens_industry_{ind_name}_{date_str}.md"
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
        "sections":      sections,
        "raw":           raw,
        "file_path":     filename,
        "telegram_sent": telegram_sent,
    }


# ── 백그라운드 잡: 종목 레포트 ────────────────────────────────────────────────────

class EquityResearchStartRequest(BaseModel):
    ticker: str
    model_tier: str = "basic"   # "basic" = Haiku 전용 / "deep" = Haiku+Sonnet
    send_telegram: bool = False


@router.post("/equity-research/start")
def equity_research_start(
    req: EquityResearchStartRequest,
    x_user_id: Optional[str] = Header(default=None),
):
    """종목 레포트 백그라운드 잡 시작. 즉시 {job_id} 반환."""
    if not req.ticker.strip():
        raise HTTPException(status_code=400, detail="티커를 입력하세요.")

    ticker         = req.ticker.strip().upper()
    model_tier     = req.model_tier if req.model_tier in ("basic", "deep") else "basic"
    send_telegram  = req.send_telegram
    uid            = _uid(x_user_id)
    job_id         = str(uuid.uuid4())
    _job_set(job_id, {"status": "pending"})

    def _run() -> None:
        try:
            write_result = write_equity_report(ticker, model_tier=model_tier)
            company_name = write_result.get("company_name", ticker)
            raw          = write_result.get("raw", "")

            date_str = datetime.now().strftime("%Y%m%d_%H%M")
            slug     = re.sub(r"[^\w]", "", ticker)
            filename = f"lens_{slug}_{date_str}.md"
            save_report(
                filename, raw,
                report_type="equity_research",
                metadata={"ticker": ticker, "company": company_name},
                user_id=uid,
            )

            if send_telegram and raw:
                try:
                    send_file_bytes(
                        raw.encode("utf-8"), filename,
                        caption=f"📊 LENS CAPITAL RESEARCH\n\n{ticker} — {company_name}\n레포트가 완성되었습니다.",
                    )
                except Exception:
                    pass

            current = _job_get(job_id)
            if current and current.get("status") == "cancelled":
                return

            result = {**write_result, "report_type": "equity", "file_path": filename}
            _job_set(job_id, {"status": "done", "result": result})

        except Exception as exc:
            current = _job_get(job_id)
            if current and current.get("status") == "cancelled":
                return
            _job_set(job_id, {"status": "error", "message": str(exc)})

    threading.Thread(target=_run, daemon=True).start()
    return {"job_id": job_id}


# ── 백그라운드 잡: 산업 레포트 ────────────────────────────────────────────────────

class IndustryResearchStartRequest(BaseModel):
    industry_id: str
    model_tier: str = "basic"   # "basic" = Haiku 전용 / "deep" = Haiku+Sonnet
    send_telegram: bool = False


@router.post("/industry-research/start")
def industry_research_start(
    req: IndustryResearchStartRequest,
    x_user_id: Optional[str] = Header(default=None),
):
    """산업 레포트 백그라운드 잡 시작. 즉시 {job_id} 반환."""
    if not req.industry_id.strip():
        raise HTTPException(status_code=400, detail="산업 ID를 입력하세요.")
    if req.industry_id not in INDUSTRIES:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 산업: {req.industry_id}")

    industry_id   = req.industry_id
    model_tier    = req.model_tier if req.model_tier in ("basic", "deep") else "basic"
    send_telegram = req.send_telegram
    uid           = _uid(x_user_id)
    job_id        = str(uuid.uuid4())
    _job_set(job_id, {"status": "pending"})

    def _run() -> None:
        try:
            write_result = write_industry_report(industry_id, model_tier=model_tier)
            raw          = write_result.get("raw", "")
            meta         = INDUSTRIES[industry_id]

            date_str = datetime.now().strftime("%Y%m%d_%H%M")
            ind_name = meta["name_en"].replace(" ", "_")
            filename = f"lens_industry_{ind_name}_{date_str}.md"
            save_report(
                filename, raw,
                report_type="industry_research",
                metadata={"industry_id": industry_id},
                user_id=uid,
            )

            if send_telegram and raw:
                try:
                    send_file_bytes(
                        raw.encode("utf-8"), filename,
                        caption=f"📊 LENS CAPITAL RESEARCH\n\n{meta['name_kr']} 산업 레포트가 완성되었습니다.",
                    )
                except Exception:
                    pass

            current = _job_get(job_id)
            if current and current.get("status") == "cancelled":
                return

            result = {**write_result, "report_type": "industry", "file_path": filename}
            _job_set(job_id, {"status": "done", "result": result})

        except Exception as exc:
            current = _job_get(job_id)
            if current and current.get("status") == "cancelled":
                return
            _job_set(job_id, {"status": "error", "message": str(exc)})

    threading.Thread(target=_run, daemon=True).start()
    return {"job_id": job_id}


# ── 잡 상태 조회 / 취소 ──────────────────────────────────────────────────────────

@router.get("/job/{job_id}")
def get_report_job_status(job_id: str):
    """레포트 잡 상태 조회. status: pending | done | error | cancelled"""
    job = _job_get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="잡을 찾을 수 없습니다. 서버가 재시작됐을 수 있습니다.")
    return job


@router.delete("/job/{job_id}")
def cancel_report_job(job_id: str):
    """실행 중인 레포트 잡 취소."""
    job = _job_get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="잡을 찾을 수 없습니다.")
    if job["status"] == "pending":
        _job_set(job_id, {"status": "cancelled"})
    return {"ok": True, "status": job.get("status")}


# ── 레포트 이력 (전체) ───────────────────────────────────────────────────────────

@router.get("/history")
def report_history(x_user_id: Optional[str] = Header(default=None)):
    uid = _uid(x_user_id)
    rows = list_reports(uid, limit=30)
    return [
        {
            "name":       r["name"],
            "type":       r["type"],
            "size_kb":    round(r.get("size", 0) / 1024, 1),
            "mtime":      r.get("created_at"),
            "created_at": r.get("created_at"),
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
        "configured":    bool(os.getenv("TELEGRAM_BOT_TOKEN")) and bool(os.getenv("TELEGRAM_CHAT_ID")),
        "bot_token_set": bool(os.getenv("TELEGRAM_BOT_TOKEN")),
        "chat_id_set":   bool(os.getenv("TELEGRAM_CHAT_ID")),
    }

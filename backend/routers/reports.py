"""
routers/reports.py
───────────────────
데일리 브리프 · LENS 종목 레포트 · 산업 레포트 API.
레포트를 DB(reports 테이블)에 저장.
백그라운드 잡 시스템: POST → job_id 즉시 반환, 완료 후 GET /job/{id} 폴링.
"""
from __future__ import annotations

import asyncio
import re
import threading
import uuid
from datetime import datetime
from typing import Optional

from backend.services.auth import current_user
from fastapi import Depends, APIRouter, HTTPException, Header
from pydantic import BaseModel

from backend.services.job_store import JobStore
from backend.db.portfolio_repo import get_holdings
from backend.db.reports_repo import (
    save_report, list_reports, get_report_content,
    find_fresh_shared_report, SHARED_TTL_HOURS,
)
from backend.services.report_writer import INDUSTRIES, write_equity_report, write_industry_report
from backend.services.daily_report import generate_daily_report
from backend.services.telegram_sender import send_file_bytes, send_message

router = APIRouter(prefix="/api/reports", tags=["reports"])




# ── 백그라운드 잡 스토어 ──────────────────────────────────────────────────────────
# { job_id: { "status": "pending"|"done"|"error"|"cancelled", "result"?: dict, "message"?: str, "_ts": float } }

_store = JobStore(max_jobs=50)


def _cached_shared_result(
    report_type: str, subject_key: str, model_tier: str = "basic",
) -> dict | None:
    """유효시간 내 공용 리포트가 있으면 잡 결과 형태로 복원해 반환.

    종목·산업 리서치는 분석 대상이 같으면 사용자와 무관하게 결과가 같다.
    최초 1인이 만든 것을 재사용해 중복 생성(LLM 비용·대기시간)을 없앤다.
    유효시간: 종목 24h / 산업 72h (reports_repo.SHARED_TTL_HOURS).
    분석 등급(basic/deep)이 일치하는 리포트만 재사용한다.
    """
    hit = find_fresh_shared_report(report_type, subject_key, model_tier)
    if not hit:
        return None
    meta = hit.get("metadata") or {}
    # 예전에 저장된 리포트는 metadata 에 sections 가 없다 → 원문에서 파싱해 복원
    sections = meta.get("sections")
    if not sections:
        try:
            from backend.services.report_writer import _parse_sections
            sections = _parse_sections(hit["content"])
        except Exception:
            sections = {}
    result = {
        "raw":            hit["content"],
        "sections":       sections,
        "file_path":      hit["filename"],
        "telegram_sent":  False,
        "model_tier":     meta.get("model_tier", "basic"),
        "cache_tier":     model_tier,
        # 캐시 재사용임을 화면에서 알 수 있도록 출처 정보를 함께 내려보낸다
        "from_cache":     True,
        "cached_at":      hit["created_at"],
        "cache_age_hours": hit["age_hours"],
        "cache_ttl_hours": SHARED_TTL_HOURS.get(report_type, 24),
    }
    if report_type == "equity_research":
        result.update({
            "report_type":  "equity",
            "ticker":       meta.get("ticker", subject_key),
            "company_name": meta.get("company", subject_key),
            "market_data":  meta.get("market_data"),
        })
    else:
        result.update({
            "report_type":      "industry",
            "industry_id":      meta.get("industry_id", subject_key),
            "industry_name_kr": meta.get("industry_name_kr"),
            "industry_name_en": meta.get("industry_name_en"),
        })
    return result


def _job_set(job_id: str, data: dict, owner: str | None = None) -> None:
    _store.set(job_id, data, owner=owner)


def _job_get(job_id: str, owner: str | None = None) -> dict | None:
    return _store.get(job_id, owner=owner)


# ── 데일리 브리프 ─────────────────────────────────────────────────────────────────

@router.post("/daily-brief")
async def daily_brief(_auth: dict = Depends(current_user)):
    uid = _auth["uid"]
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
def daily_brief_history(_auth: dict = Depends(current_user)):
    uid = _auth["uid"]
    rows = list_reports(uid, report_type="daily_brief", limit=20)
    return [{"name": r["name"], "path": r["name"], "size": r.get("size", 0)} for r in rows]


@router.get("/daily-brief/file/{filename}")
def get_daily_brief_file(filename: str, _auth: dict = Depends(current_user)):
    if not filename.startswith("daily_brief_"):
        raise HTTPException(status_code=400, detail="잘못된 파일명")
    content = get_report_content(filename, _auth["uid"])
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
    _auth: dict = Depends(current_user),
):
    uid = _auth["uid"]
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
                metadata={"ticker": ticker, "company": company_name,
                          "sections": sections, "model_tier": "basic"},
                user_id=uid, scope="shared", subject_key=ticker)

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
    _auth: dict = Depends(current_user),
):
    uid = _auth["uid"]
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
                metadata={"industry_id": req.industry_id, "sections": sections,
                          "model_tier": "basic"},
                user_id=uid, scope="shared", subject_key=req.industry_id)

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
    _auth: dict = Depends(current_user),
):
    """종목 레포트 백그라운드 잡 시작. 즉시 {job_id} 반환."""
    if not req.ticker.strip():
        raise HTTPException(status_code=400, detail="티커를 입력하세요.")

    ticker         = req.ticker.strip().upper()
    model_tier     = req.model_tier if req.model_tier in ("basic", "deep") else "basic"
    send_telegram  = req.send_telegram
    uid            = _auth["uid"]
    job_id         = str(uuid.uuid4())

    # 공용 캐시 확인 — 다른 사용자가 24시간 내에 같은 종목을 이미 분석했으면 재사용.
    # 같은 종목이면 결과가 동일하므로 중복 생성(비용·시간)을 피한다.
    cached = _cached_shared_result("equity_research", ticker, model_tier)
    if cached is not None:
        _job_set(job_id, {"status": "done", "result": cached})
        return {"job_id": job_id, "cached": True}

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
                metadata={
                    "ticker": ticker, "company": company_name, "model_tier": model_tier,
                    # 캐시 재사용 시 결과를 그대로 복원하기 위해 파싱된 섹션도 함께 보관
                    "sections": write_result.get("sections", {}),
                    "market_data": write_result.get("market_data"),
                },
                user_id=uid,
                scope="shared",
                subject_key=ticker,
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
    _auth: dict = Depends(current_user),
):
    """산업 레포트 백그라운드 잡 시작. 즉시 {job_id} 반환."""
    if not req.industry_id.strip():
        raise HTTPException(status_code=400, detail="산업 ID를 입력하세요.")
    if req.industry_id not in INDUSTRIES:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 산업: {req.industry_id}")

    industry_id   = req.industry_id
    model_tier    = req.model_tier if req.model_tier in ("basic", "deep") else "basic"
    send_telegram = req.send_telegram
    uid           = _auth["uid"]
    job_id        = str(uuid.uuid4())

    # 공용 캐시 확인 — 산업은 변화 속도가 느려 72시간까지 재사용
    cached = _cached_shared_result("industry_research", industry_id, model_tier)
    if cached is not None:
        _job_set(job_id, {"status": "done", "result": cached})
        return {"job_id": job_id, "cached": True}

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
                metadata={
                    "industry_id": industry_id, "model_tier": model_tier,
                    "sections": write_result.get("sections", {}),
                    "industry_name_kr": write_result.get("industry_name_kr"),
                    "industry_name_en": write_result.get("industry_name_en"),
                },
                user_id=uid,
                scope="shared",
                subject_key=industry_id,
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
def get_report_job_status(job_id: str, _auth: dict = Depends(current_user)):
    """레포트 잡 상태 조회 (본인 잡만). status: pending | done | error | cancelled"""
    job = _job_get(job_id, owner=_auth["uid"])
    if job is None:
        raise HTTPException(status_code=404, detail="잡을 찾을 수 없습니다. 서버가 재시작됐을 수 있습니다.")
    return job


@router.delete("/job/{job_id}")
def cancel_report_job(job_id: str, _auth: dict = Depends(current_user)):
    """실행 중인 레포트 잡 취소 (본인 잡만)."""
    job = _job_get(job_id, owner=_auth["uid"])
    if job is None:
        raise HTTPException(status_code=404, detail="잡을 찾을 수 없습니다.")
    if job["status"] == "pending":
        _job_set(job_id, {"status": "cancelled"})
    return {"ok": True, "status": job.get("status")}


# ── 레포트 이력 (전체) ───────────────────────────────────────────────────────────

@router.get("/history")
def report_history(_auth: dict = Depends(current_user)):
    uid = _auth["uid"]
    rows = list_reports(uid, limit=30)
    return [
        {
            "name":       r["name"],
            "type":       r["type"],
            "size_kb":    round(r.get("size", 0) / 1024, 1),
            "mtime":      r.get("created_at"),
            "created_at": r.get("created_at"),
            "model_tier": (r.get("metadata") or {}).get("model_tier", "basic"),
            # 공용 리포트는 다른 사용자가 만든 것도 목록에 포함된다 — 구분해서 내려보낸다
            "shared":     r.get("shared", False),
            "mine":       r.get("mine", True),
        }
        for r in rows
    ]


@router.get("/file/{filename}")
def get_report_file(filename: str, _auth: dict = Depends(current_user)):
    content = get_report_content(filename, _auth["uid"])
    if content is None:
        raise HTTPException(status_code=404, detail="파일 없음")
    return {"content": content, "name": filename}


# ── 텔레그램 ──────────────────────────────────────────────────────────────────────

class TelegramMessageRequest(BaseModel):
    text: str


@router.post("/telegram/message")
def telegram_message(req: TelegramMessageRequest, _auth: dict = Depends(current_user)):
    # 인증이 없으면 아무나 운영자 채팅방으로 메시지를 밀어넣을 수 있다.
    ok = send_message(req.text)
    return {"ok": ok}


@router.get("/telegram/status")
def telegram_status(_auth: dict = Depends(current_user)):
    import os
    return {
        "configured":    bool(os.getenv("TELEGRAM_BOT_TOKEN")) and bool(os.getenv("TELEGRAM_CHAT_ID")),
        "bot_token_set": bool(os.getenv("TELEGRAM_BOT_TOKEN")),
        "chat_id_set":   bool(os.getenv("TELEGRAM_CHAT_ID")),
    }

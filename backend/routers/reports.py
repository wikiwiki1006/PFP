"""
routers/reports.py
───────────────────
데일리 브리프 · LENS 종목 레포트 · 산업 레포트 API.
레포트를 DB(reports 테이블)에 저장.
백그라운드 잡 시스템: POST → job_id 즉시 반환, 완료 후 GET /job/{id} 폴링.
"""
from __future__ import annotations

import asyncio
import logging
import re
import threading
import uuid
from datetime import datetime
from typing import Optional

from backend.routers._errors import hidden_http_error, log_hidden, user_sentence
from backend.services.markets import market_param
from backend.services.auth import (ai_feature_user, current_user,
                                   enforce_deep_limit, resolve_model_tier)
from fastapi import Depends, APIRouter, HTTPException, Header
from pydantic import BaseModel

from backend.services.job_store import JobCancelled, JobStore
from backend.db.portfolio_repo import get_holdings
from backend.db.reports_repo import (
    save_report, list_reports, get_report_content,
    find_fresh_shared_report, SHARED_TTL_HOURS,
)
from backend.services.report_writer import (industries_for, industry_meta,
                                            write_equity_report, write_industry_report)
from backend.services.daily_report import generate_daily_report

router = APIRouter(prefix="/api/reports", tags=["reports"])

# 이 모듈에는 로거가 없었다 — 그래서 아래 실패들은 `detail=str(e)` 로 응답에만
# 실리고 로그에는 한 줄도 안 남았다 (§1.3: 로거가 없으면 먼저 만든다).
logger = logging.getLogger(__name__)

# 생성이 실패했을 때 사용자에게 보내는 문장. 원인은 로그로만 남긴다 (_errors.py).
_REPORT_FAILED = "리포트를 만드는 중 서버 오류가 났습니다. 잠시 후 다시 시도해 주세요."

# 데일리 브리프를 **만들지 않기로 한** 사유 — backend/services/daily_report.py 의
# generate_daily_report 가 사용자용 문장으로 올리는 RuntimeError 들이다. 문구가
# 바뀌면 여기 목록에서 빠져 일반 안내로 떨어진다(내부 문자열이 새지는 않는다).
_BRIEF_USER_REASONS = (
    re.compile(r"가격 데이터를 가져오지 못했습니다\."),
    # 앞은 기준일이다 — 서비스가 "2026년 09월 17일 (Thu)" 같은 표기로 적는다
    # (없으면 "기준일"). 날짜 표기가 바뀌어도 맞도록 한 줄 40자 안의 아무 글자로 둔다.
    re.compile(r"[^\n]{1,40} 기준 보유 종목 등락을 하나도 구하지 못했습니다"),
)



# ── 백그라운드 잡 스토어 ──────────────────────────────────────────────────────────
# { job_id: { "status": "pending"|"done"|"error"|"cancelled", "result"?: dict, "message"?: str, "_ts": float } }

_store = JobStore(kind="reports", max_jobs=50)


def _cached_shared_result(
    report_type: str, subject_key: str, model_tier: str = "basic",
    market: str = "US",
) -> dict | None:
    """유효시간 내 공용 리포트가 있으면 잡 결과 형태로 복원해 반환.

    종목·산업 리서치는 분석 대상이 같으면 사용자와 무관하게 결과가 같다.
    최초 1인이 만든 것을 재사용해 중복 생성(LLM 비용·대기시간)을 없앤다.
    유효시간: 종목 24h / 산업 72h (reports_repo.SHARED_TTL_HOURS).
    분석 등급(basic/deep)이 일치하는 리포트만 재사용한다.
    """
    hit = find_fresh_shared_report(report_type, subject_key, model_tier, market=market)
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
async def daily_brief(_auth: dict = Depends(ai_feature_user), market: str = Depends(market_param)):
    uid = _auth["uid"]
    holdings = get_holdings(uid, market=market)
    if not holdings:
        raise HTTPException(status_code=400, detail="보유 종목 없음")

    logs: list[str] = []
    try:
        report, price_data = await asyncio.to_thread(
            generate_daily_report, holdings, logs.append, market
        )
    except Exception as e:
        # 브리프를 **만들지 않기로 한** 사유는 서비스가 사용자용 문장으로 올린다
        # (가격을 못 받음 · 기준일 종가가 아직 없음). 그 문장은 그대로 보여 준다 —
        # 기다리면 되는지 알려 주는 유일한 신호다. 그 밖(API 키 없음 · 모델 호출
        # 실패 · DB 오류)은 내부 사정이라 로그로만 남기고 일반 안내를 보낸다.
        reason = user_sentence(e, _BRIEF_USER_REASONS) if isinstance(e, RuntimeError) else None
        if reason is not None:
            logger.warning("데일리 브리프를 만들지 않았다 (%s): %s", market, reason)
            raise HTTPException(status_code=500, detail=reason)
        raise hidden_http_error(
            logger, f"데일리 브리프 생성 ({market})", e, status_code=500,
            message="브리핑을 만드는 중 서버 오류가 났습니다. 잠시 후 다시 시도해 주세요. "
                    "계속되면 관리자에게 알려 주세요.",
        )

    date_str = datetime.now().strftime("%Y%m%d_%H%M")
    filename = f"daily_brief_{date_str}.md"
    save_report(filename, report, report_type="daily_brief",
                metadata={"user_id": uid}, user_id=uid, market=market)

    return {
        "report":     report,
        "price_data": {k: v for k, v in price_data.items() if not k.startswith("__")},
        "file_path":  filename,
        "logs":       logs,
    }


@router.get("/daily-brief/history")
def daily_brief_history(_auth: dict = Depends(current_user), market: str = Depends(market_param)):
    uid = _auth["uid"]
    rows = list_reports(uid, report_type="daily_brief", limit=20, market=market)
    return [{"name": r["name"], "path": r["name"], "size": r.get("size", 0)} for r in rows]


@router.get("/daily-brief/file/{filename}")
def get_daily_brief_file(filename: str, _auth: dict = Depends(current_user), market: str = Depends(market_param)):
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


@router.post("/equity-research")
async def equity_research(
    req: EquityReportRequest,
    _auth: dict = Depends(ai_feature_user),
    market: str = Depends(market_param),
):
    uid = _auth["uid"]
    ticker = req.ticker.upper()
    try:
        write_result = await asyncio.to_thread(write_equity_report, ticker, "basic", None, market)
    except Exception as e:
        raise hidden_http_error(logger, f"종목 리포트 생성 ({ticker}, {market})", e,
                                status_code=500, message=_REPORT_FAILED)

    company_name = write_result.get("company_name", req.company_name or ticker)
    raw          = write_result.get("raw", "")
    sections     = write_result.get("sections", {})

    date_str = datetime.now().strftime("%Y%m%d_%H%M")
    filename = f"lens_{ticker}_{date_str}.md"
    save_report(filename, raw, report_type="equity_research",
                metadata={"ticker": ticker, "company": company_name,
                          "sections": sections, "model_tier": "basic"},
                user_id=uid, scope="shared", subject_key=ticker, market=market)

    return {
        "ticker":        ticker,
        "company_name":  company_name,
        "sections":      sections,
        "raw":           raw,
        "file_path":     filename,
    }


# ── 산업 레포트 (기존 동기 엔드포인트 유지) ──────────────────────────────────────

class IndustryReportRequest(BaseModel):
    industry_id: str


@router.get("/industries")
def list_industries(market: str = Depends(market_param)):
    """시장에 맞는 산업 목록. 한국은 조선·엔터처럼 국내 증시의 축이 되는
    산업군을, 미국은 기존 목록을 돌려준다."""
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
        for k, v in industries_for(market).items()
    ]


@router.post("/industry-research")
async def industry_research(
    req: IndustryReportRequest,
    _auth: dict = Depends(ai_feature_user),
    market: str = Depends(market_param),
):
    uid = _auth["uid"]
    if industry_meta(req.industry_id) is None:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 산업: {req.industry_id}")

    try:
        write_result = await asyncio.to_thread(write_industry_report, req.industry_id, "basic", None, market)
    except Exception as e:
        raise hidden_http_error(logger, f"산업 리포트 생성 ({req.industry_id}, {market})", e,
                                status_code=500, message=_REPORT_FAILED)

    raw      = write_result.get("raw", "")
    sections = write_result.get("sections", {})

    date_str = datetime.now().strftime("%Y%m%d_%H%M")
    ind_name = industry_meta(req.industry_id)["name_en"].replace(" ", "_")
    filename = f"lens_industry_{ind_name}_{date_str}.md"
    save_report(filename, raw, report_type="industry_research",
                metadata={"industry_id": req.industry_id, "sections": sections,
                          "model_tier": "basic"},
                user_id=uid, scope="shared", subject_key=req.industry_id, market=market)

    return {
        "industry_id":   req.industry_id,
        "sections":      sections,
        "raw":           raw,
        "file_path":     filename,
    }


# ── 백그라운드 잡: 종목 레포트 ────────────────────────────────────────────────────

class EquityResearchStartRequest(BaseModel):
    ticker: str
    model_tier: str = "basic"   # "basic" = Haiku 전용 / "deep" = Haiku+Sonnet


@router.post("/equity-research/start")
def equity_research_start(
    req: EquityResearchStartRequest,
    _auth: dict = Depends(ai_feature_user),
    market: str = Depends(market_param),
):
    """종목 레포트 백그라운드 잡 시작. 즉시 {job_id} 반환."""
    if not req.ticker.strip():
        raise HTTPException(status_code=400, detail="티커를 입력하세요.")

    ticker         = req.ticker.strip().upper()
    model_tier     = resolve_model_tier(req.model_tier, _auth)
    if model_tier == "deep":
        enforce_deep_limit(_auth, "equity_research")
    uid            = _auth["uid"]
    job_id         = str(uuid.uuid4())

    # 공용 캐시 확인 — 다른 사용자가 24시간 내에 같은 종목을 이미 분석했으면 재사용.
    # 같은 종목이면 결과가 동일하므로 중복 생성(비용·시간)을 피한다.
    cached = _cached_shared_result("equity_research", ticker, model_tier, market=market)
    if cached is not None:
        _job_set(job_id, {"status": "done", "result": cached}, owner=uid)
        return {"job_id": job_id, "cached": True}

    if model_tier == "deep":
        # 캐시로 돌려준 경우는 위에서 이미 반환됐다 — 여기 왔다는 건 실제로 만든다는 뜻.
        # 확인과 기록을 한 트랜잭션에 묶는다. 나눠 두면 동시 요청 둘이 모두 0 을
        # 보고 통과해 1일 1회 제한에 심층이 두 번 나간다.
        #
        # 잡을 만들기 **전에** 소비한다 — 뒤에 두면 429 로 끝난 요청이 pending
        # 잡을 남긴다. 예외는 잡지 않는다: DBBusy 는 503, 나머지는 500 이고
        # 둘 다 '닫힘' 이 맞다 (§1.3).
        from backend.db import usage_repo
        if not usage_repo.consume(uid, "equity_research"):
            raise HTTPException(status_code=429,
                                detail="심층 분석은 24시간에 한 번만 사용할 수 있습니다.")

    _job_set(job_id, {"status": "pending"}, owner=uid)
    should_cancel = _store.cancel_token(job_id)

    def _run() -> None:
        try:
            write_result = write_equity_report(ticker, model_tier=model_tier, market=market,
                                               should_cancel=should_cancel)
            company_name = write_result.get("company_name", ticker)
            raw          = write_result.get("raw", "")

            date_str = datetime.now().strftime("%Y%m%d_%H%M")
            slug     = re.sub(r"[^\w]", "", ticker)
            filename = f"lens_{slug}_{date_str}.md"
            # 생성 직후~저장 사이의 짧은 틈에 취소됐을 수 있다.
            # 취소한 리포트를 저장하면 공용 캐시로 남아 다른 사용자에게도 노출된다.
            if should_cancel():
                return
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
                subject_key=ticker, market=market,)


            result = {**write_result, "report_type": "equity", "file_path": filename}
            # pending 일 때만 done 으로 바꾼다 — 취소된 잡을 되살리지 않는다.
            _store.update_if(job_id, "pending", {"status": "done", "result": result})

        except JobCancelled:
            return   # 취소는 실패가 아니다. 상태는 이미 cancelled 로 바뀌어 있다.
        except Exception as exc:
            # 잡 상태는 GET /job/{id} 로 그대로 나간다 — 예외 문자열은 로그로만.
            log_hidden(logger, f"종목 리포트 잡 ({ticker}, {market}, {model_tier})", exc)
            _store.update_if(job_id, "pending", {"status": "error", "message": _REPORT_FAILED})

    threading.Thread(target=_run, daemon=True).start()
    return {"job_id": job_id}


# ── 백그라운드 잡: 산업 레포트 ────────────────────────────────────────────────────

class IndustryResearchStartRequest(BaseModel):
    industry_id: str
    model_tier: str = "basic"   # "basic" = Haiku 전용 / "deep" = Haiku+Sonnet


@router.post("/industry-research/start")
def industry_research_start(
    req: IndustryResearchStartRequest,
    _auth: dict = Depends(ai_feature_user),
    market: str = Depends(market_param),
):
    """산업 레포트 백그라운드 잡 시작. 즉시 {job_id} 반환."""
    if not req.industry_id.strip():
        raise HTTPException(status_code=400, detail="산업 ID를 입력하세요.")
    if industry_meta(req.industry_id) is None:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 산업: {req.industry_id}")

    industry_id   = req.industry_id
    model_tier    = resolve_model_tier(req.model_tier, _auth)
    if model_tier == "deep":
        enforce_deep_limit(_auth, "industry_research")
    uid           = _auth["uid"]
    job_id        = str(uuid.uuid4())

    # 공용 캐시 확인 — 산업은 변화 속도가 느려 72시간까지 재사용
    cached = _cached_shared_result("industry_research", industry_id, model_tier, market=market)
    if cached is not None:
        _job_set(job_id, {"status": "done", "result": cached}, owner=uid)
        return {"job_id": job_id, "cached": True}

    if model_tier == "deep":
        # equity 쪽과 같다 — 캐시 반환은 위에서 끝났으므로 여기가 '실제로 만든다'
        # 가 확정되는 지점이고, 잡을 만들기 전에 원자적으로 소비한다.
        from backend.db import usage_repo
        if not usage_repo.consume(uid, "industry_research"):
            raise HTTPException(status_code=429,
                                detail="심층 분석은 24시간에 한 번만 사용할 수 있습니다.")

    _job_set(job_id, {"status": "pending"}, owner=uid)
    should_cancel = _store.cancel_token(job_id)

    def _run() -> None:
        try:
            write_result = write_industry_report(industry_id, model_tier=model_tier, market=market,
                                                 should_cancel=should_cancel)
            raw          = write_result.get("raw", "")
            meta         = industry_meta(industry_id)

            date_str = datetime.now().strftime("%Y%m%d_%H%M")
            ind_name = meta["name_en"].replace(" ", "_")
            filename = f"lens_industry_{ind_name}_{date_str}.md"
            if should_cancel():
                return
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
                subject_key=industry_id, market=market,)


            result = {**write_result, "report_type": "industry", "file_path": filename}
            _store.update_if(job_id, "pending", {"status": "done", "result": result})

        except JobCancelled:
            return
        except Exception as exc:
            # 잡 상태는 GET /job/{id} 로 그대로 나간다 — 예외 문자열은 로그로만.
            log_hidden(logger, f"산업 리포트 잡 ({industry_id}, {market}, {model_tier})", exc)
            _store.update_if(job_id, "pending", {"status": "error", "message": _REPORT_FAILED})

    threading.Thread(target=_run, daemon=True).start()
    return {"job_id": job_id}


# ── 잡 상태 조회 / 취소 ──────────────────────────────────────────────────────────

@router.get("/job/{job_id}")
def get_report_job_status(job_id: str, _auth: dict = Depends(current_user), market: str = Depends(market_param)):
    """레포트 잡 상태 조회 (본인 잡만). status: pending | done | error | cancelled"""
    job = _job_get(job_id, owner=_auth["uid"])
    if job is None:
        raise HTTPException(status_code=404, detail="잡을 찾을 수 없습니다. 서버가 재시작됐을 수 있습니다.")
    return job


@router.delete("/job/{job_id}")
def cancel_report_job(job_id: str, _auth: dict = Depends(current_user), market: str = Depends(market_param)):
    """실행 중인 레포트 잡 취소 (본인 잡만).

    _store.cancel() 은 상태를 바꾸는 동시에 취소 신호를 올린다. 작업 스레드는
    그 신호를 보고 LLM 스트리밍을 끊고 빠져나온다 — 상태만 바꾸던 예전 방식은
    화면에서만 멈춘 것처럼 보이고 생성은 끝까지 진행됐다.
    """
    prev = _store.cancel(job_id, owner=_auth["uid"])
    if prev is None:
        raise HTTPException(status_code=404, detail="잡을 찾을 수 없습니다.")
    return {"ok": True, "status": prev}


# ── 레포트 이력 (전체) ───────────────────────────────────────────────────────────

@router.get("/history")
def report_history(_auth: dict = Depends(current_user), market: str = Depends(market_param)):
    uid = _auth["uid"]
    rows = list_reports(uid, limit=30, market=market)
    return [
        {
            "name":       r["name"],
            "type":       r["type"],
            "size_kb":    round(r.get("size", 0) / 1024, 1),
            "mtime":      r.get("created_at"),
            "created_at": r.get("created_at"),
            "model_tier": (r.get("metadata") or {}).get("model_tier", "basic"),
            # 종목 리포트의 티커. 화면은 파일명(`lens_005930KS_…`)이 아니라 이 값으로
            # 종목 이름을 찾는다 — 파일명의 티커는 점이 빠진 형태라 되짚으려면 추측이
            # 필요하다. 저장 때 metadata 에 넣는다(두 생성 경로 모두). 산업은 None.
            "ticker":     (r.get("metadata") or {}).get("ticker"),
            # 목록에는 본인이 만든 것만 담긴다(list_reports 참고).
            # shared 는 "내 리포트가 공용으로도 재사용된다"는 표시일 뿐,
            # 남의 리포트라는 뜻이 아니다.
            "shared":     r.get("shared", False),
            "mine":       True,
        }
        for r in rows
    ]


@router.get("/file/{filename}")
def get_report_file(filename: str, _auth: dict = Depends(current_user), market: str = Depends(market_param)):
    content = get_report_content(filename, _auth["uid"])
    if content is None:
        raise HTTPException(status_code=404, detail="파일 없음")
    return {"content": content, "name": filename}





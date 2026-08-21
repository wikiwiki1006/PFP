"""
routers/macro.py
─────────────────
9-에이전트 거시경제 분석 + 데일리 브리프 API
분석은 백그라운드 스레드에서 실행되어 프론트엔드 새로고침에도 중단되지 않는다.
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from backend.services.auth import current_user
from fastapi import Depends, APIRouter, Header, HTTPException
from pydantic import BaseModel

from backend.models.macro import MacroAnalysisRequest
from backend.services.ai_analysis import (
    run_macro_agents,
    parse_verdict_cards,
    parse_portfolio_actions,
    get_ai_analyst_feedback,
    generate_daily_brief,
    ANALYSIS_MODES,
    MODEL_OPTIONS,
)
from backend.services.job_store import JobStore
from backend.db.reports_repo import save_report, list_reports, get_report_content
from backend.services.market_data import (
    get_close_df,
    get_sector_changes,
    get_fred_macro,
    get_portfolio_news,
    GICS_SECTOR_ETFS,
)
from backend.services.portfolio_calculator import calculate_metrics, build_equity_curve

router = APIRouter(prefix="/api/macro", tags=["macro"])

# ── 백그라운드 분석 잡 스토어 ─────────────────────────────────────────────────
# { job_id: { "status": "pending"|"done"|"error", "result"?: dict, "message"?: str, "_ts": float } }
_store = JobStore(max_jobs=50)


def _job_set(job_id: str, data: dict, owner: str | None = None) -> None:
    _store.set(job_id, data, owner=owner)


def _job_get(job_id: str, owner: str | None = None) -> dict | None:
    return _store.get(job_id, owner=owner)


_DATA_DIR = Path(__file__).parent.parent.parent / "pfp" / "data"
_DB_FILE  = _DATA_DIR / "holdings.json"
_LOG_FILE = _DATA_DIR / "trade_log.json"


def _load_holdings(uid: str) -> dict:
    """호출자 본인의 보유 종목. uid 는 검증된 토큰에서만 나온다."""
    from backend.db.portfolio_repo import get_holdings as _db_get_holdings
    from backend.db import is_available as _db_ok
    if _db_ok():
        holdings = _db_get_holdings(uid)
        if holdings:
            return holdings
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
def analyze_macro(
    req: MacroAnalysisRequest,
    _auth: dict = Depends(current_user),
):
    """
    9-에이전트 거시경제 이벤트 분석 (백그라운드 잡).
    즉시 { job_id } 를 반환하고, 분석은 백그라운드 스레드에서 계속 실행된다.
    GET /api/macro/job/{job_id} 로 완료 여부를 폴링하면 된다.
    """
    if not req.event.strip():
        raise HTTPException(status_code=400, detail="이벤트를 입력하세요.")

    job_id = str(uuid.uuid4())
    _job_set(job_id, {"status": "pending"}, owner=_auth["uid"])

    # 스레드에 전달할 값을 미리 캡처 (req 객체가 스레드 내에서 변경될 수 있으므로)
    ev           = req.event
    req_model    = req.model
    req_mode     = req.mode
    req_port     = req.portfolio
    req_provider = req.provider if hasattr(req, "provider") else "claude"
    uid          = _auth["uid"]

    def _run() -> None:
        try:
            portfolio = req_port or _load_holdings(uid)
            agent_results = run_macro_agents(
                event=ev,
                portfolio=portfolio,
                model_key="sonnet" if "sonnet" in req_model else "haiku",
                mode=req_mode,
                provider=req_provider,
            )

            verdict_cards = None
            portfolio_actions = None
            for ag in agent_results:
                if ag["id"] == 9:
                    verdict_cards = parse_verdict_cards(ag["text"])
                if ag["id"] == 8:
                    portfolio_actions = parse_portfolio_actions(ag["text"])

            result = {
                "event":             ev,
                "agents":            agent_results,
                "verdict_cards":     verdict_cards,
                "portfolio_actions": portfolio_actions,
            }

            date_str   = datetime.now().strftime("%Y%m%d_%H%M")
            event_slug = re.sub(r"[^\w가-힣]", "_", ev[:30]).strip("_")
            filename   = f"macro_{date_str}_{event_slug}.json"
            # 매크로 시나리오는 **개인 전용**이다.
            # 사용자가 입력한 이벤트/프롬프트에 종속된 결과라 다른 사용자와 공유할 수 없고,
            # 프롬프트 내용 자체가 사생활일 수 있으므로 scope='private' 를 명시한다.
            save_report(
                filename,
                json.dumps(result, ensure_ascii=False),
                report_type="macro_scenario",
                metadata={"event": ev[:200], "mode": req_mode, "model": req_model},
                user_id=uid,
                scope="private",
            )

            current = _job_get(job_id)
            if current and current.get("status") == "cancelled":
                return
            _job_set(job_id, {"status": "done", "result": result})
        except Exception as exc:
            current = _job_get(job_id)
            if current and current.get("status") == "cancelled":
                return
            _job_set(job_id, {"status": "error", "message": str(exc)})

    threading.Thread(target=_run, daemon=True).start()
    return {"job_id": job_id}


@router.get("/job/{job_id}")
def get_job_status(job_id: str, _auth: dict = Depends(current_user)):
    """분석 잡 상태 조회 (본인 잡만). status: pending | done | error | cancelled"""
    job = _job_get(job_id, owner=_auth["uid"])
    if job is None:
        raise HTTPException(status_code=404, detail="잡을 찾을 수 없습니다. 서버가 재시작됐을 수 있습니다.")
    return job


@router.delete("/job/{job_id}")
def cancel_job(job_id: str, _auth: dict = Depends(current_user)):
    """실행 중인 분석 잡 취소 (본인 잡만)."""
    job = _job_get(job_id, owner=_auth["uid"])
    if job is None:
        raise HTTPException(status_code=404, detail="잡을 찾을 수 없습니다.")
    if job["status"] == "pending":
        _job_set(job_id, {"status": "cancelled"})
    return {"ok": True, "status": job.get("status")}


@router.get("/reports")
def list_macro_reports(_auth: dict = Depends(current_user)):
    """저장된 시나리오 레포트 목록 반환."""
    uid = _auth["uid"]
    rows = list_reports(uid, report_type="macro_scenario", limit=30)
    return [
        {
            "name":       r["name"],
            "event":      r["metadata"].get("event", ""),
            "mode":       r["metadata"].get("mode", ""),
            "created_at": r["created_at"],
        }
        for r in rows
    ]


@router.get("/reports/{filename}")
def get_macro_report(filename: str, _auth: dict = Depends(current_user)):
    """특정 시나리오 레포트 내용 반환 (본인 또는 공용 리포트만)."""
    if not filename.startswith("macro_"):
        raise HTTPException(status_code=400, detail="잘못된 파일명")
    content = get_report_content(filename, _auth["uid"])
    if content is None:
        raise HTTPException(status_code=404, detail="레포트를 찾을 수 없습니다.")
    try:
        return json.loads(content)
    except Exception:
        raise HTTPException(status_code=500, detail="레포트 파싱 실패")


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
def analyst_feedback_auto(
    live: LiveMetrics = LiveMetrics(),
    _auth: dict = Depends(current_user),
):
    """포트폴리오 섹터 기반 AI 피드백 생성."""
    uid = _auth["uid"]
    from backend.db.portfolio_repo import (
        get_holdings as _db_get_holdings,
        get_trade_log as _db_get_trade_log,
    )
    from backend.db import is_available as _db_ok

    if _db_ok():
        holdings  = _db_get_holdings(uid)
        trade_log = _db_get_trade_log(uid)
    else:
        holdings  = _load_holdings(uid)
        trade_log = _load_trade_log()

    if not holdings:
        raise HTTPException(status_code=400, detail="보유 종목 없음")

    tickers  = [t for t in holdings if t != "CASH"]
    close_df = get_close_df(tickers, period="5d", ttl=60)

    equity_curve = build_equity_curve(holdings, trade_log, close_df)
    # raw_df(fill=False): 일변동률을 '마지막 두 실제 관측치'로 계산하도록 전달.
    # 넘기지 않으면 에쿼티 커브 위치 차분으로 폴백해 유령 행에서 0%가 나온다.
    raw_df = (
        get_close_df(tickers, period="1mo", ttl=1800, include_market=False, fill=False)
        if tickers else None
    )
    metrics = calculate_metrics(holdings, close_df, equity_curve, raw_df=raw_df)

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
def daily_brief(
    portfolio: Optional[dict] = None,
    _auth: dict = Depends(current_user),
):
    """오늘의 포트폴리오 브리프 마크다운 생성 (Claude Sonnet)."""
    holdings  = portfolio or _load_holdings(_auth["uid"])
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

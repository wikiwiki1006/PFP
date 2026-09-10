"""
routers/macro.py
─────────────────
9-에이전트 거시경제 분석 + 데일리 브리프 API
분석은 백그라운드 스레드에서 실행되어 프론트엔드 새로고침에도 중단되지 않는다.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from datetime import datetime
from typing import Optional

from backend.services.auth import (ai_feature_user, current_user,
                                   enforce_deep_limit, resolve_model_tier)
from fastapi import Depends, APIRouter, Header, HTTPException

from backend.services.markets import market_param
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
from backend.services.job_store import JobCancelled, JobStore
from backend.db.reports_repo import save_report, list_reports, get_report_content
from backend.services.market_data import (
    get_close_df,
    get_sector_changes,
    get_portfolio_news,
    GICS_SECTOR_ETFS,
)
from backend.services.portfolio_calculator import calculate_metrics, build_equity_curve

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/macro", tags=["macro"])

# ── 백그라운드 분석 잡 스토어 ─────────────────────────────────────────────────
# { job_id: { "status": "pending"|"done"|"error", "result"?: dict, "message"?: str, "_ts": float } }
_store = JobStore(kind="macro", max_jobs=50)


def _job_set(job_id: str, data: dict, owner: str | None = None) -> None:
    _store.set(job_id, data, owner=owner)


def _job_get(job_id: str, owner: str | None = None) -> dict | None:
    return _store.get(job_id, owner=owner)


def _load_holdings(uid: str, market: str = "US") -> dict:
    """호출자 본인의 보유 종목. uid 는 검증된 토큰에서만 나온다."""
    from backend.db.portfolio_repo import get_holdings as _db_get_holdings
    return _db_get_holdings(uid, market=market)


def _load_trade_log(uid: str, market: str = "US") -> list:
    """호출자 본인의 거래 이력. 자산곡선의 취득원가가 여기서 나온다."""
    from backend.db.portfolio_repo import get_trade_log as _db_get_trade_log
    return _db_get_trade_log(uid, market=market)


# ── 거시경제 분석 ─────────────────────────────────────────────────────────────

@router.post("/analyze")
def analyze_macro(
    req: MacroAnalysisRequest,
    _auth: dict = Depends(ai_feature_user),
    market: str = Depends(market_param),
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
    should_cancel = _store.cancel_token(job_id)

    # 스레드에 전달할 값을 미리 캡처 (req 객체가 스레드 내에서 변경될 수 있으므로)
    ev           = req.event
    req_model    = req.model
    req_mode     = req.mode
    req_port     = req.portfolio
    req_provider = req.provider if hasattr(req, "provider") else "claude"
    uid          = _auth["uid"]

    # 심층 분석이 잠겨 있으면 sonnet 요청을 basic(haiku)으로 낮춘다.
    tier = resolve_model_tier("deep" if "sonnet" in req_model else "basic", _auth)
    if tier == "deep":
        enforce_deep_limit(_auth, "macro_scenario")
        # 기록이 실패해도 이 요청은 계속 진행하되, 조용히 넘기지는 않는다.
        # 이 테이블이 할당량의 유일한 근거라, 기록이 안 되는 상태가 로그 없이
        # 이어지면 그동안 제한이 사실상 없어진다 (§1.3).
        #
        # 여기서 예외를 다시 올리지 않는 이유는 reports.py 의 _record_deep_use
        # 와 같다 — 이 요청은 바로 위 enforce_deep_limit 을 이미 통과했다.
        # 기록 실패가 위태롭게 하는 것은 이 요청이 아니라 다음 요청이고,
        # 그쪽은 count_recent 가 실패 시 예외를 올려 막는다.
        try:
            from backend.db import usage_repo
            usage_repo.record_use(uid, "macro_scenario")
        except Exception as e:
            logger.error(
                f"심층 분석 사용 기록 실패. 이 사용은 할당량에 잡히지 않는다 "
                f"(uid={uid}, kind=macro_scenario): {e}"
            )

    def _run() -> None:
        try:
            portfolio = req_port or _load_holdings(uid, market=market)
            agent_results = run_macro_agents(
                event=ev,
                portfolio=portfolio,
                model_key="sonnet" if tier == "deep" else "haiku",
                mode=req_mode,
                provider=req_provider,
                should_cancel=should_cancel,
                market=market,
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
            # 취소한 분석을 과거 목록에 남기지 않는다.
            if should_cancel():
                return
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

            # pending 일 때만 done 으로 바꾼다 — 취소된 잡을 되살리지 않는다.
            _store.update_if(job_id, "pending", {"status": "done", "result": result})
        except JobCancelled:
            return   # 취소는 실패가 아니다. 상태는 이미 cancelled 다.
        except Exception as exc:
            _store.update_if(job_id, "pending", {"status": "error", "message": str(exc)})

    threading.Thread(target=_run, daemon=True).start()
    return {"job_id": job_id}


@router.get("/job/{job_id}")
def get_job_status(job_id: str, _auth: dict = Depends(current_user), market: str = Depends(market_param)):
    """분석 잡 상태 조회 (본인 잡만). status: pending | done | error | cancelled"""
    job = _job_get(job_id, owner=_auth["uid"])
    if job is None:
        raise HTTPException(status_code=404, detail="잡을 찾을 수 없습니다. 서버가 재시작됐을 수 있습니다.")
    return job


@router.delete("/job/{job_id}")
def cancel_job(job_id: str, _auth: dict = Depends(current_user), market: str = Depends(market_param)):
    """실행 중인 분석 잡 취소 (본인 잡만).

    _store.cancel() 이 상태 변경과 취소 신호를 함께 처리한다 — 신호가 없으면
    9개 에이전트가 끝까지 돌아 화면에서만 멈춘 것처럼 보인다.
    """
    prev = _store.cancel(job_id, owner=_auth["uid"])
    if prev is None:
        raise HTTPException(status_code=404, detail="잡을 찾을 수 없습니다.")
    return {"ok": True, "status": prev}


@router.get("/reports")
def list_macro_reports(_auth: dict = Depends(current_user), market: str = Depends(market_param)):
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
def get_macro_report(filename: str, _auth: dict = Depends(current_user), market: str = Depends(market_param)):
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
    _auth: dict = Depends(ai_feature_user),
    market: str = Depends(market_param),
):
    """포트폴리오 섹터 기반 AI 피드백 생성.

    장이 닫혀 있는 동안에는 값이 변하지 않는다. 그때마다 새로 만들면 같은 답을
    받으려고 LLM 비용과 20~30초를 다시 쓰게 되므로, 다음 장이 열릴 때까지
    한 번 만든 결과를 재사용한다. 장중에는 지표가 실시간으로 움직이므로
    누를 때마다 새로 만든다.
    """
    uid = _auth["uid"]

    from backend.db.reports_repo import get_analysis, save_analysis
    from backend.services.market_calendar import (
        is_us_market_open, is_kr_market_open, next_session_open,
        last_completed_kr_session,
    )

    # 장중 판단은 그 시장 기준이어야 한다. 미국 기준으로 고정하면 한국장이
    # 열려 있는 동안 '마감'으로 보고 낡은 캐시를 계속 돌려준다.
    market_open = is_kr_market_open() if market == "KR" else is_us_market_open()
    # 장 마감 후 저녁과 다음 날 아침이 같은 키를 갖도록 '다음 개장일'로 묶는다.
    # 캐시 키에도 시장을 넣는다 — 안 넣으면 미국 피드백이 한국 화면에 나온다.
    if market == "KR":
        cache_key = f"KR_after_{last_completed_kr_session().isoformat()}"
    else:
        cache_key = f"until_{next_session_open().isoformat()}"

    if not market_open:
        cached = get_analysis("analyst_feedback", cache_key, user_id=uid)
        if cached:
            return {**cached, "from_cache": True}
    holdings  = _load_holdings(uid, market=market)
    trade_log = _load_trade_log(uid, market=market)

    if not holdings:
        raise HTTPException(status_code=400, detail="보유 종목 없음")

    tickers  = [t for t in holdings if t != "CASH"]
    close_df = get_close_df(tickers, period="5d", ttl=60)

    equity_curve = build_equity_curve(holdings, trade_log, close_df, market=market)
    # raw_df(fill=False): 일변동률을 '마지막 두 실제 관측치'로 계산하도록 전달.
    # 넘기지 않으면 에쿼티 커브 위치 차분으로 폴백해 유령 행에서 0%가 나온다.
    raw_df = (
        get_close_df(tickers, period="1mo", ttl=1800, include_market=False, fill=False)
        if tickers else None
    )
    metrics = calculate_metrics(holdings, close_df, equity_curve, raw_df=raw_df, market=market)

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
    sector_chgs = get_sector_changes(market)
    from backend.services.market_data import sector_etfs_for
    _sector_list = sector_etfs_for(market)
    _etf_to_label = {etf: label for label, etf in _sector_list}
    _label_to_etf = {label: etf for label, etf in _sector_list}

    # 보유 섹터를 비중 내림차순 정렬
    held_sectors = sorted(sector_weights.items(), key=lambda x: x[1], reverse=True)

    # 섹터별 비중 + 오늘 변동률 조합
    sector_lines = []
    for sec, wt in held_sectors[:4]:
        # GICS label → ETF lookup (대소문자 무시)
        etf_chg = None
        for label, etf in _sector_list:
            if label.lower() in sec.lower() or sec.lower() in label.lower():
                etf_chg = sector_chgs.get(etf)
                break
        chg_str = f"{etf_chg:+.1f}%" if etf_chg is not None else "N/A"
        sector_lines.append(f"{sec}({wt*100:.0f}%, 오늘{chg_str})")

    portfolio_sector_summary = " / ".join(sector_lines) if sector_lines else "섹터 데이터 없음"

    text = get_ai_analyst_feedback(
        vix=live.vix if live.vix is not None else metrics.get("vix", 20.0),
        # metrics 의 베타는 5일치 프레임에서 나와 신뢰할 수 없다(관측치 부족).
        # 클라이언트가 보낸 값이 없으면 None 을 넘겨 '산출 불가'로 처리한다.
        portfolio_beta=(live.portfolio_beta if live.portfolio_beta is not None
                        else metrics.get("portfolio_beta")),
        today_chg_pct=live.today_chg_pct if live.today_chg_pct is not None else metrics.get("today_change_pct", 0.0),
        sector_summary=portfolio_sector_summary,
        is_portfolio_sectors=True,
    )
    result = {"feedback": text, "metrics_snapshot": metrics}
    if not market_open:
        # TTL 은 넉넉히 잡되, 실제 만료는 cache_key 가 바뀌는 시점에 일어난다.
        save_analysis("analyst_feedback", cache_key, result, ttl_hours=96, user_id=uid)
    return {**result, "from_cache": False}


# ── 데일리 브리프 ─────────────────────────────────────────────────────────────

@router.post("/daily-brief")
def daily_brief(
    portfolio: Optional[dict] = None,
    _auth: dict = Depends(ai_feature_user),
    market: str = Depends(market_param),
):
    """오늘의 포트폴리오 브리프 마크다운 생성 (Claude Sonnet)."""
    uid      = _auth["uid"]
    holdings = portfolio or _load_holdings(uid, market=market)
    # 매매 이력은 읽지 않는다. 이 브리프는 자산곡선을 만들지 않고, 취득원가는
    # holdings 의 avg 에서 온다. (예전에는 여기서 _load_trade_log 를 부른 뒤
    # 결과를 쓰지 않고 버렸다 — 요청마다 도는 빈 조회였다.)

    if not holdings:
        raise HTTPException(status_code=400, detail="보유 종목 없음")

    tickers  = [t for t in holdings if t != "CASH"]
    close_df = get_close_df(tickers, period="5d", ttl=60)

    # 가격 데이터 수집
    #
    # 비율만 넘기면 모델이 수량을 곱해 금액을 만들어 내는데, 그 산술이 맞지
    # 않는다 (실측: 1일 손익 +₩10,239 을 +₩688,000 으로 썼다). daily_report
    # 쪽은 pos_val·day_pnl 을 미리 계산해 넘겨서 이 문제가 없다. 같은 방식을
    # 쓴다 — 모델에게 시킬 일이 아니라 여기서 끝낼 일이다.
    price_data = {}
    if not close_df.empty and len(close_df) >= 2:
        cur, prev = close_df.iloc[-1], close_df.iloc[-2]
        for t in tickers:
            if t not in close_df.columns:
                continue
            p  = cur.get(t)
            pp = prev.get(t)
            p  = float(p) if p is not None and p == p else None
            pp = float(pp) if pp is not None and pp == pp else None
            if p is None:
                continue
            qty = float(holdings[t].get("q") or 0)
            avg = float(holdings[t].get("avg") or 0)
            price_data[t] = {
                "price":   round(p, 2),
                # 값이 없으면 0 이 아니라 None. '0.00%' 는 '보합'으로 읽힌다 (§1.3).
                "chg_pct": round((p / pp - 1) * 100, 4) if pp else None,
                "pnl_pct": round((p / avg - 1) * 100, 4) if avg else None,
                "pos_val": round(p * qty, 2),
                "day_pnl": round((p - pp) * qty, 2) if pp is not None else None,
            }

    # 거시지표는 여기서 고르지 않는다. 시장에 맞는 것을 고르는 분기가
    # ai_analysis 에 이미 있고, 여기서 FRED 를 무조건 부르던 탓에 한국
    # 브리프가 연준 금리를 근거로 쓰였다.
    news_items = get_portfolio_news(tickers, max_per=2)

    md = generate_daily_brief(holdings, price_data, news_items, market)
    return {"markdown": md}

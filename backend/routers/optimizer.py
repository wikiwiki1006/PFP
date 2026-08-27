"""
routers/optimizer.py
─────────────────────
포트폴리오 최적화 + 몬테카를로 시뮬레이션 API
"""
from __future__ import annotations

import threading
import uuid
from typing import Optional

import numpy as np
import pandas as pd
from backend.services.auth import current_user, optional_user
from backend.services.job_store import JobStore
from fastapi import Depends, APIRouter, Header, HTTPException
from pydantic import BaseModel

from backend.db.portfolio_repo import get_holdings as db_get_holdings
from backend.services.market_data import get_close_df
from backend.services.portfolio_optimizer import run_ai_optimization
from backend.services.optimizer import (
    optimize_max_sharpe,
    optimize_black_litterman,
    build_regime_views,
    factor_analysis,
    generate_proxy_factors,
)

router = APIRouter(prefix="/api/optimizer", tags=["optimizer"])

# ── AI-Optimize 잡 스토어 (in-process, 재시작 시 초기화) ─────────────────────
# 잡 상태는 DB 에 둔다 — Cloud Run 은 인스턴스를 여러 개 띄우고 세션 고정이 없어서,
# 메모리에 두면 폴링이 다른 인스턴스로 갈 때 잡을 찾지 못한다.
_store = JobStore(kind="optimizer", max_jobs=50)




def _resolve_tickers(explicit: Optional[list[str]], auth: Optional[dict]) -> list[str]:
    """최적화 대상 종목 결정.

    티커를 직접 넘겼다면 개인 데이터가 필요 없으므로 로그인 없이도 계산해 준다
    (공개된 시세만 쓴다). 티커가 없을 때만 "내 포트폴리오"를 뜻하므로 로그인을
    요구한다.
    """
    cleaned = [t.strip().upper() for t in (explicit or []) if t and t.strip()]
    if cleaned:
        return cleaned
    if not auth:
        raise HTTPException(
            status_code=401,
            detail="내 포트폴리오로 최적화하려면 로그인이 필요합니다. "
                   "또는 종목을 직접 입력해 주세요.",
        )
    tickers = [t for t in db_get_holdings(auth["uid"]) if t != "CASH"]
    if not tickers:
        raise HTTPException(status_code=400, detail="보유 종목이 없습니다. 종목을 입력해 주세요.")
    return tickers


def _fetch_returns(tickers: list[str], period: str = "1y") -> pd.DataFrame:
    close_df = get_close_df(tickers, period=period, ttl=600)
    if close_df.empty:
        raise HTTPException(status_code=502, detail="시세 데이터 조회 실패")
    daily_returns = close_df.pct_change().dropna()
    valid = [t for t in tickers if t in daily_returns.columns and daily_returns[t].notna().sum() >= 60]
    if len(valid) < 2:
        raise HTTPException(status_code=400, detail=f"유효한 티커가 2개 미만 (전달: {tickers})")
    return daily_returns[valid]


# ── Pydantic 요청 모델 ─────────────────────────────────────────────────────────

class MaxSharpeRequest(BaseModel):
    tickers:        Optional[list[str]] = None  # None이면 보유 종목 자동 사용
    period:         str = "1y"
    risk_free_rate: float = 0.04
    weight_bounds:  list[float] = [0.0, 1.0]


class BlackLittermanRequest(BaseModel):
    tickers:         Optional[list[str]] = None
    period:          str = "1y"
    regime:          str = "Sideways"   # "Bull" | "Bear" | "Sideways"
    views:           Optional[dict[str, float]] = None  # 직접 지정 시 regime 대신 사용
    view_confidence: float = 0.5
    risk_free_rate:  float = 0.04
    risk_aversion:   float = 2.5


class FactorAnalysisRequest(BaseModel):
    tickers: Optional[list[str]] = None
    weights: Optional[dict[str, float]] = None
    period:  str = "1y"


# ══════════════════════════════════════════════════════════════════════════════
# 최적화 엔드포인트
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/max-sharpe")
def max_sharpe(req: MaxSharpeRequest, _auth: Optional[dict] = Depends(optional_user)):
    """과거 데이터 기반 Max Sharpe Ratio 포트폴리오 최적화."""
    tickers = _resolve_tickers(req.tickers, _auth)

    daily_returns = _fetch_returns(tickers, req.period)
    return optimize_max_sharpe(
        daily_returns,
        risk_free_rate=req.risk_free_rate,
        weight_bounds=tuple(req.weight_bounds),
    )


@router.post("/black-litterman")
def black_litterman(req: BlackLittermanRequest, _auth: Optional[dict] = Depends(optional_user)):
    """Black-Litterman + 시장 국면 시그널 결합 최적화."""
    tickers  = _resolve_tickers(req.tickers, _auth)
    # 시가총액 대용 비중에만 쓰인다. 비로그인이면 균등 비중으로 대체된다.
    holdings = db_get_holdings(_auth["uid"]) if _auth else {}

    daily_returns = _fetch_returns(tickers, req.period)

    # 섹터 맵 (holdings에서 추출)
    sector_map = {t: holdings.get(t, {}).get("sector", "") for t in tickers}

    # View 결정: 직접 입력 우선, 없으면 regime 기반 자동 생성
    views = req.views
    if not views:
        views = build_regime_views(
            list(daily_returns.columns), sector_map, req.regime
        )

    return optimize_black_litterman(
        daily_returns,
        views=views,
        view_confidence=req.view_confidence,
        risk_free_rate=req.risk_free_rate,
        risk_aversion=req.risk_aversion,
    )


@router.post("/factor-analysis")
def run_factor_analysis(req: FactorAnalysisRequest, _auth: Optional[dict] = Depends(optional_user)):
    """Fama-French 스타일 4팩터 분석 (대용 팩터 자동 생성)."""
    tickers = _resolve_tickers(req.tickers, _auth)

    daily_returns = _fetch_returns(tickers, req.period)

    # 보유 비중 결정
    if req.weights:
        w = np.array([req.weights.get(t, 1.0 / len(daily_returns.columns))
                      for t in daily_returns.columns])
    else:
        w = np.full(len(daily_returns.columns), 1.0 / len(daily_returns.columns))
    w /= w.sum()

    port_returns  = (daily_returns.values @ w)
    market_ret    = daily_returns.mean(axis=1).values
    factor_df     = generate_proxy_factors(market_ret, len(port_returns))

    return factor_analysis(port_returns, factor_df)



# ══════════════════════════════════════════════════════════════════════════════
# AI Portfolio Optimizer (Black-Litterman + GPT Views)
# ══════════════════════════════════════════════════════════════════════════════

class AIOptimizeRequest(BaseModel):
    tickers:              Optional[list[str]] = None   # None → 보유 종목 자동 사용
    period:               str   = "1y"                 # "3mo" | "6mo" | "1y" | "2y"
    target_return:        float = 0.10                 # 목표 수익률 (Efficient Return 모드)
    risk_free_rate:       float = 0.04
    holding_period_years: float = 1.0                  # AI 뷰 수익률 예측 기간
    weight_bounds:        list[float] = [0.0, 1.0]     # [최소, 최대] 비중


@router.post("/ai-optimize")
def ai_optimize(req: AIOptimizeRequest, _auth: Optional[dict] = Depends(optional_user)):
    """동기 최적화 (하위 호환 유지)."""
    tickers = _resolve_tickers(req.tickers, _auth)
    wb = tuple(req.weight_bounds) if len(req.weight_bounds) == 2 else (0.0, 1.0)
    try:
        return run_ai_optimization(
            tickers=tickers, period=req.period,
            target_return=req.target_return, risk_free_rate=req.risk_free_rate,
            holding_period_years=req.holding_period_years, weight_bounds=wb,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"최적화 오류: {e}")


# ── 잡 기반 비동기 최적화 ─────────────────────────────────────────────────────

@router.post("/ai-optimize-job")
def start_ai_optimize_job(
    req: AIOptimizeRequest,
    _auth: Optional[dict] = Depends(optional_user),
):
    """비동기 최적화 잡 시작 → job_id 반환. 완료 여부는 GET으로 폴링."""
    tickers = _resolve_tickers(req.tickers, _auth)
    wb = tuple(req.weight_bounds) if len(req.weight_bounds) == 2 else (0.0, 1.0)

    job_id = str(uuid.uuid4())
    # 비로그인 잡은 사용자가 직접 입력한 공개 티커만 다루므로 소유자가 없다.
    _owner = _auth["uid"] if _auth else None
    _store.set(job_id, {"status": "running", "stage": 0, "stage_text": "준비 중..."},
               owner=_owner)

    def _on_stage(n: int, text: str) -> None:
        # 진행 단계는 사용자에게 보여줄 뿐이라, 이미 끝난 잡이면 굳이 덮지 않는다.
        _store.update_if(job_id, "running",
                         {"status": "running", "stage": n, "stage_text": text})

    def _run() -> None:
        try:
            result = run_ai_optimization(
                tickers=tickers, period=req.period,
                target_return=req.target_return, risk_free_rate=req.risk_free_rate,
                holding_period_years=req.holding_period_years, weight_bounds=wb,
                on_stage=_on_stage,
            )
            _store.update_if(job_id, "running",
                             {"status": "done", "stage": 3, "stage_text": "완료", "result": result})
        except ValueError as e:
            _store.update_if(job_id, "running",
                             {"status": "error", "stage": 0, "stage_text": "오류", "detail": str(e)})
        except Exception as e:
            _store.update_if(job_id, "running",
                             {"status": "error", "stage": 0, "stage_text": "오류",
                              "detail": f"최적화 오류: {e}"})

    threading.Thread(target=_run, daemon=True).start()
    return {"job_id": job_id}


@router.get("/ai-optimize-job/{job_id}")
def get_ai_optimize_job(job_id: str, _auth: Optional[dict] = Depends(optional_user)):
    """잡 상태 조회 (본인 잡 또는 비로그인 잡)."""
    # 남의 잡이면 존재 여부조차 알리지 않는다 (get 이 None 을 돌려준다).
    job = _store.get(job_id, owner=(_auth or {}).get("uid"))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.delete("/ai-optimize-job/{job_id}")
def cancel_ai_optimize_job(job_id: str, _auth: Optional[dict] = Depends(optional_user)):
    """잡 취소 (본인 잡 또는 비로그인 잡)."""
    _store.cancel(job_id, owner=(_auth or {}).get("uid"))
    return {"ok": True}

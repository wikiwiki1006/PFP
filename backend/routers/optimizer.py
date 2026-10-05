"""
routers/optimizer.py
─────────────────────
포트폴리오 최적화 + 몬테카를로 시뮬레이션 API
"""
from __future__ import annotations

import logging
import re
import threading
import uuid
from typing import Optional

import numpy as np
import pandas as pd
from backend.routers._errors import log_hidden, user_sentence
from backend.services.auth import current_user, optional_user
from backend.services.job_store import JobStore, ANONYMOUS, Owner
from fastapi import Depends, APIRouter, Header, HTTPException

from backend.services.markets import market_param
from pydantic import BaseModel

from backend.services.market_data import get_close_df
from backend.services.portfolio_optimizer import run_ai_optimization
from backend.services.optimizer import (
    optimize_max_sharpe,
    optimize_black_litterman,
    build_regime_views,
)

router = APIRouter(prefix="/api/optimizer", tags=["optimizer"])

# 이 모듈에는 로거가 없었다 — 최적화 실패는 `detail` 로 응답에만 실리고 로그에는
# 남지 않았다 (§1.3: 로거가 없으면 먼저 만든다).
logger = logging.getLogger(__name__)

_OPTIMIZE_FAILED = "최적화 중 서버 오류가 났습니다. 잠시 후 다시 시도해 주세요."

# run_ai_optimization 이 **사용자용 문장**으로 올리는 ValueError
# (backend/services/portfolio_optimizer.py). `except ValueError` 는 numpy·pandas·
# pypfopt 의 ValueError 도 같이 잡으므로 타입만으로는 사용자 문장인지 알 수 없다.
_OPTIMIZE_USER_REASONS = (
    re.compile(r"가격 데이터 조회 실패 — 종목 티커를 확인해주세요\."),
    re.compile(r"충분한 데이터를 가진 종목이 2개 미만입니다"),
)


def _optimize_failure(e: Exception, what: str) -> tuple[int, str]:
    """실패를 (상태 코드, 사용자에게 보낼 문장) 으로. 사용자 문장이 아니면 로그로만 남긴다."""
    reason = user_sentence(e, _OPTIMIZE_USER_REASONS) if isinstance(e, ValueError) else None
    if reason is not None:
        return 400, reason
    log_hidden(logger, what, e)
    return 500, _OPTIMIZE_FAILED

# ── AI-Optimize 잡 스토어 (in-process, 재시작 시 초기화) ─────────────────────
# 잡 상태는 DB 에 둔다 — Cloud Run 은 인스턴스를 여러 개 띄우고 세션 고정이 없어서,
# 메모리에 두면 폴링이 다른 인스턴스로 갈 때 잡을 찾지 못한다.
_store = JobStore(kind="optimizer", max_jobs=50)


def _job_owner(auth: Optional[dict]) -> Owner:
    """조회·취소에 넘길 소유자 값.

    **이 엔드포인트는 `optional_user` 다.** 비로그인에 `None` 을 넘기면
    job_store 가 그것을 "소유자 검사를 하지 마" 로 읽어 남의 잡까지 보여
    준다. 한 값이 두 뜻을 갖고 있었고, 익명 호출자가 그 틈으로 들어갔다.

    잡 **생성** 쪽은 계속 `None` 이다 (아래 `_owner`). 거기서는 "이 잡에
    소유자가 없다" 라는 뜻이고, 그건 비로그인 최적화 잡의 실제 상태다.
    읽는 쪽의 `None`(검사 생략)과 뜻이 다르므로 같이 두지 않는다.
    """
    return auth["uid"] if auth else ANONYMOUS




def _resolve_tickers(explicit: Optional[list[str]]) -> list[str]:
    """최적화 대상 종목 결정 — **입력한 티커만 쓴다.**

    예전에는 티커를 비우면 로그인 사용자의 `holdings` 를 읽어 "내 포트폴리오"를
    최적화했다. 그 경로를 없앴다. 이유는 성능이나 취향이 아니라 업태다 —
    보유 종목을 입력으로 받는 순간 같은 요청이 사용자마다 다른 답을 내고,
    그것이 자본시장법이 말하는 '개별성' 이다. 개별성이 붙은 투자판단 제공은
    유사투자자문업(제101조)이 아니라 투자자문업(제6조 제7항)이라 금융위 등록
    대상이 된다.

    이제 이 함수는 개인 데이터를 전혀 읽지 않는다. 같은 티커 목록이면 누가
    넣어도 같은 결과가 나온다 — 로그인 여부와도 무관하다.
    """
    cleaned = [t.strip().upper() for t in (explicit or []) if t and t.strip()]
    if cleaned:
        return cleaned
    raise HTTPException(
        status_code=400,
        detail="최적화할 종목을 직접 입력해 주세요 (2개 이상).",
    )


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
    tickers:        Optional[list[str]] = None  # 필수 — 비면 400 (보유 종목 자동 사용은 없앴다)
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


# ══════════════════════════════════════════════════════════════════════════════
# 최적화 엔드포인트
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/max-sharpe")
def max_sharpe(req: MaxSharpeRequest, _auth: Optional[dict] = Depends(optional_user), market: str = Depends(market_param)):
    """과거 데이터 기반 Max Sharpe Ratio 포트폴리오 최적화."""
    tickers = _resolve_tickers(req.tickers)

    daily_returns = _fetch_returns(tickers, req.period)
    return optimize_max_sharpe(
        daily_returns,
        risk_free_rate=req.risk_free_rate,
        weight_bounds=tuple(req.weight_bounds),
    )


@router.post("/black-litterman")
def black_litterman(req: BlackLittermanRequest, _auth: Optional[dict] = Depends(optional_user), market: str = Depends(market_param)):
    """Black-Litterman + 시장 국면 시그널 결합 최적화."""
    tickers  = _resolve_tickers(req.tickers)

    daily_returns = _fetch_returns(tickers, req.period)

    # 섹터는 **공개 출처**에서 읽는다 (services/sector_lookup).
    #
    # 예전에는 `holdings` 의 sector 컬럼에서 뽑았다. 그러면 같은 티커 목록을
    # 넣어도 보유 구성이 다른 사용자끼리 sector_map 이 달라지고 → 국면 뷰가
    # 달라지고 → 최종 비중이 달라졌다. 티커를 직접 입력해도 결과가 그 사람의
    # 계좌에 의존했다는 뜻이라, 개별성이 여기로 새고 있었다.
    # 섹터는 종목의 공개 속성이므로 개인 데이터에서 읽을 이유가 없다.
    from backend.services.sector_lookup import sector_map as public_sector_map
    sector_map = public_sector_map(tickers, market=market)

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


# ══════════════════════════════════════════════════════════════════════════════
# AI Portfolio Optimizer (Black-Litterman + Perplexity Views)
# ══════════════════════════════════════════════════════════════════════════════

class AIOptimizeRequest(BaseModel):
    tickers:              Optional[list[str]] = None   # 필수 — 비면 400
    period:               str   = "1y"                 # "3mo" | "6mo" | "1y" | "2y"
    target_return:        float = 0.10                 # 목표 수익률 (Efficient Return 모드)
    risk_free_rate:       float = 0.04
    holding_period_years: float = 1.0                  # AI 뷰 수익률 예측 기간
    weight_bounds:        list[float] = [0.0, 1.0]     # [최소, 최대] 비중


@router.post("/ai-optimize")
def ai_optimize(req: AIOptimizeRequest, _auth: Optional[dict] = Depends(optional_user), market: str = Depends(market_param)):
    """동기 최적화 (하위 호환 유지)."""
    tickers = _resolve_tickers(req.tickers)
    wb = tuple(req.weight_bounds) if len(req.weight_bounds) == 2 else (0.0, 1.0)
    try:
        return run_ai_optimization(
            tickers=tickers, period=req.period,
            target_return=req.target_return, risk_free_rate=req.risk_free_rate,
            holding_period_years=req.holding_period_years, weight_bounds=wb,
            market=market,
        )
    except Exception as e:
        status, message = _optimize_failure(e, f"AI 최적화 (동기, {market})")
        raise HTTPException(status_code=status, detail=message)


# ── 잡 기반 비동기 최적화 ─────────────────────────────────────────────────────

@router.post("/ai-optimize-job")
def start_ai_optimize_job(
    req: AIOptimizeRequest,
    _auth: Optional[dict] = Depends(optional_user),
    market: str = Depends(market_param),
):
    """비동기 최적화 잡 시작 → job_id 반환. 완료 여부는 GET으로 폴링."""
    tickers = _resolve_tickers(req.tickers)
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
                on_stage=_on_stage, market=market,
            )
            _store.update_if(job_id, "running",
                             {"status": "done", "stage": 3, "stage_text": "완료", "result": result})
        except Exception as e:
            # 잡 상태의 detail 은 화면(최적화 페이지)이 그대로 보여 준다.
            _, message = _optimize_failure(e, f"AI 최적화 잡 ({market})")
            _store.update_if(job_id, "running",
                             {"status": "error", "stage": 0, "stage_text": "오류", "detail": message})

    threading.Thread(target=_run, daemon=True).start()
    return {"job_id": job_id}


@router.get("/ai-optimize-job/{job_id}")
def get_ai_optimize_job(job_id: str, _auth: Optional[dict] = Depends(optional_user), market: str = Depends(market_param)):
    """잡 상태 조회 (본인 잡 또는 비로그인 잡)."""
    # 남의 잡이면 존재 여부조차 알리지 않는다 (get 이 None 을 돌려준다).
    #
    # 비로그인이면 `None` 이 아니라 `ANONYMOUS` 다. `(_auth or {}).get("uid")`
    # 는 비로그인에 `None` 을 주는데, job_store 에서 `None` 은 **"소유자 검사를
    # 하지 마"** 라는 뜻이라 익명 호출자가 검사 면제 경로를 탔다 — 위 주석이
    # 로그인 호출자에게만 참이었다.
    job = _store.get(job_id, owner=_job_owner(_auth))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.delete("/ai-optimize-job/{job_id}")
def cancel_ai_optimize_job(job_id: str, _auth: Optional[dict] = Depends(optional_user), market: str = Depends(market_param)):
    """잡 취소 (본인 잡 또는 비로그인 잡)."""
    # 조회와 같은 판정을 지난다 — 읽을 수 없는 잡은 취소도 못 한다.
    _store.cancel(job_id, owner=_job_owner(_auth))
    return {"ok": True}

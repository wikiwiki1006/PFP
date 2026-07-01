"""
backend/main.py
────────────────
FastAPI 앱 진입점.
실행: uvicorn backend.main:app --reload --port 8000
"""
from __future__ import annotations

import json
import logging
import math
import threading
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse


def _clean_floats(obj: Any) -> Any:
    """NaN / Inf float 값을 재귀적으로 None 으로 교체 (JSON 직렬화 오류 방지)."""
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _clean_floats(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        cleaned = [_clean_floats(v) for v in obj]
        return cleaned if isinstance(obj, list) else tuple(cleaned)
    return obj


class SafeJSONResponse(JSONResponse):
    """NaN/Inf 를 null 로 변환하는 JSON 응답 클래스."""
    def render(self, content: Any) -> bytes:
        return json.dumps(
            _clean_floats(content),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")

from backend.routers import portfolio, market, macro, signals, optimizer, reports

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# ── OS fd 한도 상향 (yfinance 대량 다운로드시 Too many open files 방지) ─────────
try:
    import resource as _resource
    _soft, _hard = _resource.getrlimit(_resource.RLIMIT_NOFILE)
    _target = min(8192, _hard)
    if _target > _soft:
        _resource.setrlimit(_resource.RLIMIT_NOFILE, (_target, _hard))
        logger.info(f"fd 한도 상향: {_soft} → {_target}")
except Exception as _e:
    logger.warning(f"fd 한도 상향 실패: {_e}")

app = FastAPI(
    title="Personal Financial Platform API",
    description="포트폴리오 관리 · 시장 데이터 · AI 거시경제 분석 · 매매 신호",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    default_response_class=SafeJSONResponse,
)

# ── CORS ──────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:8501",
        "http://127.0.0.1:8501",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 라우터 등록 ────────────────────────────────────────────────────────────────
app.include_router(portfolio.router)
app.include_router(market.router)
app.include_router(macro.router)
app.include_router(signals.router)
app.include_router(optimizer.router)
app.include_router(reports.router)


# ── 시작 이벤트 ────────────────────────────────────────────────────────────────
@app.on_event("startup")
def on_startup():
    """DB 연결 풀 초기화 → 스키마 생성 → 스케줄러 시작 → 초기 프리패치."""
    from backend.db import init_pool
    from backend.db.schema import init_schema
    from backend.db import scheduler

    db_ok = init_pool()
    if db_ok:
        init_schema()
        # 백그라운드 스레드로 공통 티커 프리패치 (앱 시작을 블로킹하지 않음)
        threading.Thread(target=_prefetch_common_tickers, daemon=True).start()
        # 1분 주기 공통 데이터 스케줄러 시작
        scheduler.start()
    else:
        logger.warning("DB 미연결 — 파일 폴백 모드로 동작합니다.")


def _prefetch_common_tickers():
    """
    자주 사용되는 지수·ETF·종목 데이터를 DB에 미리 수집.
    stale 티커만 yfinance 호출하므로 반복 실행 시 빠름.
    """
    from backend.services.market_data import ALWAYS_FETCH, SECTOR_ETF_TICKERS
    from backend.db.market_cache import prefetch_tickers

    all_common = list(set(ALWAYS_FETCH + SECTOR_ETF_TICKERS))
    logger.info(f"백그라운드 프리패치 시작: {len(all_common)}개 티커")
    prefetch_tickers(all_common, period="2y")
    logger.info("백그라운드 프리패치 완료")


# ── 종료 이벤트 ────────────────────────────────────────────────────────────────
@app.on_event("shutdown")
def on_shutdown():
    from backend.db import close_pool
    from backend.db import scheduler

    scheduler.stop()
    close_pool()
    logger.info("스케줄러 정지 및 DB 연결 풀 종료")


# ── 헬스체크 ──────────────────────────────────────────────────────────────────
@app.get("/")
def root():
    from backend.db import is_available
    return {
        "service": "Personal Financial Platform API",
        "version": "2.0.0",
        "db":      "connected" if is_available() else "file-fallback",
        "docs":    "/docs",
    }


@app.get("/health")
def health():
    from backend.db import is_available
    from backend.db.market_cache import is_snapshot_fresh
    return {
        "status":          "ok",
        "db":              "ok" if is_available() else "unavailable",
        "snapshot_fresh":  is_snapshot_fresh(max_age_seconds=90),
    }

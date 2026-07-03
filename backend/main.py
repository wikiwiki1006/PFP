"""
backend/main.py
────────────────
FastAPI 앱 진입점.
개발: uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
배포: APP_ENV=production ALLOWED_ORIGINS=https://yourdomain.com uvicorn backend.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import json
import logging
import math
import os
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# ── 환경 설정 ─────────────────────────────────────────────────────────────────
APP_ENV = os.getenv("APP_ENV", "development")  # "development" | "production"
_is_prod = APP_ENV == "production"


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
except ImportError:
    pass  # Windows — resource 모듈 없음, 정상
except Exception as _e:
    logger.warning(f"fd 한도 상향 실패: {_e}")

app = FastAPI(
    title="Personal Financial Platform API",
    description="포트폴리오 관리 · 시장 데이터 · AI 거시경제 분석 · 매매 신호",
    version="2.0.0",
    # 프로덕션에서는 API 문서 비공개
    docs_url=None if _is_prod else "/docs",
    redoc_url=None if _is_prod else "/redoc",
    openapi_url=None if _is_prod else "/openapi.json",
    default_response_class=SafeJSONResponse,
)

# ── CORS ──────────────────────────────────────────────────────────────────────
# 개발: 모든 Origin 허용 (로컬 + LAN 모두 접근 가능)
# 프로덕션: ALLOWED_ORIGINS 환경변수로 허용 Origin 명시 (쉼표 구분)
#   예) ALLOWED_ORIGINS=https://mypfp.com,https://www.mypfp.com
_raw_origins = os.getenv("ALLOWED_ORIGINS", "")
_cors_origins: list[str] = (
    [o.strip() for o in _raw_origins.split(",") if o.strip()]
    if _is_prod and _raw_origins
    else ["*"]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-User-ID"],
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
        # KST 03:00 수집을 놓쳤으면 백그라운드에서 즉시 SP500 전 종목 수집 + pairs 사전계산
        scheduler.trigger_sp500_if_missed()
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


# ── 헬스체크 ─────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    from backend.db import is_available
    from backend.db.market_cache import is_snapshot_fresh
    return {
        "status":          "ok",
        "db":              "ok" if is_available() else "unavailable",
        "snapshot_fresh":  is_snapshot_fresh(max_age_seconds=90),
    }


# ── 프론트엔드 정적 파일 서빙 (SPA 지원) ─────────────────────────────────────────
# `npm run build` 후 frontend/dist 가 있으면 활성화.
# API 라우터가 먼저 등록됐으므로 /api/* 는 이 블록에 도달하지 않음.
_frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if _frontend_dist.exists():
    try:
        from fastapi.staticfiles import StaticFiles
        from fastapi.responses import FileResponse as _FileResponse

        # Vite 번들 에셋 (/assets/*)
        _assets = _frontend_dist / "assets"
        if _assets.exists():
            app.mount("/assets", StaticFiles(directory=str(_assets)), name="vite-assets")

        # 루트 정적 파일 (favicon.svg 등) — index.html 제외
        for _sf in _frontend_dist.iterdir():
            if _sf.is_file() and _sf.name != "index.html":
                @app.get(f"/{_sf.name}", include_in_schema=False)
                def _serve_root_file(p=_sf):
                    return _FileResponse(str(p))

        # SPA catch-all: React Router가 처리하는 모든 경로에 index.html 반환
        # 반드시 마지막에 등록 — API 라우트가 먼저 매칭됨
        @app.get("/{full_path:path}", include_in_schema=False)
        def _serve_spa(full_path: str):
            return _FileResponse(str(_frontend_dist / "index.html"))

        logger.info(f"프론트엔드 정적 파일 서빙 활성화: {_frontend_dist}")
    except ImportError:
        logger.warning("aiofiles 미설치 — pip install aiofiles")


# ── 직접 실행 시 uvicorn 내장 서버 ────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=8000,
        reload=APP_ENV != "production",
        log_level="info",
    )

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

from fastapi import FastAPI, HTTPException
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

from backend.db import DBBusy
from backend.routers import internal, admin, portfolio, market, macro, signals, optimizer, reports, ticker, auth

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
    title="ZOOPZOOP API",
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

# ── API 응답 캐시 금지 ────────────────────────────────────────────────────────
#
# Firebase Hosting 이 /api/** 응답을 기본값으로 10분 캐시한다. 그래서 코드를
# 고쳐 배포해도 옛 응답이 그대로 나갔고, 실제로 삼성전자 조회가 고쳐진 뒤에도
# 404 가 계속 반환됐다(x-cache: HIT). 더 나쁜 것은 사용자별 데이터가 섞일 수
# 있다는 점이다 — 포트폴리오 응답이 캐시되면 다른 사람에게 갈 수 있다.
#
# firebase.json 에도 같은 규칙을 뒀지만, 앞단 설정이 바뀌어도 새지 않도록
# 서버가 직접 붙인다.
@app.middleware("http")
async def _no_cache_api(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


# ── 라우터 등록 ────────────────────────────────────────────────────────────────
app.include_router(internal.router)
app.include_router(admin.router)
app.include_router(admin.public_router)
app.include_router(auth.router)
app.include_router(portfolio.router)
app.include_router(market.router)
app.include_router(macro.router)
app.include_router(signals.router)
app.include_router(optimizer.router)
app.include_router(reports.router)
app.include_router(ticker.router)


# ── DB 가 바쁠 때는 503 ────────────────────────────────────────────────────────
#
# backend/db 는 fastapi 를 import 하지 않는다 (services/auth.py 가 유일한 경계다).
# 그래서 db 계층은 HTTPException 대신 도메인 예외를 올린다:
#
#   DBBusy ├─ PoolExhausted        커넥션 부족
#          └─ WriteLockUnavailable 쓰기 락 획득 실패
#
# 둘 다 "지금은 안 되지만 다시 시도하면 된다" 이므로 503 이다. 이 핸들러가
# 없으면 500 + "Internal Server Error" 로 나가는데, 그건 사용자에게 우리가
# 망가졌다고만 말하고 재시도해도 되는지를 알려주지 않는다. 프론트는 detail 을
# 그대로 띄우므로 여기서 준 문장이 화면에 보인다.
@app.exception_handler(DBBusy)
async def _db_busy_handler(request, exc: DBBusy):
    logger.warning(f"DB 사용량 초과로 요청 거절: {request.url.path} — {exc}")
    return JSONResponse(status_code=503, content={"detail": str(exc)})


# ── 시작 이벤트 ────────────────────────────────────────────────────────────────
@app.on_event("startup")
def on_startup():
    """DB 연결 풀 초기화 → 스키마 생성 → 스케줄러 시작 → 초기 프리패치."""
    from backend.db import init_pool
    from backend.db.schema import init_schema
    from backend.db import scheduler

    db_ok = init_pool()
    if not db_ok:
        logger.warning("DB 미연결 — 파일 폴백 모드로 동작합니다.")
        return

    # 실패해도 기동은 계속하지만(가용성), 그 사실이 로그에 분명히 남아야
    # 한다. 스키마가 안 맞으면 저장 경로가 통째로 죽고 증상은 한참 뒤에
    # 나타난다 — 원인과 증상이 멀어서 신호가 없으면 아무도 못 찾는다.
    if not init_schema():
        logger.error(
            "DB 스키마 적용 실패 — 새 코드가 옛 스키마 위에서 돕니다. "
            "저장 경로가 실패할 수 있습니다 (CLAUDE.md §7.4)."
        )

    # 완료된 잡은 DB 에 계속 쌓인다. 폴링은 길어야 몇 분이면 끝나므로
    # 하루 지난 것은 지운다. 기동 때 한 번이면 충분하다 — 인스턴스가 자주
    # 교체되는 환경이라 별도 스케줄러보다 이쪽이 확실하다.
    try:
        from backend.routers.reports import _store as _reports_store
        n = _reports_store.purge_stale()
        if n:
            logger.info(f"오래된 잡 {n}건 정리")
    except Exception as e:
        logger.warning(f"잡 정리 건너뜀: {e}")

    # 서버리스(Cloud Run 등)에서는 요청이 없으면 인스턴스가 0으로 내려가므로
    # 백그라운드 스레드 스케줄러가 신뢰성 있게 돌지 않는다. 그런 환경에서는
    # ENABLE_SCHEDULER=false 로 꺼두고, 시세는 온디맨드 경로로만 수집한다.
    # 기본값은 false 다. 이 리포에서 true 로 세팅하는 실행 경로가 하나도 없다 —
    # Dockerfile 은 false, dev.sh 는 --scheduler 를 줬을 때만 true. 그래서 기본
    # true 는 아무도 쓰지 않으면서 환경변수를 주지 않은 사람만 함정에 빠뜨렸다:
    # start.bat 이나 문서의 uvicorn 예시로 띄우면 프리패치·SP500 수집·1분
    # 스케줄러가 조용히 돌았고, 병렬 창 다섯이면 그게 다섯 배가 된다.
    # 켜는 쪽을 명시하게 한다.
    if os.getenv("ENABLE_SCHEDULER", "false").lower() not in ("1", "true", "yes"):
        logger.info("ENABLE_SCHEDULER=false — 백그라운드 스케줄러/프리패치 비활성화")
        return

    # 백그라운드 스레드로 공통 티커 프리패치 (앱 시작을 블로킹하지 않음)
    threading.Thread(target=_prefetch_common_tickers, daemon=True).start()
    # 수집을 놓쳤으면 백그라운드에서 즉시 SP500 전 종목 수집 + pairs 사전계산
    scheduler.trigger_sp500_if_missed()
    # 1분 주기 공통 데이터 스케줄러 시작
    scheduler.start()


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
            # /api/* 는 SPA 경로가 아니다. 여기까지 왔다는 건 그런 엔드포인트가
            # 없다는 뜻이므로, index.html 대신 404 JSON 을 돌려준다.
            # (API 클라이언트가 HTML 을 받아 파싱에 실패하면 원인 파악이 어렵다.)
            if full_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="존재하지 않는 API 경로입니다.")
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

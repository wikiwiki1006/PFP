"""
backend/main.py
────────────────
FastAPI 앱 진입점.
실행: uvicorn backend.main:app --reload --port 8000
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.routers import portfolio, market, macro, signals, optimizer

app = FastAPI(
    title="Personal Financial Platform API",
    description="포트폴리오 관리 · 시장 데이터 · AI 거시경제 분석 · 매매 신호",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── CORS (React dev server + Streamlit 모두 허용) ──────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",   # React dev server
        "http://localhost:5173",   # Vite dev server
        "http://localhost:8501",   # Streamlit
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


@app.get("/")
def root():
    return {
        "service": "Personal Financial Platform API",
        "version": "1.0.0",
        "docs":    "/docs",
        "endpoints": {
            "portfolio": "/api/portfolio",
            "market":    "/api/market",
            "macro":     "/api/macro",
            "signals":   "/api/signals",
            "optimizer": "/api/optimizer",
        },
    }


@app.get("/health")
def health():
    return {"status": "ok"}

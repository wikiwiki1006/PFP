"""
backend/db/schema.py
──────────────────────
테이블 DDL 정의 + 최초 실행 시 JSON → DB 마이그레이션.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from backend.db import get_conn, is_available

logger = logging.getLogger(__name__)

_DDL = """
-- 사용자 테이블
CREATE TABLE IF NOT EXISTS users (
    id          TEXT PRIMARY KEY,
    name        TEXT,
    email       TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
INSERT INTO users(id, name) VALUES ('default', 'Default User')
ON CONFLICT (id) DO NOTHING;

-- 보유 종목 (사용자별)
CREATE TABLE IF NOT EXISTS holdings (
    user_id     TEXT NOT NULL DEFAULT 'default',
    ticker      TEXT NOT NULL,
    qty         DOUBLE PRECISION NOT NULL DEFAULT 0,
    avg_cost    DOUBLE PRECISION NOT NULL DEFAULT 0,
    sector      TEXT NOT NULL DEFAULT 'Other',
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (user_id, ticker)
);

-- 거래 이력 (사용자별)
CREATE TABLE IF NOT EXISTS trade_log (
    id          SERIAL PRIMARY KEY,
    user_id     TEXT NOT NULL DEFAULT 'default',
    trade_date  DATE NOT NULL,
    ticker      TEXT NOT NULL,
    trade_type  TEXT NOT NULL,
    qty         DOUBLE PRECISION NOT NULL,
    price       DOUBLE PRECISION,
    memo        TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_trade_log_user_date
    ON trade_log(user_id, trade_date DESC);

-- 시장 가격 캐시 (일별 종가) — 공통 데이터
CREATE TABLE IF NOT EXISTS market_prices (
    ticker      TEXT NOT NULL,
    price_date  DATE NOT NULL,
    close_price DOUBLE PRECISION NOT NULL,
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (ticker, price_date)
);
CREATE INDEX IF NOT EXISTS idx_market_prices_ticker
    ON market_prices(ticker, price_date DESC);

-- 실시간 시장 스냅샷 (1분마다 갱신) — 공통 데이터
CREATE TABLE IF NOT EXISTS market_snapshot (
    ticker          TEXT PRIMARY KEY,
    price           DOUBLE PRECISION,
    change_1d       DOUBLE PRECISION,
    change_1d_pct   DOUBLE PRECISION,
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- 공통 데이터 캐시 (macro, doom_radar, sector 등 유형별 TTL)
CREATE TABLE IF NOT EXISTS common_cache (
    cache_type  TEXT PRIMARY KEY,
    data        JSONB NOT NULL,
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    expires_at  TIMESTAMPTZ
);

-- 생성된 레포트 (사용자별)
CREATE TABLE IF NOT EXISTS reports (
    id          SERIAL PRIMARY KEY,
    user_id     TEXT NOT NULL DEFAULT 'default',
    report_type TEXT NOT NULL,
    filename    TEXT NOT NULL UNIQUE,
    content     TEXT NOT NULL,
    metadata    JSONB DEFAULT '{}',
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_reports_user_type
    ON reports(user_id, report_type, created_at DESC);

-- AI 분석 결과 캐시 (사용자별)
CREATE TABLE IF NOT EXISTS analysis_cache (
    id              SERIAL PRIMARY KEY,
    user_id         TEXT NOT NULL DEFAULT 'default',
    analysis_type   TEXT NOT NULL,
    cache_key       TEXT NOT NULL,
    result          JSONB NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    expires_at      TIMESTAMPTZ,
    UNIQUE(user_id, analysis_type, cache_key)
);
CREATE INDEX IF NOT EXISTS idx_analysis_cache_lookup
    ON analysis_cache(user_id, analysis_type, cache_key);
"""


def init_schema():
    """DDL 실행 후 기존 JSON 데이터 마이그레이션 (최초 1회)."""
    if not is_available():
        return
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(_DDL)
        logger.info("DB 스키마 초기화 완료")
        _migrate_json()
    except Exception as e:
        logger.error(f"스키마 초기화 오류: {e}")


def _migrate_json():
    """pfp/data/*.json → DB 마이그레이션 (이미 데이터 있으면 스킵)."""
    from backend.db import execute

    try:
        row = execute(
            "SELECT COUNT(*) AS cnt FROM holdings WHERE user_id='default'",
            fetch="one",
        )
        if row and row["cnt"] > 0:
            return
    except Exception:
        return

    data_dir = Path(__file__).parent.parent.parent / "pfp" / "data"

    # holdings.json
    holdings_file = data_dir / "holdings.json"
    if holdings_file.exists():
        try:
            raw = json.loads(holdings_file.read_text())
            holdings = raw.get("my_holdings", raw)
            with get_conn() as conn:
                with conn.cursor() as cur:
                    for ticker, info in holdings.items():
                        cur.execute(
                            """INSERT INTO holdings(user_id, ticker, qty, avg_cost, sector)
                               VALUES(%s,%s,%s,%s,%s)
                               ON CONFLICT(user_id, ticker) DO UPDATE
                               SET qty=EXCLUDED.qty, avg_cost=EXCLUDED.avg_cost,
                                   sector=EXCLUDED.sector""",
                            ("default", ticker,
                             float(info.get("q", 0)),
                             float(info.get("avg", 0)),
                             info.get("sector", "Other")),
                        )
            logger.info(f"holdings.json → DB 마이그레이션 완료 ({len(holdings)}개 종목)")
        except Exception as e:
            logger.warning(f"holdings 마이그레이션 실패: {e}")

    # trade_log.json
    trade_file = data_dir / "trade_log.json"
    if trade_file.exists():
        try:
            trades = json.loads(trade_file.read_text())
            with get_conn() as conn:
                with conn.cursor() as cur:
                    for t in trades:
                        cur.execute(
                            """INSERT INTO trade_log
                                   (user_id, trade_date, ticker, trade_type, qty, price, memo)
                               VALUES(%s,%s,%s,%s,%s,%s,%s)
                               ON CONFLICT DO NOTHING""",
                            ("default",
                             t.get("date"),
                             t.get("ticker"),
                             t.get("type"),
                             float(t.get("q", 0)),
                             t.get("price"),
                             t.get("memo")),
                        )
            logger.info(f"trade_log.json → DB 마이그레이션 완료 ({len(trades)}건)")
        except Exception as e:
            logger.warning(f"trade_log 마이그레이션 실패: {e}")

    # outputs/*.md → reports 테이블
    outputs_dir = Path(__file__).parent.parent.parent / "outputs"
    if outputs_dir.exists():
        md_files = list(outputs_dir.glob("*.md"))
        migrated = 0
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    for f in md_files:
                        rtype = (
                            "daily_brief" if f.name.startswith("daily_brief") else
                            "industry_research" if "industry" in f.name else
                            "equity_research"
                        )
                        try:
                            cur.execute(
                                """INSERT INTO reports(user_id, report_type, filename, content)
                                   VALUES(%s,%s,%s,%s)
                                   ON CONFLICT(filename) DO NOTHING""",
                                ("default", rtype, f.name,
                                 f.read_text(encoding="utf-8")),
                            )
                            migrated += 1
                        except Exception:
                            pass
            if migrated:
                logger.info(f"outputs/*.md → DB 마이그레이션 완료 ({migrated}개)")
        except Exception as e:
            logger.warning(f"reports 마이그레이션 실패: {e}")

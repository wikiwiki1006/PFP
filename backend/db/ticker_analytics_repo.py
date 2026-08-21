"""
db/ticker_analytics_repo.py
───────────────────────────
종목 분석 결과의 일 단위 공용 캐시.

퀀트 스코어·패닉 점수·국면·VaR·성과는 **일봉 기반**이라 하루에 한 번만 값이 바뀐다.
그런데 매 조회마다 yfinance history + info 를 받아 다시 계산하고 있었다(종목당 2초+).
최초 호출자가 계산해 DB 에 저장하면, 그날 나머지 호출은 전 사용자가 즉시 읽는다.

optimizer(포트폴리오 맥락)는 사용자 보유 종목에 의존하므로 여기 넣지 않는다 —
캐시된 공용 본문에 요청 시점에 계산해 덧붙인다.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from backend.db import get_conn, is_available

logger = logging.getLogger(__name__)

# 갱신 주기 — 일봉이 하루 한 번 확정되므로 24시간
TTL_HOURS = 24


def get_cached(ticker: str, period: str, ttl_hours: int = TTL_HOURS) -> Optional[dict]:
    """유효기간 내 캐시된 분석 본문. 없거나 만료면 None."""
    if not is_available():
        return None
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT payload, EXTRACT(EPOCH FROM (NOW() - computed_at)) / 3600.0
                       FROM ticker_analytics
                       WHERE ticker = %s AND period = %s
                         AND computed_at >= NOW() - (%s * INTERVAL '1 hour')""",
                    (ticker.upper(), period, ttl_hours),
                )
                row = cur.fetchone()
        if not row:
            return None
        payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        payload["_cache_age_hours"] = round(float(row[1]), 2)
        return payload
    except Exception as e:
        logger.warning(f"ticker_analytics 조회 실패 {ticker}/{period}: {e}")
        return None


def save(ticker: str, period: str, payload: dict) -> bool:
    """분석 본문 저장 (같은 종목·기간이면 덮어쓰고 시각 갱신)."""
    if not is_available():
        return False
    try:
        body = {k: v for k, v in payload.items() if not k.startswith("_")}
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO ticker_analytics(ticker, period, payload, computed_at)
                       VALUES (%s, %s, %s, NOW())
                       ON CONFLICT (ticker, period) DO UPDATE
                       SET payload = EXCLUDED.payload, computed_at = NOW()""",
                    (ticker.upper(), period, json.dumps(body, ensure_ascii=False, default=str)),
                )
        return True
    except Exception as e:
        logger.warning(f"ticker_analytics 저장 실패 {ticker}/{period}: {e}")
        return False


def purge_stale(older_than_days: int = 7) -> int:
    """오래된 항목 정리. 유니버스가 11,000종목이라 무한정 쌓이면 곤란하다."""
    if not is_available():
        return 0
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM ticker_analytics WHERE computed_at < NOW() - (%s * INTERVAL '1 day')",
                    (older_than_days,),
                )
                return cur.rowcount
    except Exception as e:
        logger.warning(f"ticker_analytics 정리 실패: {e}")
        return 0

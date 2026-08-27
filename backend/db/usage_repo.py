"""
db/usage_repo.py
────────────────
심층 분석 사용 기록.

관리자가 '계정당 24시간 1회' 제한을 켰을 때 판단 근거가 된다.
잡 저장소(JobStore)는 메모리라 서버가 재시작되면 사라지고, reports 테이블은
취소·실패한 시도를 남기지 않아 횟수 계산에 쓸 수 없다. 그래서 시도 자체를
여기에 따로 남긴다.
"""
from __future__ import annotations

import logging
from typing import Optional

from backend.db import get_conn, is_available

logger = logging.getLogger(__name__)

WINDOW_HOURS = 24


def record_use(user_id: str, kind: str) -> None:
    """심층 분석 사용 1건 기록."""
    if not is_available() or not user_id:
        return
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO deep_analysis_usage (user_id, kind) VALUES (%s, %s)",
                    (user_id, kind),
                )
            conn.commit()
    except Exception as e:
        logger.error(f"deep_analysis_usage 기록 실패: {e}")


def count_recent(user_id: str, hours: int = WINDOW_HOURS) -> int:
    """최근 N시간 사용 횟수."""
    if not is_available() or not user_id:
        return 0
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT COUNT(*) FROM deep_analysis_usage
                       WHERE user_id = %s
                         AND used_at >= NOW() - (%s * INTERVAL '1 hour')""",
                    (user_id, hours),
                )
                r = cur.fetchone()
        return int(r[0]) if r else 0
    except Exception as e:
        logger.error(f"deep_analysis_usage 조회 실패: {e}")
        # 조회에 실패했다는 이유로 사용자를 막지는 않는다.
        return 0


def next_available_at(user_id: str, hours: int = WINDOW_HOURS) -> Optional[str]:
    """가장 오래된 사용 기록 기준으로 다시 쓸 수 있는 시각 (ISO). 없으면 None."""
    if not is_available() or not user_id:
        return None
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT MIN(used_at) + (%s * INTERVAL '1 hour')
                       FROM deep_analysis_usage
                       WHERE user_id = %s
                         AND used_at >= NOW() - (%s * INTERVAL '1 hour')""",
                    (hours, user_id, hours),
                )
                r = cur.fetchone()
        return r[0].isoformat() if r and r[0] else None
    except Exception as e:
        logger.error(f"deep_analysis_usage 조회 실패: {e}")
        return None

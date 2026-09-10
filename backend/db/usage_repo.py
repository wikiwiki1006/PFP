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
    """심층 분석 사용 1건 기록. 기록하지 못하면 예외를 올린다.

    예전에는 실패를 삼키고 조용히 반환했다. 이 테이블이 할당량의 **유일한
    근거**라 기록이 안 되면 그 사용은 없었던 일이 된다 (§1.3).
    """
    if not user_id:
        raise ValueError("record_use 에 user_id 가 없다")
    if not is_available():
        raise RuntimeError("DB 미연결 — 심층 분석 사용을 기록할 수 없다")
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
        raise


def count_recent(user_id: str, hours: int = WINDOW_HOURS) -> int:
    """최근 N시간 사용 횟수. **세지 못하면 예외를 올린다.**

    예전에는 "조회에 실패했다는 이유로 사용자를 막지는 않는다" 며 0 을
    돌려줬다. 0 은 "아직 안 썼다" 와 같은 값이라, 호출자(enforce_deep_limit)가
    무조건 통과시킨다 — **DB 가 흔들리는 동안 전 사용자가 심층 분석을 무제한으로
    쓰고, 신호는 청구서뿐이다.** 안전장치는 실패하면 닫는다 (§1.3).

    막는 쪽 손해도 재봤다. 이 함수가 못 도는 상황이면 잡 저장소(DB)도, 리포트
    저장도 같이 못 돈다 — 통과시켜 봐야 LLM 비용만 쓰고 결과를 못 남긴다.
    """
    if not user_id:
        raise ValueError("count_recent 에 user_id 가 없다")
    if not is_available():
        raise RuntimeError("DB 미연결 — 심층 분석 사용 횟수를 확인할 수 없다")
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
        raise


def next_available_at(user_id: str, hours: int = WINDOW_HOURS) -> Optional[str]:
    """가장 오래된 사용 기록 기준으로 다시 쓸 수 있는 시각 (ISO). 없으면 None.

    위 둘과 달리 실패해도 None 을 돌려준다. 이 값은 이미 확정된 429 응답의
    문구를 꾸미는 데만 쓰이므로(`enforce_deep_limit`), 실패해도 막을 것을
    못 막는 일이 없다. 안내 문장이 짧아질 뿐이고 실패는 로그에 남는다.
    """
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

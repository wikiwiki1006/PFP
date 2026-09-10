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

from backend.db import DBBusy, get_conn, is_available

logger = logging.getLogger(__name__)

WINDOW_HOURS = 24

# 소비 한 건이 락을 기다리는 상한. 경쟁은 같은 사용자 안에서만 일어나므로
# (제출 버튼 더블클릭 수준) 실제로는 즉시 잡힌다. 상한을 두는 이유는 기다리는
# 동안 커넥션을 쥐고 있기 때문이다 — 풀 고갈은 이미 한 번 겪었다.
_LOCK_TIMEOUT_MS = 3000


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


def consume(user_id: str, kind: str, hours: int = WINDOW_HOURS) -> bool:
    """할당량 한 칸을 **원자적으로** 쓴다. 성공하면 True, 이미 썼으면 False.

    `count_recent` 로 확인하고 나중에 `record_use` 로 기록하는 순서는 TOCTOU 다.
    동시 요청 둘이 모두 0을 보고 통과한 뒤 둘 다 기록하면, 1일 1회 제한에
    심층 분석이 두 번 나간다. 심층은 LLM 호출이라 비용이 바로 발생한다.
    검사와 기록을 한 트랜잭션에 묶는다.

    ## 왜 이 모양인가

    조건부 INSERT 만으로는 부족하다. READ COMMITTED 에서는 두 트랜잭션이 각자의
    스냅샷에서 `NOT EXISTS` 를 참으로 보고 둘 다 INSERT 한다(write skew). 보통은
    유니크 제약이 그걸 막아주는데, **"최근 24시간"은 슬라이딩 윈도우라 유니크
    제약으로 표현할 수 없다.** 기댈 제약이 없다.

    윈도우를 고정 버킷(예: 날짜)으로 바꾸면 제약을 걸 수 있지만 의미가 달라진다 —
    23:50 에 쓴 사용자가 00:10 에 또 쓴다. 20분 안에 2회이고, 그게 바로 막으려던
    비용이다. 원자성을 얻으려고 막으려던 것을 허용하는 건 거래가 안 된다.

    그래서 사용자별 advisory lock 으로 직렬화한다. **`_xact_` 인 것이 핵심이다** —
    트랜잭션 스코프라 커밋·롤백·커넥션 종료 어느 쪽으로 끝나도 자동으로 풀린다.
    세션 스코프 락(`portfolio_repo.user_write_lock`)은 커넥션이 닫히면 조용히
    증발해서 그걸 막느라 한참 걸렸다. 같은 함정이 없는 원시연산을 골랐다.

    락 키에 `deep:` 을 붙이는 이유는 `user_write_lock` 의 `pfp:` 와 겹치지 않게
    하기 위해서다. `hashtext` 는 32bit 라 서로 다른 uid 가 같은 값으로 떨어질 수
    있는데, 그건 무관한 요청 둘이 잠깐 줄을 서는 손해일 뿐 판정이 틀리지는
    않는다. 접두사는 그 우연을 **두 락 계열 사이에서 체계적으로** 만들지 않는다.

    ## 호출 규약

    - `True`  — 소비했다. 실제로 분석을 시작해도 된다.
    - `False` — 최근 `hours` 시간 안에 이미 썼다. 호출자가 429 를 준다.
    - `DBBusy` — 락을 제때 못 잡았다. 503 으로 나간다(`main.py` 핸들러).
    - `RuntimeError` / 그 밖의 예외 — 확인 자체가 불가능하다. **닫는다.**
      `count_recent` 와 같은 기준이다 — 이 함수가 못 도는 상황이면 잡 저장소도
      리포트 저장도 같이 못 돈다.

    **되돌리기(release)가 없다.** 취소·캐시 히트처럼 "결국 안 만든" 경우를
    되돌리려면 호출자가 usage id 를 들고 다녀야 하고, 어느 한 경로에서 빠뜨리면
    그게 새로운 조용한 결함이 된다. 대신 **실제로 만들기로 확정된 지점에서만**
    부른다 — 공용 캐시로 돌려주는 경로는 여기까지 오지 않는다.
    """
    if not user_id:
        raise ValueError("consume 에 user_id 가 없다")
    if not is_available():
        raise RuntimeError("DB 미연결 — 심층 분석 할당량을 확인할 수 없다")

    key = f"deep:{user_id}"
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL lock_timeout = %s", (_LOCK_TIMEOUT_MS,))
                cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (key,))
                cur.execute(
                    """INSERT INTO deep_analysis_usage (user_id, kind)
                       SELECT %s, %s
                       WHERE NOT EXISTS (
                           SELECT 1 FROM deep_analysis_usage
                           WHERE user_id = %s
                             AND used_at >= NOW() - (%s * INTERVAL '1 hour')
                       )""",
                    (user_id, kind, user_id, hours),
                )
                return cur.rowcount == 1
    except Exception as e:
        if getattr(e, "pgcode", None) == "55P03":        # lock_not_available
            logger.error(f"심층 분석 할당량 락 대기 초과 (uid={user_id}): {e}")
            raise DBBusy(
                "요청이 몰려 지금 처리할 수 없습니다. 잠시 후 다시 시도해 주세요."
            ) from e
        logger.error(f"심층 분석 할당량 소비 실패 (uid={user_id}, kind={kind}): {e}")
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

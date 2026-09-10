"""
풀이 재초기화돼도 이미 쥔 advisory lock 은 살아 있어야 한다.

`test_holdings_write_race.py` 는 락이 **걸리는지**를 잰다. 이 파일은 그 락이
**유지되는지**를 잰다. 둘은 다른 성질이고, 두 번째가 조용히 깨진다.

`user_write_lock` 은 풀에서 커넥션 하나를 꺼내 `pg_advisory_lock` 을 걸고
블록이 끝날 때까지 쥐고 있는다. advisory lock 은 **세션(커넥션) 단위**라
그 커넥션이 닫히면 락도 함께 사라진다.

그런데 `backend/db/__init__.py` 의 `_try_reinit_pool()` 은 `_pool.closeall()`
을 부른다 — 풀 안의 **모든** 커넥션을 닫는다. 락을 쥐고 있는 다른 요청의
커넥션까지 포함해서다. 그 요청은 자기가 아직 락을 쥐고 있다고 믿으면서
임계구역을 계속 실행하고, 그 사이 다른 요청이 같은 락을 새로 잡을 수 있다.

`_try_reinit_pool()` 은 커넥션이 세 번 연속 실패했을 때 불린다. 즉 **DB 가
불안정할 때만** 일어나고, 하필 그때 동시 쓰기가 직렬화되지 않는다. 재현이
어려운 조건이라 운영에서 났다면 원인을 찾기 어려웠을 종류다.

## 관찰 방법

락이 살아 있는지는 **풀 바깥의 별도 커넥션**에서 확인해야 한다. 풀에서 꺼낸
커넥션으로 보면 `closeall()` 이 관찰자까지 닫아버려 아무것도 못 본다.

확인은 `pg_try_advisory_lock` 으로 한다 — 다른 세션이 같은 키를 잡는 데
성공하면 그건 곧 아무도 쥐고 있지 않다는 뜻이다. `pg_locks` 를 직접 읽는
것보다 키 인코딩(bigint 를 classid/objid 로 쪼개는 규칙)에 덜 의존한다.
"""
from __future__ import annotations

import uuid

import psycopg2
import pytest

import backend.db as db
from backend.db.portfolio_repo import user_write_lock


@pytest.fixture
def observer(live_db):
    """풀과 무관한 커넥션. `closeall()` 이 닫지 못해야 관찰이 가능하다."""
    conn = psycopg2.connect(db._dsn())
    conn.autocommit = True
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def lock_uid():
    """이 실행에만 쓰는 키. 다른 창·다른 테스트와 절대 겹치지 않게 한다."""
    return f"lockprobe-{uuid.uuid4().hex[:12]}"


def _held_by_someone(observer, uid: str) -> bool:
    """다른 세션이 같은 키를 못 잡으면 = 아직 누군가 쥐고 있다.

    잡는 데 성공했으면 즉시 놓는다. 관찰이 상태를 남기면 안 된다.
    """
    key = f"pfp:{uid}"
    with observer.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (key,))
        acquired = cur.fetchone()[0]
        if acquired:
            cur.execute("SELECT pg_advisory_unlock(hashtext(%s))", (key,))
    return not acquired


# ── 대조군: 관찰 장치가 실제로 락을 본다 ────────────────────────────────────────

def test_observer_sees_the_lock(observer, lock_uid):
    """관찰 장치부터 검증한다.

    이게 없으면 아래 테스트가 "락이 없다" 를 언제나 보고할 수 있고, 그러면
    통과해도 아무것도 증명하지 못한다.
    """
    assert not _held_by_someone(observer, lock_uid), "테스트 시작 전인데 키가 잡혀 있다"

    with user_write_lock(lock_uid):
        assert _held_by_someone(observer, lock_uid), (
            "user_write_lock is inside its block but no session holds the "
            "advisory lock -- the observer or the lock itself is broken. "
            "(락을 쥔 상태를 관찰하지 못하면 아래 검사가 무의미하다.)"
        )

    assert not _held_by_someone(observer, lock_uid), (
        "the advisory lock outlived its with-block -- it leaked. "
        "(블록을 벗어났는데 락이 남아 있다.)"
    )


# ── 본론 ──────────────────────────────────────────────────────────────────────

@pytest.mark.xfail(
    strict=True,
    reason=(
        "backend/db/__init__.py _try_reinit_pool() calls _pool.closeall(), "
        "which closes the connection another request is holding its advisory "
        "lock on, so the lock dies mid-critical-section. Delete this marker "
        "once a reset spares in-use connections."
    ),
)
def test_lock_outlives_a_pool_reinit(observer, lock_uid):
    """풀 재초기화가 남의 락을 끊으면 안 된다.

    `_try_reinit_pool()` 은 커넥션 실패가 누적됐을 때 복구용으로 불린다.
    복구가 **아직 정상인 다른 요청의 상호배제를 깨뜨리는 것**이 문제다.
    깨지는 순간 아무도 모른다 — 락을 쥔 쪽은 계속 실행하고, 예외도 로그도 없다.
    """
    with user_write_lock(lock_uid):
        assert _held_by_someone(observer, lock_uid), "전제: 블록 진입 시 락이 잡혀 있다"

        assert db._try_reinit_pool(), "풀 재초기화 자체가 실패했다 — 다른 문제다"

        assert _held_by_someone(observer, lock_uid), (
            "the advisory lock vanished during a pool reinit while the "
            "with-block was still running -- two requests can now enter the "
            "same critical section and neither notices. "
            "(임계구역 실행 중에 락이 증발했다.)"
        )

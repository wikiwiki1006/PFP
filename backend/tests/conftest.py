"""
backend/tests/conftest.py
─────────────────────────
실DB 에 붙는 테스트의 공용 배관.

이 리포의 테스트는 거의 전부 모킹이다. 그래도 실DB 가 필요한 것이 있다 —
`user_write_lock` 은 PostgreSQL advisory lock 이라 모킹하면 검증이 성립하지
않는다 (가짜 락은 언제나 성공하므로 락이 아예 없어도 통과한다). 창마다 전용
DB 를 둔 것이 그걸 가능하게 하려는 것이다.

실DB 테스트에는 매번 같은 세 가지가 필요한데, 셋 다 빼먹으면 조용히 틀린다.
그래서 파일마다 다시 쓰지 않고 여기 모아 둔다.

  1. 풀 생명주기 — 열고, **끝나면 반드시 원상복구**
  2. 붙은 DB 가드 — 로컬인지, 그리고 남의 DB 가 아닌지
  3. 사용자 행 준비·정리 — holdings·trade_log 가 users(id) 를 FK 로 문다

**autouse 로 만들지 않는다.** 필요한 테스트만 `live_db` / `db_uid` 를 인자로
받아 간다. 전역으로 켜면 나머지 모킹 테스트가 전부 실DB 를 물게 된다.
"""
from __future__ import annotations

import uuid

import pytest

import backend.db as db

# 원격이면 아무것도 하지 않는다. Neon 에는 실사용자 데이터가 있고
# (users 12 · holdings 30 · reports 46) 역할 창이 붙을 곳이 아니다.
_ALLOWED_HOSTS = {"localhost", "127.0.0.1", "::1", "", None}


@pytest.fixture(scope="module")
def live_db():
    """실제 연결 풀. 붙지 못하면 fail 이고, 끝나면 풀을 원래 상태로 되돌린다.

    **skip 이 아니라 fail 인 이유**: `user_write_lock` 은
    `if not is_available(): yield; return` 이라 DB 가 없으면 락 없이 그냥
    통과한다. 그 상태의 초록불은 "락이 동작한다" 가 아니라 "락을 재보지도
    못했다" 는 뜻인데, skip 은 CI 에서 조용히 사라져 그 구분이 안 남는다.

    **모듈 스코프이고 반드시 되돌리는 이유**: 나머지 테스트는 전부 모킹이고
    그중 일부는 `is_available()` 이 False 인 것에 기대어 동작한다. 풀을 열어둔
    채 나가면 뒤따르는 파일이 통째로 깨진다 (실제로 그랬다 — test_job_cancel
    2건). 세션 스코프로 올리면 세션 끝까지 열려 있어 같은 사고가 난다.
    """
    previous = db._pool
    if previous is None and not db.init_pool(minconn=2, maxconn=10):
        pytest.fail(
            "cannot reach the database -- this test measures a real advisory "
            "lock and has no mocked equivalent. "
            "(로컬 도커 postgres(5433)를 띄우고 backend/.env 의 DB_NAME 이 "
            "이 워크트리 전용 DB 인지 확인하라.)"
        )
    try:
        yield _assert_safe_target()
    finally:
        if previous is None:
            db.close_pool()          # 우리가 연 것만 닫는다
        db._pool = previous


def _assert_safe_target() -> str:
    """붙은 DB 가 이 창의 전용 DB 가 맞는지 확인한다. 아니면 즉시 멈춘다.

    `postgres` 거부가 특히 중요하다. 그건 다섯 창이 복제해 온 **원본 템플릿**이라
    테스트 행이 섞이면 이후 만들어지는 모든 창이 그걸 물려받는다. 실제로 한 번
    걸렸다 — 병합 후 메인에서 게이트를 돌렸더니 `backend/.env` 가 아직
    `DB_NAME=postgres` 였고, 이 가드가 없었으면 조용히 오염됐을 것이다.
    """
    with db.get_conn() as conn:
        # 서버가 스스로 말하는 주소(inet_server_addr)를 보면 안 된다 — 도커 포트
        # 매핑을 거치면 컨테이너 내부 IP(172.17.x.x)가 나와서 로컬인데도 원격으로
        # 읽힌다. 우리가 실제로 다이얼한 클라이언트 쪽 호스트를 본다.
        host = (conn.info.host or "").lower()
        with conn.cursor() as cur:
            cur.execute("SELECT current_database()")
            dbname = cur.fetchone()[0]

    if host not in _ALLOWED_HOSTS:
        pytest.fail(
            f"connected to a remote database (host={host}) -- local only. "
            "(원격 DB 에 붙었다.)"
        )
    if dbname == "postgres":
        pytest.fail(
            "connected to 'postgres', the template every window was cloned from -- "
            "test rows here are inherited by every window created later. "
            "Point backend/.env DB_NAME at this worktree's own database. "
            "(원본 템플릿이다. 여기에 쓰면 격리가 무의미해진다.)"
        )
    if not dbname.startswith("pfp_"):
        pytest.fail(
            f"connected to '{dbname}', which is not one of this project's "
            "per-window databases (pfp_*). Check backend/.env DB_NAME."
        )
    return dbname


@pytest.fixture
def db_uid(live_db):
    """테스트마다 새 사용자. 남의 행을 건드리지 않고, 끝나면 자기 행만 지운다.

    holdings·trade_log·reports 가 users(id) 를 FK 로 물고 있어 사용자 행이 먼저
    있어야 한다. 지울 때는 ON DELETE CASCADE 가 딸린 행까지 걷어가므로 사용자
    한 줄만 지우면 된다.
    """
    uid = f"pytest-{uuid.uuid4().hex[:12]}"
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users(id, name, email) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                (uid, "pytest", f"{uid}@example.invalid"),
            )
    yield uid
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE id=%s", (uid,))

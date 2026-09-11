"""
잡 조회는 소유자가 다르면 **403 이 아니라 404** 다 (§1.2).

CLAUDE.md 가 명시한 규칙이고 `job_store.get` 의 docstring도 그렇게 적고
있다 — *"403 으로 구분해 주면 '그 잡은 존재한다'는 사실이 새어나가므로,
없는 것과 똑같이 취급한다."* 인용된 적은 있어도 **잰 적은 없다.**

## 상태 코드까지 재려면 HTTP 를 지나야 한다

`job_store.get()` 만 부르면 "None 을 돌려준다" 까지만 확인된다. 규칙이
말하는 것은 **사용자가 받는 응답**이고, None 을 404 로 바꾸는 것은 라우터다.
그 사이에서 403 으로 바뀌어도 저장소 검사는 초록이다. 그래서 라우터를 얹은
미니 앱으로 실제 상태 코드를 읽는다.

`backend.main.app` 을 그대로 쓰면 startup(DB 풀·스케줄러)이 돈다.
라우터만 얹는다.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers import optimizer as opt
from backend.services.auth import optional_user
from backend.services.markets import market_param

OWNER = "job-owner-uid"
OTHER = "job-other-uid"


@pytest.fixture
def store(monkeypatch):
    """잡 저장소를 **메모리로** 고정한다.

    실DB 를 쓰면 이 검사가 이 창의 `jobs` 테이블 상태에 달린다. 재려는 것은
    소유자 판정이지 저장 매체가 아니고, 판정 코드는 두 경로가 같은 규칙을
    쓴다 — 아래 마지막 검사가 그 점을 따로 고정한다.
    """
    monkeypatch.setattr(opt._store, "_jobs", {}, raising=False)
    monkeypatch.setattr(opt._store, "_events", {}, raising=False)
    monkeypatch.setattr("backend.services.job_store._db", lambda: None)
    return opt._store


@pytest.fixture
def client(store):
    """optimizer 라우터만 얹은 앱. 인증은 테스트가 갈아끼운다."""
    app = FastAPI()
    app.include_router(opt.router)
    app.dependency_overrides[market_param] = lambda: "US"

    def as_user(uid):
        if uid is None:
            app.dependency_overrides[optional_user] = lambda: None
        else:
            app.dependency_overrides[optional_user] = lambda: {"uid": uid}

    c = TestClient(app)
    c.as_user = as_user
    as_user(OWNER)
    return c


def _make_job(store, owner):
    job_id = str(uuid.uuid4())
    store.set(job_id, {"status": "done", "result": {"secret": "주인의 최적화 결과"}},
              owner=owner)
    return job_id


# ── 규칙 — 남의 잡은 404 다 ────────────────────────────────────────────────────

def test_another_users_job_is_404_not_403(client, store):
    """다른 사용자의 잡은 **404** 다.

    403 은 "권한이 없다" 이므로 **그 잡이 존재한다**는 사실을 알려준다.
    잡 id 를 바꿔 가며 찔러 보면 어떤 id 가 실재하는지 지도가 그려진다.
    """
    job_id = _make_job(store, OWNER)
    client.as_user(OTHER)

    r = client.get(f"/api/optimizer/ai-optimize-job/{job_id}")

    assert r.status_code == 404, (
        f"another user's job answered {r.status_code} -- 403 confirms the job "
        "exists, which is the fact this rule exists to hide. (§1.2)"
    )
    assert "주인의" not in r.text, "남의 결과가 본문에 실렸다"


def test_a_job_that_never_existed_is_also_404(client, store):
    """없는 잡도 404 — **남의 잡과 구별되지 않아야** 한다.

    둘이 다른 응답이면 그 차이만으로 존재 여부를 읽을 수 있다. 상태 코드가
    같아도 본문이 다르면 마찬가지다.
    """
    mine = _make_job(store, OWNER)
    client.as_user(OTHER)

    theirs = client.get(f"/api/optimizer/ai-optimize-job/{mine}")
    missing = client.get(f"/api/optimizer/ai-optimize-job/{uuid.uuid4()}")

    assert theirs.status_code == missing.status_code == 404
    assert theirs.text == missing.text, (
        f"the two answers differ ({theirs.text} vs {missing.text}) -- the "
        "difference alone tells the caller which job ids are real."
    )


def test_the_owner_can_read_their_own_job(client, store):
    """대조군 — 주인은 읽을 수 있다.

    없으면 위 둘은 "모든 잡이 404" 라는 구현으로도 통과하고, 그러면 진행
    상황 폴링이 통째로 죽는다.
    """
    job_id = _make_job(store, OWNER)
    client.as_user(OWNER)

    r = client.get(f"/api/optimizer/ai-optimize-job/{job_id}")

    assert r.status_code == 200, f"주인이 자기 잡을 못 읽는다: {r.status_code}"
    assert "주인의" in r.text


def test_an_unowned_job_is_readable_by_a_signed_in_user(client, store):
    """소유자 없는 잡은 누구나 읽는다 — **의도된 동작**이다.

    비로그인 최적화는 사용자가 직접 입력한 공개 티커만 다루므로 소유자를
    두지 않는다(`optimizer.py` 의 그 주석). id 가 UUID4 라 추측으로는 못
    찾는다. 여기서 막으면 비로그인 사용자가 자기 잡을 못 본다.
    """
    job_id = _make_job(store, None)
    client.as_user(OTHER)

    assert client.get(f"/api/optimizer/ai-optimize-job/{job_id}").status_code == 200


def test_an_anonymous_caller_can_still_read_an_unowned_job(client, store):
    """비로그인 호출자도 **소유자 없는** 잡은 읽어야 한다.

    아래 결함을 고칠 때 **같이 지켜야 하는 조건**이다. `owner=None` 일 때
    그냥 전부 막아 버리면 이 검사가 빨개진다 — 비로그인 최적화 사용자가
    자기가 방금 띄운 잡의 진행 상황을 못 보게 되고, 화면은 영원히 "준비 중"
    에 머문다.

    그래서 고칠 자리는 "익명이면 막는다" 가 아니라 **"익명" 과 "소유자 검사
    안 함" 을 서로 다른 값으로 만드는 것**이다.
    """
    job_id = _make_job(store, None)
    client.as_user(None)

    assert client.get(f"/api/optimizer/ai-optimize-job/{job_id}").status_code == 200, (
        "an anonymous caller lost access to its own unowned job -- the fix "
        "below must keep this working."
    )


# ── 익명 호출자도 규칙에 걸린다 ────────────────────────────────────────────────
#
# 한동안 안 걸렸다. `job_store.get` 의 검사가 `if owner is not None and
# user_id not in (None, owner)` 였고, 이 엔드포인트는 `optional_user` 라
# 라우터가 비로그인에 `owner=None` 을 넘겼다. 그 `None` 은 "익명 사용자" 가
# 아니라 **"검사하지 마"** 였다 — 한 값이 두 뜻을 갖는 동안 로그인하지 않은
# 호출자가 남의 잡을 읽고 취소할 수 있었다.
#
# 고친 방향은 "익명이면 막는다" 가 **아니다.** 그러면 바로 위 검사가
# 빨개진다 — 비로그인 최적화 사용자가 자기 잡의 진행 상황을 못 본다.
# 두 뜻을 다른 값으로 갈랐다 (`job_store.ANONYMOUS`):
#
#     owner is None        소유자 검사 안 함 (내부 호출·정리) → 전부
#     owner is ANONYMOUS   비로그인 호출자                   → 무주공산만
#     owner == uid         로그인 호출자                     → 자기 것 + 무주공산
#
# id 가 UUID4 라 추측은 어렵다. 다만 잡 id 는 URL·로그·브라우저 기록에
# 남으므로 "모르면 안전" 은 소유자 검사의 대체물이 아니다.

def test_an_anonymous_caller_cannot_read_someone_elses_job(client, store):
    """로그인하지 않은 호출자도 남의 잡은 못 읽어야 한다."""
    job_id = _make_job(store, OWNER)
    client.as_user(None)

    r = client.get(f"/api/optimizer/ai-optimize-job/{job_id}")

    assert r.status_code == 404, (
        f"an anonymous caller read a signed-in user's job ({r.status_code}) -- "
        "owner=None skips the check instead of meaning 'anonymous'."
    )


def test_an_anonymous_caller_cannot_cancel_someone_elses_job(client, store):
    """취소도 같은 판정을 지난다.

    읽기만 막고 끝내면 안 된다 — 취소는 `get()` 을 지나므로 **같이** 열려
    있었다. 그리고 읽기와 달리 상태를 **바꾼다.** 실행 중인 잡으로 재야
    한다: 끝난 잡에 대고 부르면 아무 일도 안 일어나서, 막혔는지 원래
    바뀔 것이 없었는지 구별할 수 없다.
    """
    running = str(uuid.uuid4())
    store.set(running, {"status": "running"}, owner=OWNER)

    client.as_user(None)
    client.delete(f"/api/optimizer/ai-optimize-job/{running}")

    client.as_user(OWNER)
    assert store.get(running, owner=OWNER)["status"] == "running", (
        "an anonymous caller cancelled a signed-in user's running job -- "
        "cancel goes through the same get(), so it must apply the same rule."
    )


# ── 저장 매체가 바뀌어도 규칙은 같다 ───────────────────────────────────────────

def test_the_database_path_applies_the_same_owner_rule():
    """DB 경로와 메모리 경로가 같은 판정을 한다.

    `get()` 은 DB 가 없거나 조회가 터지면 메모리로 내려간다. 두 경로의
    규칙이 어긋나면 **DB 가 흔들리는 동안만** 남의 잡이 열린다 — 그 순간을
    재현하기 어려워서 아무도 못 본다.

    소스에서 두 검사가 같은 형태인지 본다. 실DB 를 띄워 한쪽을 죽이는 것보다
    이 편이 정확하다 — 재려는 것은 "두 분기가 같은 규칙을 쓰는가" 이지
    DB 가 실제로 죽었을 때의 동작이 아니다.

    **한계를 적어 둔다**: 위 검사들은 `_db()` 를 None 으로 눌러 메모리
    경로만 돈다. 그래서 **DB 경로의 소유자 검사는 이 검사 하나로만 덮인다** —
    변이로 확인했다: DB 경로의 검사를 지우면 다른 검사는 전부 초록이고
    여기만 빨개진다.

    예전에는 두 경로에 규칙이 **복사되어** 있어 같은 문자열
    (`not in (None, owner)`)이 양쪽에 있는지 봤다. 익명 수정 때 그 규칙이
    `_visible()` 한 곳으로 모였고, 지금은 **둘이 같은 것을 부르는지**를
    본다 — 복사본이 없으면 어긋날 자리도 없으므로 이쪽이 더 강하다.
    (원 검사의 주석이 "검사 문구가 바뀌면 같이 고쳐야 한다" 고 그 비용을
    미리 적어 두었다.)
    """
    import inspect

    from backend.services import job_store

    db_path = inspect.getsource(job_store.JobStore.get)
    mem_path = inspect.getsource(job_store.JobStore._mem_get)

    call = "self._visible("
    assert call in db_path and call in mem_path, (
        "the DB path and the in-memory fallback no longer share the same "
        f"owner rule ({call!r}) -- a mismatch opens other people's jobs only "
        "while the database is unreachable, which is nearly impossible to "
        "notice. (DB 가 흔들리는 동안만 열린다.)"
    )

    # 그 한 곳이 세 상태를 실제로 가르는지 본다. 위 단언만으로는 `_visible`
    # 이 `return True` 여도 통과한다 — 호출하는지만 봤지 무엇을 하는지는
    # 안 봤기 때문이다.
    visible = job_store.JobStore._visible
    assert visible(None, None) and visible("someone", None), "None 은 검사 생략이다"
    assert visible(None, job_store.ANONYMOUS), "익명도 무주공산은 읽는다"
    assert not visible("someone", job_store.ANONYMOUS), "익명이 남의 잡을 읽는다"
    assert visible("me", "me") and visible(None, "me"), "본인 것과 무주공산"
    assert not visible("someone", "me"), "로그인 호출자가 남의 잡을 읽는다"


# ── DB 분기를 실제로 돌린다 ────────────────────────────────────────────────────
#
# 위 동등성 검사는 두 조각으로 나눠 본다 — "DB 경로가 `_visible` 을 부르는가"
# 와 "`_visible` 이 세 상태를 가르는가". 둘 다 맞아도 **합성이 틀릴 수 있다**:
# DB 경로가 `_visible` 에 인자를 바꿔 넘기거나, `SELECT` 의 다른 열을
# `user_id` 로 읽으면 두 조각 검사는 그대로 통과한다.
#
# 위 검사들은 전부 `_db()` 를 None 으로 눌러 **메모리 경로만** 돈다. 그래서
# 그 합성은 아무 데서도 안 재진다. 여기서 행을 직접 넣고 DB 경로로 읽는다.

@pytest.fixture
def db_jobs(live_db):
    """`jobs` 테이블에 직접 넣고 지운다. 풀이 열려 있어 `_db()` 가 DB 를 준다."""
    from backend.db import get_conn

    kind = "__test_owner__"

    def purge():
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM jobs WHERE kind=%s", (kind,))

    def put(owner):
        job_id = str(uuid.uuid4())
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO jobs(id, kind, user_id, status, result) "
                "VALUES(%s,%s,%s,'done',%s)",
                (job_id, kind, owner, '{"secret": "주인의 결과"}'),
            )
        return job_id

    purge()
    yield kind, put
    purge()


def test_the_database_branch_applies_the_rule_to_the_right_row(db_jobs):
    """DB 에서 읽은 행에 소유자 규칙이 **실제로** 적용된다.

    여섯 갈래를 DB 경로로 직접 잰다. 조각 검사 둘이 다 맞아도 여기가
    틀릴 수 있다 — 인자를 바꿔 넘기거나 다른 열을 `user_id` 로 읽으면
    조각 검사는 통과한다.
    """
    from backend.services.job_store import ANONYMOUS, JobStore

    kind, put = db_jobs
    store = JobStore(kind=kind)
    owned = put(OWNER)
    unowned = put(None)

    # 소유자 검사 생략(None) — 둘 다 보인다.
    assert store.get(owned, owner=None) is not None
    assert store.get(unowned, owner=None) is not None

    # 익명 — 무주공산만.
    assert store.get(unowned, owner=ANONYMOUS) is not None, "익명이 자기 잡을 못 읽는다"
    assert store.get(owned, owner=ANONYMOUS) is None, (
        "an anonymous caller read a signed-in user's job through the database "
        "path -- the in-memory tests cannot see this. (§1.2)"
    )

    # 로그인 — 자기 것 + 무주공산.
    assert store.get(owned, owner=OWNER) is not None, "주인이 자기 잡을 못 읽는다"
    assert store.get(unowned, owner=OWNER) is not None
    assert store.get(owned, owner=OTHER) is None, (
        "another user read this job through the database path."
    )


def test_the_database_branch_is_the_one_being_exercised(db_jobs):
    """전제 — 위 검사가 **메모리 폴백이 아니라** DB 경로를 돌았다.

    풀이 닫혀 있으면 `_db()` 가 None 을 주고 전부 메모리로 내려간다. 그러면
    위 검사는 이미 덮인 경로를 한 번 더 재는 것이 되고, 노리던 합성은
    그대로 안 재진다.
    """
    from backend.services import job_store

    assert job_store._db() is not None, (
        "the job store fell back to memory -- the test above measured the "
        "path that was already covered, not the database one."
    )

    kind, put = db_jobs
    job_id = put(OWNER)
    from backend.db import get_conn
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM jobs WHERE id=%s", (job_id,))
        assert cur.fetchone(), "행이 DB 에 안 들어갔다"

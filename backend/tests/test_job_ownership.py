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


# ── 익명 호출자에게는 그 규칙이 걸리지 않는다 ──────────────────────────────────
#
# `job_store.get` 의 검사는 `if owner is not None and user_id not in (None,
# owner)` 다. 즉 `owner=None` 은 **"익명 사용자"** 가 아니라 **"검사하지
# 마"** 로 동작한다.
#
# 그리고 이 엔드포인트는 `optional_user` 라 로그인 없이 부를 수 있고,
# 라우터는 `owner=(_auth or {}).get("uid")` — 비로그인이면 `None` 이다.
# 그래서 **로그인하지 않은 호출자가 남의 잡을 읽는다.** 같은 이유로
# DELETE 도 통과해 남의 잡을 취소할 수 있다.
#
# 라우터 주석은 *"남의 잡이면 존재 여부조차 알리지 않는다"* 라고 적고 있다.
# 로그인한 호출자에게는 맞고 **익명 호출자에게는 틀리다.**
#
# id 가 UUID4 라 추측은 어렵다. 다만 잡 id 는 URL·로그·브라우저 기록에
# 남으므로 "모르면 안전" 은 소유자 검사의 대체물이 아니다.
#
# ## 무엇을 고쳐야 하는가
#
# "익명이면 막는다" 가 아니다. 그렇게 하면 바로 위 검사가 빨개진다 —
# 비로그인 최적화 사용자가 자기 잡의 진행 상황을 못 보게 된다.
#
# 고칠 것은 **한 값이 두 뜻을 갖는 것**이다. `owner=None` 이 지금
# "소유자 검사를 하지 마" 와 "익명 호출자" 를 동시에 뜻한다. 셋을 갈라야
# 한다:
#
#     소유자 검사 안 함   (내부 호출·정리 작업)      → 전부 보인다
#     익명 호출자        (`optional_user` 가 None)  → 소유자 **없는** 잡만
#     로그인 호출자       uid                        → 자기 것 + 소유자 없는 것
#
# 가운데가 지금 없다. 셋째 줄의 규칙(`user_id not in (None, owner)`)은 이미
# 맞으므로, 익명을 그 규칙에 태우면 된다 — 예를 들어 라우터가 `owner` 를
# 안 넘기는 대신 "익명" 을 나타내는 별도 표식을 넘기고, `get` 이 그때
# `user_id is None` 인 잡만 돌려주는 식이다.
#
# 고칠 자리가 `backend/routers/optimizer.py` 와
# `backend/services/job_store.py` 라 이 창 소유가 아니다. 고쳐지면 XPASS 로
# 뒤집혀 이 표시를 떼라고 요구한다.

@pytest.mark.xfail(strict=True, reason=(
    "owner=None 이 '익명' 과 '소유자 검사 생략' 을 동시에 뜻한다. "
    "엔드포인트가 optional_user 라, 로그인하지 않은 호출자는 소유자 검사를 "
    "통째로 건너뛰고 남의 잡을 읽고 취소할 수 있다. 고치는 방향은 '익명이면 "
    "막는다' 가 아니라 그 두 뜻을 다른 값으로 가르는 것이다 — 위 설명 참고. "
    "자리: routers/optimizer.py · services/job_store.py."))
def test_an_anonymous_caller_cannot_read_someone_elses_job(client, store):
    """로그인하지 않은 호출자도 남의 잡은 못 읽어야 한다."""
    job_id = _make_job(store, OWNER)
    client.as_user(None)

    r = client.get(f"/api/optimizer/ai-optimize-job/{job_id}")

    assert r.status_code == 404, (
        f"an anonymous caller read a signed-in user's job ({r.status_code}) -- "
        "owner=None skips the check instead of meaning 'anonymous'."
    )


def test_this_is_what_the_anonymous_path_does_today(client, store):
    """지금 동작을 그대로 적어 둔다 — 위 xfail 이 무엇을 기다리는지.

    고쳐지는 순간 이 검사도 같이 빨개져서 둘을 함께 지우게 한다.
    """
    job_id = _make_job(store, OWNER)
    client.as_user(None)

    read = client.get(f"/api/optimizer/ai-optimize-job/{job_id}")
    assert read.status_code == 200 and "주인의" in read.text, (
        f"the anonymous path changed ({read.status_code}) -- update or delete "
        "this note together with the xfail above."
    )

    # 취소도 같은 `get()` 을 지나므로 같이 열린다. 다만 **실행 중일 때만**
    # 실제로 상태가 바뀐다 — 끝난 잡에 대고 불러도 아무 일도 없다.
    running = str(uuid.uuid4())
    store.set(running, {"status": "running"}, owner=OWNER)
    assert client.delete(f"/api/optimizer/ai-optimize-job/{running}").status_code == 200

    client.as_user(OWNER)
    assert store.get(running, owner=OWNER)["status"] == "cancelled", (
        "취소는 안 열려 있다 — 그렇다면 위 설명에서 취소를 빼야 한다"
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
    변이로 확인했다: DB 경로의 `if owner ...` 를 지우면 다른 검사는 전부
    초록이고 여기만 빨개진다. 소스 형태 비교라 검사 문구가 바뀌면 같이
    고쳐야 하는데, 그 비용을 내고서라도 둘 중 하나만 고쳐지는 것을 막는다.
    """
    import inspect

    from backend.services import job_store

    db_path = inspect.getsource(job_store.JobStore.get)
    mem_path = inspect.getsource(job_store.JobStore._mem_get)

    rule = "not in (None, owner)"
    assert rule in db_path and rule in mem_path, (
        "the DB path and the in-memory fallback no longer share the same "
        f"owner rule ({rule!r}) -- a mismatch opens other people's jobs only "
        "while the database is unreachable, which is nearly impossible to "
        "notice. (DB 가 흔들리는 동안만 열린다.)"
    )

"""
`users_repo` 가 docstring 으로 **약속한 것**들을 실제로 지키는지.

이 파일의 함수들은 전부 `except` 로 감싸여 있고 전부 무언가를 돌려준다.
CLAUDE.md §1.3 의 판단 기준 그대로 물으면 — *이게 실패한 채로 계속 가면
누가 언제 알게 되는가?* — 답은 대부분 "아무도" 다. 권한 판정이 조용히
열리거나, 아이디가 겹치거나, 사용자가 고른 이름이 로그인마다 덮어써져도
호출자가 받는 값은 정상 경로와 같은 모양이다.

§1.3 은 `users_repo.is_admin` 을 **좋은 예**로 인용한다(예외 시
`logger.error` + `return False`). 인용된 적은 있어도 **잰 적은 없다.**

## 실DB 를 쓴다

`upsert_user` 의 `ON CONFLICT ... COALESCE`, 아이디 유니크 인덱스,
`touch_login` 이 행을 만들지 않는다는 것 — 셋 다 **SQL 이 하는 일**이다.
커서를 모킹하면 내가 SQL 을 어떻게 읽었는지만 검증된다 (§6 의 전사본 사고와
같은 형태). 붙는 DB 가 이 창 것인지는 `live_db` 가 확인한다.

실패 주입은 `get_conn` 을 깨뜨려서 한다. 함수를 통째로 모킹하면 재려는
`except` 블록 자체가 사라진다.
"""
from __future__ import annotations

import logging

import pytest

from backend.db import users_repo

# 이 파일이 만드는 행. `LIKE` 는 `_` 가 와일드카드라 접두사 비교에 쓰면
# 남의 행까지 지운다 — `LEFT()` 로 정확히 자른다.
_PREFIX = "__TEST_U_"


@pytest.fixture
def rows(live_db):
    """이 파일이 만든 사용자 행만 읽고, 앞뒤로 지운다."""
    from backend.db import get_conn

    def purge():
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE LEFT(id, %s) = %s",
                        (len(_PREFIX), _PREFIX))

    def read(uid, *cols):
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT {', '.join(cols)} FROM users WHERE id=%s", (uid,))
            return cur.fetchone()

    purge()
    yield read
    purge()


@pytest.fixture
def unreachable_db(monkeypatch):
    """`users` 조회·쓰기만 실패시킨다. 함수는 실물이 돈다."""
    monkeypatch.setattr(users_repo, "get_conn",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("DB 끊김")))


# ── 안전장치는 실패하면 닫힌다 (§1.3 c) ────────────────────────────────────────

def test_is_admin_is_false_when_the_lookup_fails(rows, unreachable_db, caplog):
    """권한 조회가 실패하면 관리자가 아니다 — **그리고 기록이 남는다.**

    열린 채로 통과하면 DB 가 흔들리는 동안 아무나 관리자 화면을 연다.
    조용히 닫히기만 해도 곤란하다: 관리자가 "왜 안 되지" 로 읽고 원인을
    찾을 단서가 없다.
    """
    with caplog.at_level(logging.ERROR):
        assert users_repo.is_admin(f"{_PREFIX}admin") is False, (
            "a failed permission lookup granted admin -- the check must close "
            "when it cannot answer. (판정 못 하면 닫는다.)"
        )

    assert caplog.records, (
        "the admin check failed and nothing was logged -- an outage that "
        "silently strips admin rights leaves no trace to find it by."
    )


def test_is_admin_is_true_for_a_real_admin(rows):
    """대조군 — 실제 관리자는 True 다.

    없으면 위 검사는 "언제나 False" 라는 구현으로도 통과한다. 그건 관리자
    기능이 통째로 죽은 것이고, 초록불이 그걸 정상이라고 말한다.
    """
    uid = f"{_PREFIX}admin"
    assert users_repo.upsert_user(uid, email=f"{uid}@example.com") is True
    assert users_repo.set_admin(uid, True) is True

    assert users_repo.is_admin(uid) is True, "실제 관리자를 관리자로 못 읽는다"


def test_is_admin_is_false_for_an_ordinary_user(rows):
    """대조군 반대편 — 일반 사용자는 False 다."""
    uid = f"{_PREFIX}plain"
    users_repo.upsert_user(uid, email=f"{uid}@example.com")

    assert users_repo.is_admin(uid) is False, "일반 사용자가 관리자로 읽힌다"


def test_revoking_admin_actually_takes_effect(rows):
    """권한 회수가 즉시 반영된다.

    권한을 DB 에 둔 이유가 이것이다 — 토큰의 custom claim 은 갱신 전까지
    옛 값을 들고 있어 회수가 늦는다. DB 쪽이 늦으면 그 근거가 사라진다.
    """
    uid = f"{_PREFIX}revoked"
    users_repo.upsert_user(uid, email=f"{uid}@example.com")
    users_repo.set_admin(uid, True)
    assert users_repo.is_admin(uid) is True, "전제: 관리자였다"

    assert users_repo.set_admin(uid, False) is True
    assert users_repo.is_admin(uid) is False, (
        "admin was revoked but the check still says admin -- the whole reason "
        "permissions live in the DB is that revocation takes effect at once."
    )


def test_set_admin_reports_failure_instead_of_claiming_success(live_db, unreachable_db,
                                                               caplog):
    """권한 부여가 실패하면 False 다 — 성공했다고 말하지 않는다.

    시드 스크립트가 True 를 받고 끝나면 "관리자를 만들었다" 고 믿은 채
    아무도 관리자가 아닌 상태가 남는다.
    """
    with caplog.at_level(logging.ERROR):
        assert users_repo.set_admin(f"{_PREFIX}nobody", True) is False

    assert caplog.records, "권한 부여가 실패했는데 아무 기록도 없다"


def test_an_unverifiable_username_is_treated_as_taken(live_db, unreachable_db, caplog):
    """중복을 확인하지 못하면 '사용 중' 으로 본다.

    반대로 열면 DB 가 흔들리는 동안 같은 아이디가 여러 개 생긴다. 아이디는
    로그인 식별자라 그 상태는 나중에 사람이 손으로 풀어야 한다.
    """
    with caplog.at_level(logging.ERROR):
        assert users_repo.username_taken("anything") is True, (
            "a username was declared free while the check could not run -- "
            "duplicates created this way have to be untangled by hand later."
        )

    assert caplog.records, "중복 확인이 실패했는데 아무 기록도 없다"


def test_the_guards_stay_closed_when_the_database_is_not_configured(monkeypatch):
    """DB 에 아예 붙지 않은 상태도 닫힌 쪽이다.

    이건 `except` 가 아니라 함수 첫 줄의 `is_available()` 이 처리한다 —
    **다른 분기다.** 두 상태를 같이 재지 않으면 한쪽만 열려 있어도 모른다.

    풀을 실제로 닫아서 만들지 않고 `is_available` 을 눌러 만든다. 이 모듈은
    `live_db` 가 모듈 스코프라 풀이 내내 열려 있고, "앞 검사가 닫아 줬겠지" 에
    기대면 실행 순서가 바뀌는 순간 조용히 다른 것을 재게 된다. 이 파일을
    쓰면서 실제로 그 함정에 빠졌다 — 풀이 닫힌 채로는 `except` 블록에
    **도달조차 하지 않는데**, 옆 검사가 열어 둔 풀 덕분에 통과하고 있었다.
    """
    monkeypatch.setattr(users_repo, "is_available", lambda: False)

    assert users_repo.is_admin("anyone") is False
    assert users_repo.set_admin("anyone", True) is False
    assert users_repo.username_taken("anything") is True
    assert users_repo.find_by_email("someone@example.com") is None
    assert users_repo.touch_login("anyone") is False
    assert users_repo.delete_user("anyone") == {"deleted": False}


def test_an_unused_username_is_free(rows):
    """대조군 — 안 쓰는 아이디는 비어 있다.

    없으면 위 검사는 "언제나 사용 중" 이라는 구현으로도 통과하고, 그러면
    아무도 아이디를 만들 수 없다.
    """
    assert users_repo.username_taken(f"{_PREFIX}free") is False


def test_an_empty_username_is_never_free():
    """빈 아이디는 DB 를 보지 않고도 거절한다 — 빈 값은 유니크 인덱스에 안 걸린다."""
    assert users_repo.username_taken("") is True


# ── 아이디는 대소문자를 구분하지 않는다 ────────────────────────────────────────
#
# 스키마가 `LOWER(username)` 에 유니크 인덱스를 건 이유가 여기 적혀 있다 —
# 'Foo' 와 'foo' 가 다른 계정이 되면 사칭에 쓰인다. `set_username` 은 그
# 인덱스를 근거로 "경쟁 상황에서도 안전하다" 고 적고 있는데, 그 근거가
# 실제로 서 있는지는 잰 적이 없다. 인덱스가 사라지면 그 주석만 남는다.

def test_the_same_username_in_different_case_is_taken(rows):
    """대소문자만 다른 아이디는 이미 쓰는 것으로 본다."""
    uid = f"{_PREFIX}case"
    users_repo.upsert_user(uid, email=f"{uid}@example.com", username="ZoopZoop")

    assert users_repo.username_taken("zoopzoop") is True, (
        "'zoopzoop' was free while 'ZoopZoop' exists -- case-distinct names "
        "let one account impersonate another. (사칭에 쓰인다.)"
    )


def test_a_second_account_cannot_claim_the_same_username(rows):
    """두 번째 계정은 같은 아이디를 가져가지 못한다 — **중복 확인을 건너뛰어도.**

    `username_taken` 과 `set_username` 사이에는 창이 있다. 두 요청이 그
    창에 같이 들어가면 둘 다 "비어 있다" 를 받는다. 막는 것은 유니크
    인덱스뿐이고, `set_username` 의 "경쟁 상황에서도 안전하다" 는 그
    인덱스를 근거로 한 주장이다.
    """
    first, second = f"{_PREFIX}one", f"{_PREFIX}two"
    users_repo.upsert_user(first, email=f"{first}@example.com")
    users_repo.upsert_user(second, email=f"{second}@example.com")

    assert users_repo.set_username(first, "sharedname") is True, "전제: 첫 번째는 된다"

    assert users_repo.set_username(second, "SharedName") is False, (
        "two accounts ended up with the same login identifier -- the unique "
        "index that set_username's docstring relies on is not doing it. "
        "(아이디가 겹치면 사람이 손으로 풀어야 한다.)"
    )


# ── 로그인이 사용자가 고친 것을 덮어쓰지 않는다 ────────────────────────────────

def test_relogin_keeps_the_name_the_user_chose(rows):
    """재로그인이 표시이름·사진·아이디를 되돌리지 않는다.

    로그인마다 공급자가 준 값으로 덮어쓰면, 사용자가 바꾼 이름이 다음
    로그인에 사라진다. 되돌아간 이유가 화면 어디에도 없어서 사용자는
    "저장이 안 된다" 로 읽는다.
    """
    uid = f"{_PREFIX}named"
    users_repo.upsert_user(uid, email=f"{uid}@example.com", name="auto_generated",
                           provider="google", photo_url="https://old/pic.png",
                           username="autoname")
    assert users_repo.update_profile(uid, name="내가 고른 이름") is True

    users_repo.upsert_user(uid, email=f"{uid}@example.com", name="auto_generated",
                           provider="google", photo_url="https://new/pic.png",
                           username="othername")

    name, photo, username = rows(uid, "name", "photo_url", "username")
    assert name == "내가 고른 이름", f"로그인이 사용자가 고른 이름을 덮어썼다: {name!r}"
    assert photo == "https://old/pic.png", f"사진이 덮어써졌다: {photo!r}"
    assert username == "autoname", f"아이디가 바뀌었다: {username!r}"


def test_relogin_does_refresh_what_the_provider_owns(rows):
    """대조군 — 공급자가 원본인 값은 갱신된다.

    없으면 위 검사는 "재로그인이 아무것도 안 바꾼다" 는 구현으로도 통과한다.
    그러면 이메일 인증을 마쳐도 미인증으로 남아 인증 전용 기능이 계속 막힌다.
    """
    uid = f"{_PREFIX}refresh"
    users_repo.upsert_user(uid, email=f"{uid}@example.com", provider="password",
                           email_verified=False)
    assert rows(uid, "email_verified")[0] is False, "전제: 미인증이었다"

    users_repo.upsert_user(uid, email=f"{uid}@example.com", provider="google",
                           email_verified=True)

    verified, provider = rows(uid, "email_verified", "provider")
    assert verified is True, "이메일 인증을 마쳤는데 미인증으로 남았다"
    assert provider == "google", f"공급자가 갱신되지 않았다: {provider!r}"


def test_the_stored_email_is_lowercased(rows):
    """이메일은 소문자로 저장된다 — 조회가 `LOWER(email)` 로 걸리기 때문이다.

    대문자로 들어가면 유니크 인덱스는 `LOWER()` 라 중복은 막히는데,
    `find_by_email` 은 양쪽을 `LOWER()` 하므로 조회는 된다. 즉 지금은
    어긋나도 증상이 안 보인다 — 그래서 어긋난 채로 남는다.
    """
    uid = f"{_PREFIX}mixed"
    users_repo.upsert_user(uid, email=f"{_PREFIX}Mixed@Example.COM")

    assert rows(uid, "email")[0] == f"{_PREFIX}mixed@example.com".lower()


# ── touch_login 은 행을 만들지 않는다 ──────────────────────────────────────────

def test_touch_login_does_not_create_a_row(rows):
    """가입한 적 없는 uid 로 부르면 행이 생기지 않는다.

    생기면 "가입 여부" 판정이 무너진다 — 가입 절차를 거치지 않은 계정이
    가입된 것으로 보이고, 소셜 로그인 버튼만으로 계정이 생긴다.
    """
    uid = f"{_PREFIX}ghost"

    assert users_repo.touch_login(uid) is False, (
        "touch_login reported success for an account that was never "
        "registered -- registration would stop meaning anything."
    )
    assert rows(uid, "id") is None, "가입한 적 없는 uid 로 행이 생겼다"


def test_touch_login_updates_an_existing_row(rows):
    """대조군 — 있는 행이면 최종 로그인 시각을 갱신한다.

    없으면 위 검사는 "아무것도 안 한다" 는 구현으로도 통과하고, 그러면
    마지막 로그인 시각이 영영 가입 시점에 멈춘다.
    """
    uid = f"{_PREFIX}present"
    users_repo.upsert_user(uid, email=f"{uid}@example.com")
    before = rows(uid, "last_login_at")[0]

    assert users_repo.touch_login(uid) is True
    assert rows(uid, "last_login_at")[0] >= before


# ── 삭제 ───────────────────────────────────────────────────────────────────────

def test_deleting_an_unknown_user_is_not_reported_as_done(rows):
    """없는 사용자를 지웠다고 말하지 않는다.

    탈퇴 화면이 "삭제 완료" 를 띄웠는데 아무것도 안 지워졌으면, 사용자는
    개인 데이터가 사라졌다고 믿는다. 그건 되돌릴 수 없는 오해다.
    """
    out = users_repo.delete_user(f"{_PREFIX}nosuch")

    assert out.get("deleted") is False, f"없는 사용자를 지웠다고 보고했다: {out}"


def test_deleting_a_real_user_removes_the_row(rows):
    """대조군 — 있는 사용자는 실제로 지워진다."""
    uid = f"{_PREFIX}bye"
    users_repo.upsert_user(uid, email=f"{uid}@example.com")
    assert rows(uid, "id") is not None, "전제: 행이 있다"

    out = users_repo.delete_user(uid)

    assert out["deleted"] is True, f"실제 사용자가 안 지워졌다: {out}"
    assert rows(uid, "id") is None

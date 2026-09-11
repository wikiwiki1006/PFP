"""
**조회 실패는 "계정 없음" 이 아니다.**

`_reconcile_account` 는 한쪽에만 남은 잔해를 치운다. 판단 근거는 두 개다 —
우리 DB 의 행(`users_repo.find_by_email`)과 Firebase 의 인증 계정. 행이 없고
인증 계정만 있으면 "가입 기록 없는 고아" 로 보고 **인증 계정을 지운다.**

그 판단이 성립하려면 `find_by_email(...) is None` 이 "그런 계정이 없다" 를
뜻해야 한다. 그런데 그 함수는 예외를 삼키고 같은 `None` 을 돌려준다:

    except Exception as e:
        logger.error(f"find_by_email 실패: {e}")
        return None

즉 **DB 가 잠깐 끊긴 동안 들어온 가입 시도가 실사용자의 인증 계정을
지웠다.** 지워지면 그 사람은 로그인할 수 없고, 되돌릴 방법도 없다. 이메일만
알면 누구나 그 시도를 할 수 있으므로 인증도 필요 없었다.

CLAUDE.md §1.3(c) 그대로다 — 실패한 경로의 반환값이 정상 경로의 반환값과
구별되지 않는다.

## 지금은 못 읽으면 올려보낸다

`find_by_email` 은 세 가지를 **다르게** 말한다:

    이메일이 없다      → None      (입력이 없는 것. 진짜 "없음")
    읽었는데 행이 없다  → None      (진짜 "없음")
    못 읽었다          → DBBusy    (판단 불가)

`DBBusy` 는 `main.py` 의 핸들러가 **503 + 메시지**로 번역한다. 그래서
호출부 다섯 곳을 하나도 안 고치고 전부 옳아진다 — `_reconcile_account` 는
삭제 대신 503 으로 멈추고, `/email-available` 은 "사용 가능" 대신 "확인할
수 없음", 소셜 가입은 중복 검사를 건너뛰는 대신 503 이 된다.

**두 번째 방법(`_reconcile_account` 만 멈추기)은 왜 아니었나**: 그러면
`/email-available` 과 소셜 가입이 조용히 틀린 채로 남는다. 다섯 중 셋이
읽기 실패에 대해 틀렸고, 셋이 서로 다른 대응을 필요로 했다.

**`is_registered` 방식은 왜 아니었나**: 그 함수의 근거는 uid 로 걸린
캐시인데 여기는 이메일로 찾는다 — 두 번째 근거가 없다. 그리고
`is_registered` 는 실패 시 **닫히지만**(거절) 이 경로는 실패 시
**파괴적**이다(삭제). 안전한 기본값이 "캐시를 믿기" 가 아니라 "아무것도
하지 않고 알리기" 다.
"""
from __future__ import annotations

import sys
import types

import pytest

import backend.routers.auth as auth_mod
from backend.db import users_repo

EMAIL = "real-user@example.com"


class _FakeFbUser:
    def __init__(self, uid):
        self.uid = uid


@pytest.fixture
def broken_db(monkeypatch):
    """`users` 조회만 실패시킨다 — 함수 대역이 아니라 **진짜 코드**가 돌게.

    `find_by_email` 을 통째로 모킹하면 이 파일이 재려는 것(예외를 삼키고
    None 을 돌려주는 그 자리)이 사라진다. 그래서 그 아래 `get_conn` 을
    깨뜨리고 함수는 실물을 쓴다.
    """
    monkeypatch.setattr(users_repo, "is_available", lambda: True)
    monkeypatch.setattr(users_repo, "get_conn",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("DB 끊김")))


@pytest.fixture
def broken_db_pool(monkeypatch):
    """풀 고갈 — 이 리포가 **503 으로 번역할 줄 아는** 실패다."""
    from backend.db import PoolExhausted

    monkeypatch.setattr(users_repo, "is_available", lambda: True)
    monkeypatch.setattr(users_repo, "get_conn",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            PoolExhausted("커넥션 풀이 가득 찼습니다")))


@pytest.fixture
def firebase_account(monkeypatch):
    """Firebase 에 그 이메일 계정이 **있다**. 삭제 호출을 기록만 하고 막는다."""
    deleted: list[str] = []

    monkeypatch.setattr(auth_mod, "_fb_user_by_email",
                        lambda e: _FakeFbUser("uid-of-a-real-user") if e == EMAIL else None)
    monkeypatch.setattr(auth_mod, "init_firebase", lambda: True)
    monkeypatch.setattr(auth_mod, "forget_registration", lambda uid: None)
    # 정리는 운영에서만 일어난다. 로컬 가드에 걸려 안 지워지는 것을
    # "안전하다" 로 읽으면 안 되므로 운영으로 둔다.
    monkeypatch.setattr(auth_mod, "_IS_MANAGED_RUNTIME", True)

    mod = types.ModuleType("firebase_admin.auth")
    mod.delete_user = deleted.append
    monkeypatch.setitem(sys.modules, "firebase_admin.auth", mod)
    return deleted


def test_the_lookup_really_does_fail(broken_db):
    """전제 — 이 조건에서 조회가 실제로 실패한다.

    이게 없으면 아래 검사는 조회가 멀쩡히 성공한 상태를 재고 있을 수 있다.
    그러면 "계정이 안 지워졌다" 는 결과가 **버그가 없어서가 아니라 조건을
    못 만들어서** 나온 것이 된다.
    """
    from backend.db import DBBusy

    with pytest.raises(DBBusy):
        users_repo.find_by_email(EMAIL)


def test_a_database_outage_does_not_delete_a_real_auth_account(broken_db, firebase_account):
    """DB 를 못 읽었다는 이유로 인증 계정을 지우면 안 된다.

    지워진 사람은 로그인할 수 없고 되돌릴 방법이 없다. 이메일만 알면
    인증 없이도 이 경로를 부를 수 있다.

    멈추는 방식은 **예외로 올라가는 것**이다 — 조용히 None 을 돌려주고
    말면 호출부가 "정리할 것이 없었다" 로 읽어 아무 기록도 안 남는다.
    """
    from backend.db import DBBusy

    with pytest.raises(DBBusy):
        auth_mod._reconcile_account(EMAIL)

    assert firebase_account == [], (
        f"a real user's auth account was deleted because the database lookup "
        f"failed: {firebase_account} -- a failed read is not a missing row, "
        "and this deletion cannot be undone. "
        "(DB 를 못 읽은 것과 계정이 없는 것은 다르다.)"
    )


# ── 실패의 종류가 달라도 결론은 하나다 ────────────────────────────────────────
#
# 풀 고갈(`PoolExhausted`)과 연결 끊김(`psycopg2.OperationalError`)은 다른
# 예외지만 이 함수에서는 같은 뜻이다 — **못 읽었다.** 읽는 데 성공했는데
# 행이 없으면 그건 `except` 가 아니라 `if r else None` 으로 나간다. 즉
# `except` 에 도달했다는 것 자체가 "판단 불가" 이므로, 종류를 가리지 않고
# 같은 신호로 올린다.
#
# 좁게(`DBBusy` 만) 잡으면 **가장 흔한 실패가 그대로 남는다** — 연결 끊김은
# `DBBusy` 가 아니라 `psycopg2.OperationalError` 다. 그게 바로 이 파일이
# 막으려는 조건이다.

def test_every_kind_of_read_failure_is_reported(broken_db_pool):
    """풀 고갈도, 연결 끊김도, 같은 "못 읽었다" 로 올라온다.

    한 종류만 올리면 나머지는 계속 "그런 계정 없음" 이 되고, 위 검사가
    보여주듯 그 다음은 삭제다.
    """
    from backend.db import DBBusy

    with pytest.raises(DBBusy):
        users_repo.find_by_email(EMAIL)


def test_a_reachable_database_leaves_the_account_alone(firebase_account, monkeypatch):
    """대조군 — DB 를 읽을 수 있으면 정상 계정은 건드리지 않는다.

    없으면 위 검사는 "아무것도 안 지운다" 는 구현으로도 통과한다. 그건
    반대 방향의 결함이다 — 잔해가 영영 남아 그 이메일이 막힌다.
    """
    monkeypatch.setattr(users_repo, "find_by_email",
                        lambda e: {"uid": "uid-of-a-real-user", "username": "real",
                                   "provider": "password", "disabled": False})
    monkeypatch.setattr(auth_mod.users_repo, "find_by_email",
                        lambda e: {"uid": "uid-of-a-real-user", "username": "real",
                                   "provider": "password", "disabled": False})

    row = auth_mod._reconcile_account(EMAIL)

    assert row and row["uid"] == "uid-of-a-real-user"
    assert firebase_account == [], f"정상 계정을 지웠다: {firebase_account}"


def test_an_orphan_auth_account_is_still_cleaned_up(broken_db, firebase_account,
                                                    monkeypatch):
    """대조군 — 진짜 고아는 계속 지워져야 한다.

    위 검사를 "인증 계정을 절대 지우지 않는다" 로 고치면 이 검사가 빨개진다.
    한쪽만 남은 잔해를 안 치우면 그 이메일로 영영 다시 가입할 수 없다 —
    그게 이 정리 코드가 생긴 이유다.
    """
    monkeypatch.setattr(auth_mod.users_repo, "find_by_email", lambda e: None)

    auth_mod._reconcile_account(EMAIL)

    assert firebase_account == ["uid-of-a-real-user"], (
        "an orphaned auth account was left in place -- that email can never be "
        "registered again. (잔해를 안 치우면 그 이메일이 영영 막힌다.)"
    )

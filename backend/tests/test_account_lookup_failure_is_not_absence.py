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
지운다.** 지워지면 그 사람은 로그인할 수 없고, 되돌릴 방법도 없다. 이메일만
알면 누구나 그 시도를 할 수 있으므로 인증도 필요 없다.

CLAUDE.md §1.3(c) 그대로다 — 실패한 경로의 반환값이 정상 경로의 반환값과
구별되지 않는다. 같은 리포의 `services/auth.py:is_registered` 는 이미 그
구분을 한다: DB 를 못 읽었을 뿐이고 캐시가 유효하면 통과시키고, 근거가 전혀
없을 때만 막는다. 한쪽은 알고 한쪽은 모른다.

## 이 파일의 상태

아래 검사는 **지금 빨갛다.** 고칠 자리가 둘 다 내 소유가 아니라
(`backend/routers/auth.py` · `backend/db/users_repo.py`) xfail(strict) 로
둔다. 고쳐지면 XPASS 로 뒤집혀 이 표시를 떼라고 요구한다 — 통과하는데
xfail 로 남아 있는 것도 실패로 잡힌다.

고치는 방법은 둘 중 하나다.
  · `find_by_email` 이 "없음"(None)과 "못 읽음"을 구별해서 돌려준다.
  · `_reconcile_account` 가 DB 를 못 읽었을 때는 정리하지 않고 5xx 로 멈춘다.
    (이미 인증 계정 삭제에 실패했을 때 그렇게 한다 — 같은 판단이다.)
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
    """전제 — 이 조건에서 `find_by_email` 이 `None` 을 돌려준다.

    이게 없으면 아래 검사는 조회가 멀쩡히 성공한 상태를 재고 있을 수 있다.
    그러면 "계정이 안 지워졌다" 는 결과가 **버그가 없어서가 아니라 조건을
    못 만들어서** 나온 것이 된다.
    """
    assert users_repo.find_by_email(EMAIL) is None


@pytest.mark.xfail(strict=True, reason=(
    "조회 실패와 계정 없음이 같은 None 이라, DB 가 끊긴 동안의 가입 시도가 "
    "실사용자의 인증 계정을 지운다. 고칠 자리가 routers/auth.py 와 "
    "db/users_repo.py 라 이 창 소유가 아니다 — 파일 맨 위 설명 참고."))
def test_a_database_outage_does_not_delete_a_real_auth_account(broken_db, firebase_account):
    """DB 를 못 읽었다는 이유로 인증 계정을 지우면 안 된다.

    지워진 사람은 로그인할 수 없고 되돌릴 방법이 없다. 이메일만 알면
    인증 없이도 이 경로를 부를 수 있다.
    """
    auth_mod._reconcile_account(EMAIL)

    assert firebase_account == [], (
        f"a real user's auth account was deleted because the database lookup "
        f"failed: {firebase_account} -- a failed read is not a missing row, "
        "and this deletion cannot be undone. "
        "(DB 를 못 읽은 것과 계정이 없는 것은 다르다.)"
    )


# ── 이 리포는 이미 "다시 시도하면 되는 실패" 를 알고 있다 ──────────────────────
#
# `backend/db` 에 `DBBusy` 와 `PoolExhausted` 가 있고, `main.py` 의 핸들러가
# 그 계열을 **503 + 메시지**로 번역한다. 즉 "지금은 못 읽었다" 를 사용자에게
# 전할 통로가 이미 깔려 있다.
#
# 그런데 `find_by_email` 의 `except Exception` 이 그걸 **삼킨다.** 풀 고갈은
# 부하가 몰릴 때 나고, 부하가 몰릴 때는 가입도 몰린다 — 즉 이 경로가 제일
# 많이 불리는 순간에 제일 잘 터진다.
#
# 그래서 고치는 방법으로 파일 맨 위에 적어 둔 둘 중, **첫 번째가 이 리포에
# 맞다**: 반환값을 넓히는 대신 이 계열을 그냥 올려보내면 된다. 호출부 다섯
# 곳을 하나도 안 고치고 전부 옳아진다 (자세한 근거는 pfp-11 에게 보낸 보고).

def test_a_retryable_failure_is_swallowed_here(broken_db_pool):
    """풀 고갈처럼 **앱이 이미 번역할 줄 아는 실패**도 여기서 사라진다.

    `PoolExhausted` 는 `main.py` 가 503 으로 바꿔 "잠시 후 다시" 를 보낼 수
    있는 예외다. 여기서 삼키면 그 신호가 "그런 계정 없음" 이 되고, 위
    검사가 보여주듯 그 다음은 삭제다.
    """
    assert users_repo.find_by_email(EMAIL) is None, (
        "if this now raises, the swallow is gone -- update the note above and "
        "the two fix options at the top of this file."
    )


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

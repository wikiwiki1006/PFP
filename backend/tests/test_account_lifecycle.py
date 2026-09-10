"""
계정이 두 곳(Firebase 인증 / 우리 DB)에 나뉘어 있어 생기는 꼬임을 막는다.

탈퇴는 두 곳을 모두 지워야 한다. 한쪽만 지워지면 사용자는 이도 저도 못 한다:
  · DB 행만 남음   → 가입하면 "이미 가입된 이메일", 로그인하면 인증 실패
  · 인증 계정만 남음 → 가입하면 "이미 존재", 로그인하면 "가입되지 않은 계정"

실제로 그 상태에 빠진 계정이 운영에 여럿 있었다. _reconcile_account 는 한쪽에만
남은 잔해를 치워 그 이메일로 다시 가입할 수 있게 한다.
"""
import pytest

import backend.routers.auth as auth_mod


class _FakeFbUser:
    def __init__(self, uid): self.uid = uid


@pytest.fixture
def env(monkeypatch):
    """DB 행과 인증 계정을 각각 흉내낸다."""
    state = {"db": {}, "fb": {}, "deleted_db": [], "deleted_fb": []}

    monkeypatch.setattr(auth_mod.users_repo, "find_by_email",
                        lambda e: state["db"].get(e))
    def _del(uid):
        state["deleted_db"].append(uid)
        for k, v in list(state["db"].items()):
            if v["uid"] == uid:
                del state["db"][k]
        return {"deleted": True}
    monkeypatch.setattr(auth_mod.users_repo, "delete_user", _del)
    monkeypatch.setattr(auth_mod, "forget_registration", lambda uid: None)
    monkeypatch.setattr(auth_mod, "_fb_user_by_email",
                        lambda e: (_FakeFbUser(state["fb"][e]) if e in state["fb"] else None))

    class _FbAuth:
        @staticmethod
        def delete_user(uid):
            state["deleted_fb"].append(uid)
            for k, v in list(state["fb"].items()):
                if v == uid:
                    del state["fb"][k]
    import sys, types
    mod = types.ModuleType("firebase_admin.auth")
    mod.delete_user = _FbAuth.delete_user
    monkeypatch.setitem(sys.modules, "firebase_admin.auth", mod)
    monkeypatch.setattr(auth_mod, "init_firebase", lambda: True)
    # 정리 동작은 운영에서만 일어난다 (로컬은 운영 계정을 지키느라 막는다).
    # 기본을 운영으로 두고, 로컬 동작을 보는 테스트가 따로 뒤집는다.
    monkeypatch.setattr(auth_mod, "_IS_MANAGED_RUNTIME", True)
    return state


EMAIL = "someone@example.com"


def test_healthy_account_is_left_alone(env):
    """양쪽 다 있으면 정상 계정이다 — 건드리지 않고 그대로 알린다."""
    env["db"][EMAIL] = {"uid": "u1", "provider": "password"}
    env["fb"][EMAIL] = "u1"

    got = auth_mod._reconcile_account(EMAIL)

    assert got is not None and got["uid"] == "u1"
    assert env["deleted_db"] == [] and env["deleted_fb"] == []


def test_db_row_without_auth_account_is_cleared(env):
    """탈퇴가 반만 처리돼 DB 행만 남은 경우 — 치워서 재가입을 열어 준다."""
    env["db"][EMAIL] = {"uid": "u2", "provider": "password"}

    assert auth_mod._reconcile_account(EMAIL) is None
    assert env["deleted_db"] == ["u2"]


def test_auth_account_without_db_row_is_cleared(env):
    """반대 방향 — 인증 계정만 남은 경우도 치운다."""
    env["fb"][EMAIL] = "u3"

    assert auth_mod._reconcile_account(EMAIL) is None
    assert env["deleted_fb"] == ["u3"]


def test_unknown_email_is_free(env):
    """양쪽 다 없으면 그냥 새 이메일이다."""
    assert auth_mod._reconcile_account(EMAIL) is None
    assert env["deleted_db"] == [] and env["deleted_fb"] == []


def test_orphan_cleanup_failure_is_not_swallowed(env, monkeypatch):
    """고아 인증 계정을 못 지우면 가입도 못 한다 — 조용히 넘어가면 원인 모를 실패가 된다."""
    env["fb"][EMAIL] = "u4"
    import sys
    def _boom(uid):
        raise RuntimeError("삭제 실패")
    sys.modules["firebase_admin.auth"].delete_user = _boom

    with pytest.raises(auth_mod.HTTPException) as e:
        auth_mod._reconcile_account(EMAIL)
    assert e.value.status_code == 503


# ── 탈퇴가 실제로 완결되는지 ────────────────────────────────────────────────

def test_shared_reports_are_anonymized_to_an_existing_row():
    """공용 리포트를 옮길 대상 행이 스키마에 실제로 만들어져야 한다.

    reports.user_id 는 NOT NULL 이고 users 를 참조한다. 옮길 행이 없으면
    익명화가 FK 위반으로 실패하고, 같은 트랜잭션의 DELETE 까지 통째로 롤백된다.
    그러면 탈퇴는 '실패'인데 호출부는 그걸 확인하지 않아 인증 계정만 지워졌고,
    로그인도 가입도 못 하는 계정이 운영에 쌓였다. 실제로 겪은 사고다.
    """
    from backend.db import schema
    from backend.db.users_repo import ANONYMIZED_UID

    ddl = schema._DDL
    assert f"'{ANONYMIZED_UID}'" in ddl, "익명 사용자 행을 만드는 DDL 이 없다"
    assert "INSERT INTO users" in ddl and "ON CONFLICT (id) DO NOTHING" in ddl


def test_anonymized_row_cannot_be_deleted(monkeypatch):
    """익명 행을 지우면 거기 매달린 공용 리포트가 CASCADE 로 함께 사라진다."""
    from backend.db import users_repo

    monkeypatch.setattr(users_repo, "is_available", lambda: True)
    res = users_repo.delete_user(users_repo.ANONYMIZED_UID)
    assert res["deleted"] is False


# ── 가입 확인 캐시 ──────────────────────────────────────────────────────────

def test_registration_cache_expires(monkeypatch):
    """캐시는 만료돼야 한다.

    예전에는 한 번 True 면 영원히 True 였다. users 행이 사라져도(다른
    인스턴스에서 탈퇴, 로컬 DB 초기화) 그 인스턴스는 계속 가입된 것으로 봤고,
    재시작하면 같은 계정이 갑자기 미가입이 됐다. 그래서 "가입하면 이미 가입된
    계정, 로그인하면 가입되지 않은 계정" 이 번갈아 나왔다.
    """
    import time
    import backend.services.auth as A
    import backend.db.users_repo as ur

    monkeypatch.setattr(ur, "get_user", lambda uid: None)
    monkeypatch.setattr(A, "_REGISTRATION_TTL_SECONDS", 0.05)
    A._registered.clear()

    A._registered["u"] = time.time()
    assert A.is_registered("u") is True          # 아직 유효
    time.sleep(0.08)
    assert A.is_registered("u") is False         # 만료 후 DB 로 재확인
    assert "u" not in A._registered              # 어긋난 항목은 치운다


def test_valid_cache_survives_db_outage(monkeypatch):
    """DB 를 못 읽었을 뿐이라면 진행 중인 요청을 끊지 않는다."""
    import time
    import backend.services.auth as A
    import backend.db.users_repo as ur

    def _boom(uid): raise RuntimeError("DB 끊김")
    monkeypatch.setattr(ur, "get_user", _boom)
    monkeypatch.setattr(A, "_REGISTRATION_TTL_SECONDS", 0.0)   # 항상 재확인
    A._registered.clear()

    A._registered["u"] = time.time()
    assert A.is_registered("u") is True           # 캐시가 있으면 통과
    A._registered.clear()
    assert A.is_registered("u") is False          # 근거가 없으면 막는다


def test_local_run_never_deletes_an_auth_account(env, monkeypatch):
    """로컬에서는 인증 계정을 지우지 않는다.

    Firebase 프로젝트는 로컬과 운영이 같은데 DB 는 다르다. 운영에 가입한
    이메일이 로컬 DB 에는 없어 '고아'로 보이는데, 그걸 지우면 실제 사용자의
    계정이 로컬 작업 때문에 사라진다.
    """
    monkeypatch.setattr(auth_mod, "_IS_MANAGED_RUNTIME", False)
    env["fb"][EMAIL] = "prod-uid"

    with pytest.raises(auth_mod.HTTPException) as e:
        auth_mod._reconcile_account(EMAIL)

    assert e.value.status_code == 409
    assert env["deleted_fb"] == []               # 지우지 않았다


# ── 로컬 고정 테스트 계정 ────────────────────────────────────────────────────

def test_local_test_account_is_reserved():
    """test@gmail.com 은 회원가입으로 만들 수도, 가로챌 수도 없어야 한다.

    로컬 개발이 항상 이 계정으로 들어가므로, 운영에서 누가 같은 주소로
    가입을 시도해 정리 로직이 인증 계정을 지워버리면 로컬 로그인이 조용히
    망가진다. 예약어로 막아 그 경로 자체를 없앤다.
    """
    assert auth_mod._is_reserved(auth_mod.LOCAL_TEST_EMAIL)
    assert auth_mod._is_reserved("  TEST@Gmail.com  ")      # 공백·대소문자 무관
    assert not auth_mod._is_reserved("someone-else@gmail.com")


def test_seed_script_refuses_to_run_in_production(monkeypatch):
    """이 계정은 로컬 전용이다 — 운영 DB 에 만들어지면 안 된다."""
    import importlib
    monkeypatch.setenv("K_SERVICE", "pfp-backend")
    mod = importlib.import_module("backend.scripts.seed_test_user")
    assert mod.main() == 1

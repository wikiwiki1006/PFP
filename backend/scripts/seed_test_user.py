"""
scripts/seed_test_user.py
─────────────────────────
로컬 개발용 고정 테스트 계정을 만든다.

    python -m backend.scripts.seed_test_user

왜 고정 계정을 쓰나
    Firebase 프로젝트는 로컬과 운영이 같고 DB 만 다르다. 그래서 로컬에서
    아무 이메일로나 가입하면 실서비스 계정과 같은 공간에 쌓이고, 운영에 이미
    있는 이메일로는 가입도 안 된다. 계정 하나를 미리 만들어 두고 로컬 테스트는
    항상 그걸로 하면 그런 충돌이 없다.

    이 주소는 _is_reserved 에 등록돼 있어 아무도 회원가입으로 만들 수 없다.
    운영에서 누가 같은 주소를 시도해도 정리 로직이 이 계정을 지우지 않는다 —
    지워지면 로컬 로그인이 조용히 망가진다.

여러 번 실행해도 안전하다. 이미 있으면 비밀번호와 DB 행만 맞춘다.
DB 는 현재 설정된 곳에 만들어진다 — DATABASE_URL 을 주지 않으면 로컬 DB 다.
"""
from __future__ import annotations

import os
import sys

from backend.db import init_pool, is_available
from backend.db import users_repo
from backend.db.schema import init_schema
from backend.routers.auth import LOCAL_TEST_EMAIL
from backend.services.auth import init_firebase

DEFAULT_PASSWORD = "10october@"


def main() -> int:
    password = os.getenv("TEST_USER_PASSWORD") or DEFAULT_PASSWORD

    if os.getenv("K_SERVICE") or os.getenv("GAE_ENV"):
        print("✗ 운영 환경에서는 실행할 수 없습니다. 로컬 개발용 스크립트입니다.",
              file=sys.stderr)
        return 1

    if not is_available():
        init_pool()
    if not is_available():
        print("✗ DB에 연결할 수 없습니다. 로컬 PostgreSQL 이 켜져 있는지 확인하세요.",
              file=sys.stderr)
        return 1
    init_schema()

    if not init_firebase():
        print("✗ Firebase Admin SDK 를 초기화할 수 없습니다.", file=sys.stderr)
        return 1
    from firebase_admin import auth as fb_auth

    # 0) DB 에 이미 이 계정이 있으면 **그 uid 를 그대로 쓴다.**
    #
    # 예전에는 인증 계정을 uid 없이 만들어(무작위 uid) 그 uid 로 DB 행을 upsert 했다.
    # 그런데 각 창 DB 는 개발 데이터 사본이라 test@gmail.com 행이 이미 있고,
    # users.email 에 UNIQUE 인덱스(idx_users_email)가 걸려 있어 새 uid 의 행은
    # 들어가지 못한다 → "✗ DB 행을 만들지 못했습니다" 로 끝났다. 에뮬레이터를
    # 재시작할 때마다 시드가 실패하는 셈이었다.
    # 기존 uid 를 쓰면 그 계정의 보유·거래가 그대로 이어진다 (로그인 세션의 uid 는
    # DB 행의 uid 다 — routers/auth.py login 의 _custom_token_for(account["uid"])).
    existing = users_repo.find_by_email(LOCAL_TEST_EMAIL)
    want_uid = existing["uid"] if existing else None

    # 1) 인증 계정 — 있으면 비밀번호만 맞춘다
    try:
        rec = fb_auth.get_user_by_email(LOCAL_TEST_EMAIL)
        fb_auth.update_user(rec.uid, password=password)
        created = False
        if want_uid and rec.uid != want_uid:
            # 로그인은 DB uid 로 세션을 만들므로 동작은 한다. 다만 두 저장소가
            # 다른 uid 를 들고 있다는 사실은 남긴다 — 나중에 헷갈리는 원인이 된다.
            print(f"! 인증 계정 uid({rec.uid})가 DB uid({want_uid})와 다릅니다. "
                  f"로그인은 DB uid 로 됩니다.", file=sys.stderr)
    except fb_auth.UserNotFoundError:
        kwargs = dict(email=LOCAL_TEST_EMAIL, password=password,
                      display_name="테스트", email_verified=True)
        if want_uid:
            kwargs["uid"] = want_uid
        rec = fb_auth.create_user(**kwargs)
        created = True

    # 2) users 행 — 이게 있어야 '가입한 계정'으로 인정된다.
    #    이미 있으면 새로 만들지 않는다 (uid 가 다르면 UNIQUE(email) 에 걸린다).
    row_uid = want_uid or rec.uid
    if not existing:
        users_repo.upsert_user(
            uid=row_uid, email=LOCAL_TEST_EMAIL, name="테스트",
            provider="password", email_verified=True, photo_url=None,
            username="테스트",
        )
    if not users_repo.get_user(row_uid):
        print("✗ DB 행을 만들지 못했습니다.", file=sys.stderr)
        return 1

    print(f"✓ 로컬 테스트 계정 {'생성' if created else '갱신'} 완료")
    print(f"  이메일   : {LOCAL_TEST_EMAIL}")
    print(f"  비밀번호 : {password}")
    print(f"  UID      : {row_uid}" + ("  (DB 기존 행 재사용)" if existing else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

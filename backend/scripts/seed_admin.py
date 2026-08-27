"""
scripts/seed_admin.py
─────────────────────
관리자 계정을 만들거나 갱신한다.

    python -m backend.scripts.seed_admin                 # 비밀번호를 물어봄
    ADMIN_PASSWORD='...' python -m backend.scripts.seed_admin   # 비대화식(CI)

비밀번호를 인자로 받지 않는 이유는, 명령행 인자가 셸 히스토리와 프로세스 목록에
그대로 남기 때문이다. 입력을 받거나 환경변수로만 넘긴다.

이 스크립트는 관리자 권한을 부여하는 **유일한** 경로다. HTTP 로는 어떤 방법으로도
is_admin 을 켤 수 없다 — 열어두면 권한 상승 통로가 된다.

여러 번 실행해도 안전하다. 이미 있으면 비밀번호와 권한만 맞춘다.
"""
from __future__ import annotations

import getpass
import os
import sys

from backend.db import init_pool, is_available
from backend.db.schema import init_schema
from backend.db import users_repo
from backend.routers.auth import ADMIN_EMAIL, ADMIN_LOGIN_ID
from backend.services.auth import init_firebase
from backend.services.credentials import validate_password


def main() -> int:
    password = os.getenv("ADMIN_PASSWORD") or ""
    if not password:
        password = getpass.getpass(f"'{ADMIN_LOGIN_ID}' 계정에 쓸 비밀번호: ")
        if password != getpass.getpass("한 번 더 입력: "):
            print("✗ 두 입력이 다릅니다.", file=sys.stderr)
            return 1

    ok, reason = validate_password(password)
    if not ok:
        print(f"✗ {reason}", file=sys.stderr)
        return 1

    # 연결 풀은 웹 서버 기동 시점에 열린다. 스크립트로 직접 실행할 때는
    # 우리가 열어야 한다 — 안 그러면 DB 가 멀쩡해도 "연결 불가"로 보인다.
    if not is_available():
        init_pool()
    if not is_available():
        print("✗ DB에 연결할 수 없습니다. DATABASE_URL 을 확인하세요.", file=sys.stderr)
        return 1
    init_schema()

    if not init_firebase():
        print("✗ Firebase Admin SDK 를 초기화할 수 없습니다.", file=sys.stderr)
        return 1

    from firebase_admin import auth as fb_auth

    # 1) Firebase 계정 — 있으면 비밀번호만 맞춘다
    try:
        rec = fb_auth.get_user_by_email(ADMIN_EMAIL)
        fb_auth.update_user(rec.uid, password=password)
        created = False
    except fb_auth.UserNotFoundError:
        rec = fb_auth.create_user(email=ADMIN_EMAIL, password=password,
                                  display_name="관리자")
        created = True

    # 2) users 행 — 서비스 가입 처리
    users_repo.upsert_user(
        uid=rec.uid, email=ADMIN_EMAIL, name="관리자",
        provider="password", email_verified=True, photo_url=None,
    )
    if not users_repo.set_admin(rec.uid, True):
        print("✗ 관리자 권한 부여에 실패했습니다.", file=sys.stderr)
        return 1

    print(f"✓ 관리자 계정 {'생성' if created else '갱신'} 완료")
    print(f"  로그인 아이디 : {ADMIN_LOGIN_ID}")
    print(f"  내부 이메일   : {ADMIN_EMAIL}")
    print(f"  UID          : {rec.uid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

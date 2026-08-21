"""
services/auth.py
────────────────
Firebase ID 토큰 검증 + 사용자 식별.

**이 모듈이 존재하는 이유**
예전에는 라우터가 `X-User-Id` 헤더를 그대로 믿었다. 즉 누구든
`X-User-Id: 남의_아이디` 를 보내면 그 사람의 보유 종목·거래 이력·리포트를
전부 읽을 수 있었다. 인증이 아니라 자기신고였다.

이제는 클라이언트가 Firebase ID 토큰(JWT)을 보내고, 서버가 Google 공개키로
서명을 검증한 뒤 토큰 안의 `uid` 만 신뢰한다. 헤더로 주장하는 신원은 무시한다.

검증 항목 (firebase_admin 이 수행)
  · 서명 — Google 공개키, 위조 불가
  · exp  — 만료된 토큰 거부
  · aud/iss — 우리 프로젝트가 발급한 토큰인지
  · revoked — 로그아웃·비밀번호 변경 시 무효화된 토큰 거부
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import Depends, Header, HTTPException, status

logger = logging.getLogger(__name__)

_initialized = False
_init_error: Optional[str] = None

# 개발 편의 스위치 — 인증 없이 로컬에서 돌려볼 때만 사용한다.
# 켜면 토큰 없이 X-User-Id 를 그대로 신뢰하므로 **운영에서는 절대 켜면 안 된다**.
ALLOW_INSECURE_DEV_AUTH = os.getenv("ALLOW_INSECURE_DEV_AUTH", "false").lower() in ("1", "true", "yes")


def _cred_path() -> Optional[Path]:
    env = os.getenv("FIREBASE_CREDENTIALS", "").strip()
    if env and Path(env).exists():
        return Path(env)
    local = Path(__file__).parent.parent.parent / "secrets" / "firebase-admin.json"
    return local if local.exists() else None


def init_firebase() -> bool:
    """Firebase Admin SDK 초기화. 성공 시 True.

    자격증명 경로 우선순위:
      1. FIREBASE_CREDENTIALS 환경변수 (파일 경로)
      2. FIREBASE_CREDENTIALS_JSON 환경변수 (JSON 원문 — Cloud Run Secret 용)
      3. secrets/firebase-admin.json (로컬 개발)
      4. Application Default Credentials (GCP 런타임)
    """
    global _initialized, _init_error
    if _initialized:
        return True
    try:
        import firebase_admin
        from firebase_admin import credentials

        if firebase_admin._apps:                       # 이미 초기화됨
            _initialized = True
            return True

        raw = os.getenv("FIREBASE_CREDENTIALS_JSON", "").strip()
        if raw:
            import json
            cred = credentials.Certificate(json.loads(raw))
        else:
            path = _cred_path()
            cred = credentials.Certificate(str(path)) if path else credentials.ApplicationDefault()

        project = os.getenv("FIREBASE_PROJECT_ID", "personalfinancialplatform")
        firebase_admin.initialize_app(cred, {"projectId": project})
        _initialized = True
        logger.info(f"Firebase Admin 초기화 완료 (project={project})")
        return True
    except Exception as e:
        _init_error = str(e)
        logger.warning(f"Firebase Admin 초기화 실패 — 인증 비활성: {e}")
        return False


# 시계 오차 허용치(초).
# 서버 시계가 Google 보다 조금 느리면 방금 발급된 토큰의 iat 가 "미래"로 보여
# "Token used too early" 로 거부된다. 실제로 로컬에서 1~2초 뒤처져 재현됐다.
# 몇 초의 여유는 만료 판정을 그만큼만 늦출 뿐이라 안전하다 (SDK 상한은 60초).
_CLOCK_SKEW_SECONDS = 30


def verify_id_token(token: str) -> dict:
    """ID 토큰 검증 후 클레임 반환. 실패 시 예외."""
    from firebase_admin import auth as fb_auth
    # check_revoked=True: 로그아웃·비밀번호 변경 후 남은 토큰을 막는다.
    return fb_auth.verify_id_token(
        token, check_revoked=True, clock_skew_seconds=_CLOCK_SKEW_SECONDS,
    )


def _bearer(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


# 가입이 확인된 uid — 매 요청마다 DB 를 읽지 않기 위한 캐시.
# 탈퇴 시 비워야 하므로 forget_registration() 을 함께 둔다.
_registered: set[str] = set()


def forget_registration(uid: str) -> None:
    """가입 캐시에서 제거 (탈퇴 처리 후 호출)."""
    _registered.discard(uid)


def is_registered(uid: str) -> bool:
    """우리 서비스에 **가입**한 계정인지.

    Firebase 인증에 성공했다고 가입한 것은 아니다. 구글 팝업 로그인은 처음
    누르는 순간 Firebase 계정을 자동으로 만들어 주기 때문에, 이것만 믿으면
    가입 절차 없이 아무나 들어오게 된다. 그래서 users 행의 존재를 가입의
    기준으로 삼는다 — 이 행은 명시적인 가입 경로에서만 만들어진다.
    """
    if uid in _registered:
        return True
    try:
        from backend.db.users_repo import get_user
        if get_user(uid):
            _registered.add(uid)
            return True
    except Exception as e:                     # DB 미연결 — 인증을 통과시키지 않는다
        logger.warning(f"가입 확인 실패 {uid}: {e}")
    return False


async def verified_user(
    authorization: Optional[str] = Header(default=None),
    x_user_id: Optional[str] = Header(default=None),
) -> dict:
    """토큰만 검증한다 — **가입 여부는 보지 않는다**.

    가입 절차 자체(소셜 가입 완료, 가입 여부 조회)에 쓴다. 그 외의 모든
    엔드포인트는 current_user 를 써서 가입까지 확인해야 한다.
    """
    token = _bearer(authorization)

    if not token:
        # 개발 모드에서만 헤더 신뢰 (기본 꺼짐)
        if ALLOW_INSECURE_DEV_AUTH and x_user_id:
            return {"uid": x_user_id.strip(), "email": None, "name": None,
                    "provider": "dev", "email_verified": False, "_insecure": True}
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="로그인이 필요합니다.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not init_firebase():
        raise HTTPException(status_code=503, detail=f"인증 서버 초기화 실패: {_init_error}")

    try:
        claims = verify_id_token(token)
    except Exception as e:
        name = type(e).__name__
        if "Expired" in name:
            detail = "세션이 만료되었습니다. 다시 로그인해 주세요."
        elif "Revoked" in name:
            detail = "로그아웃된 세션입니다. 다시 로그인해 주세요."
        else:
            detail = "유효하지 않은 인증 정보입니다."
        raise HTTPException(status_code=401, detail=detail,
                            headers={"WWW-Authenticate": "Bearer"})

    uid = claims.get("uid") or claims.get("user_id")
    if not uid:
        raise HTTPException(status_code=401, detail="토큰에 사용자 식별자가 없습니다.")

    return {
        "uid":            uid,
        "email":          claims.get("email"),
        "name":           claims.get("name") or (claims.get("email") or "").split("@")[0],
        "provider":       (claims.get("firebase") or {}).get("sign_in_provider"),
        "email_verified": bool(claims.get("email_verified")),
        "picture":        claims.get("picture"),
    }


async def current_user(user: dict = Depends(verified_user)) -> dict:
    """인증 + 가입 확인. 가입하지 않은 계정은 403.

    401 이 아니라 403 을 쓴다 — 토큰 자체는 유효하므로 재로그인해도 소용없고,
    프론트가 "가입이 필요합니다" 안내로 분기할 수 있어야 한다.
    """
    if not is_registered(user["uid"]):
        raise HTTPException(
            status_code=403,
            detail="가입되지 않은 계정입니다. 회원가입을 먼저 진행해 주세요.",
        )
    return user


async def optional_user(
    authorization: Optional[str] = Header(default=None),
    x_user_id: Optional[str] = Header(default=None),
) -> Optional[dict]:
    """인증 선택 의존성 — 비로그인 접근이 허용된 엔드포인트용.

    토큰이 있고 가입까지 마친 계정이면 정보를 주고, 아니면 None
    (401·403 을 던지지 않는다).
    """
    try:
        user = await verified_user(authorization, x_user_id)
    except HTTPException:
        return None
    return user if is_registered(user["uid"]) else None


async def require_uid(user: dict = Depends(current_user)) -> str:
    """검증된 UID 만 필요한 경우의 축약 의존성."""
    return user["uid"]

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
import time
import os
from pathlib import Path
from typing import Optional

from fastapi import Depends, Header, HTTPException, status

logger = logging.getLogger(__name__)

_initialized = False
_init_error: Optional[str] = None

# 개발 편의 스위치 — 인증 없이 로컬에서 돌려볼 때만 사용한다.
# 켜면 토큰 없이 X-User-Id 를 그대로 신뢰한다. 즉 헤더 한 줄로 아무 계정이나
# 사칭할 수 있다는 뜻이라, 운영에서 켜지면 그 순간 전 계정이 열린다.
#
# "켜지 말자"는 약속에 기대지 않고, 운영 환경에서는 값과 무관하게 무시한다.
# Cloud Run 은 K_SERVICE 를, App Engine 은 GAE_ENV 를 자동으로 넣어주므로
# 이 변수들의 존재만으로 "여기는 운영"이라고 판단할 수 있다.
_IS_MANAGED_RUNTIME = bool(os.getenv("K_SERVICE") or os.getenv("GAE_ENV"))

ALLOW_INSECURE_DEV_AUTH = (
    not _IS_MANAGED_RUNTIME
    and os.getenv("ALLOW_INSECURE_DEV_AUTH", "false").lower() in ("1", "true", "yes")
)

if _IS_MANAGED_RUNTIME and os.getenv("ALLOW_INSECURE_DEV_AUTH", "").lower() in ("1", "true", "yes"):
    logger.error(
        "ALLOW_INSECURE_DEV_AUTH 가 운영 환경에 설정돼 있어 무시했습니다. "
        "이 변수는 로컬 전용입니다 — 배포 설정에서 제거하세요."
    )


# 에뮬레이터를 쓰면 인증도 로컬에만 있으므로, 운영 계정을 건드릴 위험이 없다.
# 그때는 로컬에서도 정리 동작을 그대로 돌린다 — 운영과 같은 흐름을 시험할 수 있다.
_USING_AUTH_EMULATOR = bool(os.getenv("FIREBASE_AUTH_EMULATOR_HOST"))

# 공유 Firebase 를 쓰면서도 로컬에서 정리를 허용하고 싶을 때의 탈출구.
# 실제 사용자 계정을 지울 수 있으므로 기본은 꺼 둔다.
_LOCAL_MAY_DELETE_AUTH = _USING_AUTH_EMULATOR or (
    os.getenv("LOCAL_ALLOW_AUTH_DELETE", "false").lower() in ("1", "true", "yes")
)


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


# 가입이 확인된 uid → 확인한 시각. 매 요청마다 DB 를 읽지 않기 위한 캐시다.
# 탈퇴 시 비워야 하므로 forget_registration() 을 함께 둔다.
# 만료가 없으면 DB 와 어긋난 채 굳어버린다 (is_registered 주석 참고).
_registered: dict[str, float] = {}
_REGISTRATION_TTL_SECONDS = 60.0


def forget_registration(uid: str) -> None:
    """가입 캐시에서 제거 (탈퇴 처리 후 호출)."""
    _registered.pop(uid, None)


def is_registered(uid: str) -> bool:
    """우리 서비스에 **가입**한 계정인지.

    Firebase 인증에 성공했다고 가입한 것은 아니다. 구글 팝업 로그인은 처음
    누르는 순간 Firebase 계정을 자동으로 만들어 주기 때문에, 이것만 믿으면
    가입 절차 없이 아무나 들어오게 된다. 그래서 users 행의 존재를 가입의
    기준으로 삼는다 — 이 행은 명시적인 가입 경로에서만 만들어진다.

    캐시는 **반드시 만료돼야 한다.** 예전에는 한 번 True 가 되면 영원히
    True 였다. 그래서 users 행이 사라져도(다른 인스턴스에서 탈퇴, 로컬 DB
    초기화) 그 인스턴스는 계속 가입된 것으로 봤고, 서버가 재시작되면 같은
    계정이 갑자기 미가입으로 바뀌었다. 그 결과 "가입하면 이미 가입된 계정,
    로그인하면 가입되지 않은 계정" 이 번갈아 나왔다.

    DB 를 매번 읽지 않는 이유는 이 함수가 거의 모든 요청에서 불리기 때문이다.
    짧은 만료로 왕복을 줄이면서도 어긋남이 오래 가지 않게 한다.
    """
    now = time.time()
    seen_at = _registered.get(uid)
    if seen_at is not None and now - seen_at < _REGISTRATION_TTL_SECONDS:
        return True
    try:
        from backend.db.users_repo import get_user
        if get_user(uid):
            _registered[uid] = now
            return True
        # DB 에 없다 — 캐시에 남아 있던 옛 판단을 지운다.
        _registered.pop(uid, None)
    except Exception as e:                     # DB 미연결 — 인증을 통과시키지 않는다
        logger.warning(f"가입 확인 실패 {uid}: {e}")
        # DB 를 못 읽었을 뿐이라면 아직 유효한 캐시는 그대로 믿는다.
        return seen_at is not None
    return False


def verified_user(
    authorization: Optional[str] = Header(default=None),
    x_user_id: Optional[str] = Header(default=None),
) -> dict:
    """토큰만 검증한다 — **가입 여부는 보지 않는다**.

    **이 함수는 반드시 동기(def)여야 한다.**
    verify_id_token 은 check_revoked=True 라 Firebase 에 네트워크 요청을 보낸다.
    async def 안에서 블로킹 호출을 하면 이벤트 루프가 그동안 멈추고, 같은
    인스턴스의 다른 사용자 요청까지 전부 대기한다. 실제로 그래서 동시 100명일 때
    처리량이 인스턴스당 2 req/s 로 주저앉았다. 동기로 두면 FastAPI 가
    스레드풀에서 실행해 여러 건이 동시에 처리된다.

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


def current_user(user: dict = Depends(verified_user)) -> dict:
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


def optional_user(
    authorization: Optional[str] = Header(default=None),
    x_user_id: Optional[str] = Header(default=None),
) -> Optional[dict]:
    """인증 선택 의존성 — 비로그인 접근이 허용된 엔드포인트용.

    토큰이 있고 가입까지 마친 계정이면 정보를 주고, 아니면 None
    (401·403 을 던지지 않는다).
    """
    try:
        user = verified_user(authorization, x_user_id)
    except HTTPException:
        return None
    return user if is_registered(user["uid"]) else None


def require_uid(user: dict = Depends(current_user)) -> str:
    """검증된 UID 만 필요한 경우의 축약 의존성."""
    return user["uid"]


# ── 관리자 권한 · 기능 스위치 ────────────────────────────────────────────────────

def admin_user(user: dict = Depends(current_user)) -> dict:
    """관리자 전용 의존성. 관리자가 아니면 404 로 막는다.

    403 이 아니라 404 인 이유는, 403 은 "그 기능이 존재한다"를 알려주기 때문이다.
    관리 기능의 존재 자체를 숨기는 편이 낫다.
    """
    from backend.db.users_repo import is_admin as _is_admin

    if not _is_admin(user["uid"]):
        raise HTTPException(status_code=404, detail="찾을 수 없습니다.")
    return user


def _flag(key: str) -> bool:
    from backend.db.settings_repo import get_flag

    return bool(get_flag(key))


def ai_feature_user(user: dict = Depends(current_user)) -> dict:
    """AI 기능(리포트·시나리오 생성) 접근 의존성.

    관리자가 ai_enabled 를 내리면 일반 사용자는 막히고 관리자만 계속 쓸 수 있다.
    관리자까지 막으면 스위치를 내린 뒤 상태를 확인할 방법이 없어진다.
    """
    from backend.db.users_repo import is_admin as _is_admin

    if _is_admin(user["uid"]):
        return user
    if not _flag("ai_enabled"):
        raise HTTPException(
            status_code=503,
            detail="AI 분석 기능이 일시적으로 중지되었습니다. 잠시 후 다시 시도해 주세요.",
        )
    return user


def enforce_deep_limit(user: dict, kind: str) -> None:
    """심층 분석 횟수 제한 검사. 초과하면 429 를 올린다.

    관리자와 제한이 꺼진 상태는 그냥 통과한다. 기록은 실제로 생성이 시작된 뒤에
    남긴다 — 검사 시점에 미리 남기면, 요청이 검증에서 막혀도 횟수가 깎인다.
    """
    from backend.db.users_repo import is_admin as _is_admin
    from backend.db import usage_repo

    if _is_admin(user["uid"]) or not _flag("deep_analysis_daily_limit"):
        return
    if usage_repo.count_recent(user["uid"]) < 1:
        return

    when = usage_repo.next_available_at(user["uid"])
    detail = "심층 분석은 24시간에 한 번만 사용할 수 있습니다."
    if when:
        from datetime import datetime
        try:
            t = datetime.fromisoformat(when)
            detail += f" {t.strftime('%m월 %d일 %H:%M')} 이후 다시 사용할 수 있습니다."
        except Exception:
            pass
    raise HTTPException(status_code=429, detail=detail)


def resolve_model_tier(requested: str, user: dict) -> str:
    """요청한 분석 등급을 실제 허용 등급으로 바꾼다.

    deep_analysis_enabled 가 꺼져 있으면 일반 사용자의 'deep' 요청을 'basic' 으로
    낮춘다. 400 으로 거절하지 않는 이유는, 프론트가 심층 옵션을 이미 숨기고 있어
    여기까지 온 요청은 오래된 화면이나 캐시된 상태일 가능성이 크기 때문이다.
    기능을 실패시키는 것보다 기본 분석으로 처리하는 편이 낫다.
    """
    from backend.db.users_repo import is_admin as _is_admin

    tier = requested if requested in ("basic", "deep") else "basic"
    if tier == "deep" and not _is_admin(user["uid"]) and not _flag("deep_analysis_enabled"):
        return "basic"
    return tier

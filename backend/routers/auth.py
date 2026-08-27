"""
routers/auth.py
───────────────
인증 API.

Google·이메일은 Firebase Auth 가 프론트에서 직접 처리하므로 서버가 할 일은
① 토큰 검증(services/auth.py) ② 사용자 레코드 동기화 뿐이다.

Naver·Kakao 는 Firebase 가 기본 지원하지 않는다. 그래서 Custom Token 흐름을 쓴다:
  ① 프론트가 Naver/Kakao 로 로그인해 액세스 토큰을 받는다
  ② 그 토큰을 이 서버로 보낸다
  ③ 서버가 Naver/Kakao API 로 토큰을 **검증**하고 프로필을 받는다
  ④ 서버가 Firebase Custom Token 을 발급한다
  ⑤ 프론트가 그 토큰으로 Firebase 에 로그인한다 → 이후는 Google 과 동일

③이 핵심이다. 클라이언트가 보낸 프로필을 그대로 믿으면 남의 계정을 사칭할 수
있으므로, 반드시 공급자 API 에 직접 물어 확인한다.
"""
from __future__ import annotations

import logging
import os
import re
import time
from typing import Optional

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.db import users_repo
from backend.services.auth import (
    current_user, verified_user, init_firebase, is_registered, forget_registration,
)
from backend.services.credentials import (
    normalize_username, validate_username, validate_password, validate_email,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])

# Firebase 웹 API 키 — 비밀이 아니다(프론트 번들에 그대로 들어간다).
# 아이디 로그인에서 비밀번호를 대조할 때 Identity Toolkit 호출에 쓴다.
FIREBASE_WEB_API_KEY = os.getenv(
    "FIREBASE_WEB_API_KEY", "AIzaSyCSjie4HV_Z8zEnlowZDW33qTRdpstTIVE")
IDENTITY_TOOLKIT = "https://identitytoolkit.googleapis.com/v1"

NAVER_CLIENT_ID     = os.getenv("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET", "")
KAKAO_REST_API_KEY  = os.getenv("KAKAO_REST_API_KEY", "")
# 카카오 JS SDK 초기화용 키. 미설정이면 REST 키를 쓴다 —
# 카카오는 두 키가 다르지만, 앱에 따라 REST 키로도 JS 초기화가 동작한다.
KAKAO_JS_KEY        = os.getenv("KAKAO_JS_KEY", "")
# 요청할 카카오 동의항목. 콘솔에서 "사용함"으로 켠 것만 넣을 수 있고,
# 권한이 없는 항목을 요청하면 인가 단계에서 거부된다.
#
# account_email 은 기본으로 넣지 않는다 — 개인 개발자 앱에서는 "권한 없음"이라
# 요청 자체가 막힌다(비즈 앱 전환이 필요하다). 이메일이 없어도 카카오 고유 ID로
# 계정을 구분할 수 있으므로 로그인에는 지장이 없다.
# 비즈 앱 전환 후에는 KAKAO_SCOPE=account_email 처럼 넣어 주면 된다.
KAKAO_SCOPE         = os.getenv("KAKAO_SCOPE", "").strip()
# 카카오 개발자 콘솔 → 보안 → Client Secret 을 "사용함"으로 켠 경우에만 필요하다.
KAKAO_CLIENT_SECRET = os.getenv("KAKAO_CLIENT_SECRET", "")


# ── 현재 사용자 ────────────────────────────────────────────────────────────────

@router.get("/status")
def signup_status(user: dict = Depends(verified_user)):
    """이 토큰의 계정이 **가입**돼 있는지.

    구글 팝업은 처음 누르는 순간 Firebase 계정을 만들어 버리므로, 로그인 성공
    여부만으로는 가입 여부를 알 수 없다. 프론트는 소셜 로그인 직후 이걸 물어
    가입 화면으로 보낼지 그대로 들여보낼지 정한다.
    """
    row = users_repo.get_user(user["uid"])
    return {
        "registered": bool(row),
        "username":   (row or {}).get("username"),
        # 아이디 자동 제안 — 가입 화면의 기본값으로 쓴다.
        "suggested_username": None if row else _auto_username(
            user.get("name") or (user.get("email") or "").split("@")[0], user["uid"]),
        "email":      user.get("email"),
        "provider":   user.get("provider"),
    }


@router.get("/me")
def get_me(user: dict = Depends(current_user)):
    """로그인한 사용자 정보. 가입하지 않은 계정은 current_user 가 403 을 낸다.

    예전에는 여기서 users 행을 자동으로 만들었다. 그 탓에 가입한 적 없는
    구글 계정도 처음 로그인하는 순간 계정이 생겨 그냥 들어와졌다.
    이제 행 생성은 가입 경로에서만 한다.
    """
    users_repo.touch_login(user["uid"])
    row = users_repo.get_user(user["uid"]) or {}
    if row.get("disabled"):
        raise HTTPException(status_code=403, detail="비활성화된 계정입니다.")
    return {
        "uid":            user["uid"],
        "username":       row.get("username"),
        "email":          row.get("email") or user.get("email"),
        "name":           row.get("name") or user.get("name"),
        "provider":       row.get("provider") or user.get("provider"),
        "email_verified": user.get("email_verified", False),
        "photo_url":      row.get("photo_url"),
        "age":            row.get("age"),
        "created_at":     row.get("created_at"),
    }


# 표시 이름 상한. 프론트(AccountSettings)의 maxLength 와 같은 값이어야 한다 —
# 한쪽만 바꾸면 저장이 조용히 잘리거나 영문 원시 오류가 나간다.
DISPLAY_NAME_MAX = 20


class ProfileUpdate(BaseModel):
    # 길이 검사는 아래에서 한국어 문구로 처리한다. 여기서 막으면 Pydantic 이
    # 영어 원시 오류를 그대로 내보낸다.
    name: Optional[str] = Field(default=None, max_length=200)
    # 선택 입력. 보내지 않으면 그대로 두고, clear_age 로 지운다.
    # 범위 검사는 아래에서 한국어로 처리한다 — Field 로 막으면 영문 원시 오류가 나간다.
    age: Optional[int] = None
    clear_age: bool = False


@router.patch("/me")
def patch_me(body: ProfileUpdate, user: dict = Depends(current_user)):
    """표시 이름·나이 변경. 이메일은 로그인 식별자라 여기서 바꾸지 않는다."""
    name = body.name.strip() if body.name is not None else None
    if name is not None:
        if not name:
            raise HTTPException(status_code=400, detail="표시 이름을 입력해 주세요.")
        if len(name) > DISPLAY_NAME_MAX:
            raise HTTPException(status_code=400,
                                detail=f"표시 이름은 {DISPLAY_NAME_MAX}자 이하로 입력해 주세요.")
    if body.age is not None and not (1 <= body.age <= 120):
        raise HTTPException(status_code=400, detail="나이는 1~120 사이로 입력해 주세요.")
    if name is None and body.age is None and not body.clear_age:
        raise HTTPException(status_code=400, detail="변경할 내용이 없습니다.")
    if not users_repo.update_profile(user["uid"], name, body.age, body.clear_age):
        raise HTTPException(status_code=400, detail="저장하지 못했습니다.")
    return users_repo.get_user(user["uid"])


@router.delete("/me")
def delete_me(user: dict = Depends(current_user)):
    """회원 탈퇴 — DB 개인 데이터 + Firebase 계정 모두 삭제."""
    result = users_repo.delete_user(user["uid"])
    forget_registration(user["uid"])
    try:
        if init_firebase():
            from firebase_admin import auth as fb_auth
            fb_auth.delete_user(user["uid"])
            result["firebase_deleted"] = True
    except Exception as e:
        logger.warning(f"Firebase 계정 삭제 실패 {user['uid']}: {e}")
        result["firebase_deleted"] = False
    return result


# ── Naver / Kakao — Custom Token 발급 ─────────────────────────────────────────

class OAuthTokenIn(BaseModel):
    access_token: str = Field(min_length=10)
    # 가입 화면에서 눌렀는지 여부. 로그인 화면에서는 가입한 적 없는 계정을 막는다.
    # (BaseModel.register 와 이름이 겹치지 않도록 signup 으로 둔다)
    signup: bool = False


class SocialRegisterIn(BaseModel):
    """소셜 로그인으로 Firebase 인증까지 끝난 사용자가 아이디를 정해 가입을 마친다."""
    username: str = Field(min_length=1, max_length=100)


def _provider_label(p: Optional[str]) -> str:
    """가입 공급자를 사용자에게 보여줄 이름으로."""
    return {
        "google.com": "Google", "password": "이메일·비밀번호",
        "naver": "네이버", "kakao": "카카오",
    }.get(p or "", "다른 방법")


def _auto_username(seed: str, uid: str) -> Optional[str]:
    """SNS 가입자에게 아이디를 자동으로 부여한다.

    구글·카카오로 가입하면 아이디를 입력할 기회가 없다. 그런데 아이디가 없으면
    화면에 표시할 이름도 없고, 나중에 아이디 로그인으로 전환할 수도 없다.
    닉네임·이메일 앞부분을 규칙에 맞게 다듬어 쓰고, 겹치면 뒤에 숫자를 붙인다.
    """
    import re
    base = re.sub(r"[^a-z0-9_-]", "", (seed or "").lower())
    base = re.sub(r"^[^a-z]+", "", base)[:14]
    if len(base) < 4:
        base = f"user{uid[:6].lower()}"
        base = re.sub(r"[^a-z0-9]", "", base)[:14]
    for suffix in ("", *(str(i) for i in range(1, 50))):
        cand = f"{base}{suffix}"[:20]
        if len(cand) >= 4 and not users_repo.username_taken(cand):
            return cand
    return None


def _issue_custom_token(uid: str, email: Optional[str], name: Optional[str],
                        provider: str, photo: Optional[str],
                        register: bool = False) -> dict:
    """검증된 프로필로 Firebase Custom Token 발급.

    register=False (로그인) 이면 가입한 적 없는 계정은 거부한다. 가입 절차를
    거치지 않고 소셜 버튼만으로 계정이 생기면 "가입"이라는 단계가 무의미해진다.

    register=True (가입) 여도 여기서 users 행을 만들지는 **않는다**. 아이디를
    정해야 가입이 끝나며, 그 처리는 /auth/social/register 가 맡는다. 공급자마다
    가입 흐름이 달라지지 않도록 한 곳으로 모았다.
    """
    if not init_firebase():
        raise HTTPException(status_code=503, detail="인증 서버를 사용할 수 없습니다.")
    from firebase_admin import auth as fb_auth

    if not register and not is_registered(uid):
        raise HTTPException(
            status_code=404,
            detail="가입되지 않은 계정입니다. 회원가입을 먼저 진행해 주세요.",
        )
    if register and is_registered(uid):
        raise HTTPException(status_code=409, detail="이미 가입된 계정입니다. 로그인해 주세요.")

    try:
        fb_auth.get_user(uid)
    except Exception:
        try:
            fb_auth.create_user(
                uid=uid, email=email or None, display_name=name or None,
                photo_url=photo or None,
            )
        except Exception as e:
            # 같은 이메일이 다른 공급자로 이미 있으면 계정 연결이 필요하다.
            if "EMAIL_EXISTS" in str(e).upper():
                raise HTTPException(
                    status_code=409,
                    detail="이미 다른 방법으로 가입된 이메일입니다. 기존 방식으로 로그인해 주세요.",
                )
            logger.warning(f"create_user 실패 {uid}: {e}")
            fb_auth.create_user(uid=uid)      # 이메일 없이라도 생성

    if not register:
        users_repo.touch_login(uid)
    token = fb_auth.create_custom_token(uid, {"provider": provider})
    return {"custom_token": token.decode() if isinstance(token, bytes) else token,
            "uid": uid, "provider": provider}


_PROVIDER_CACHE: dict = {"at": 0.0, "value": None}
_PROVIDER_TTL = 300      # 초


def _firebase_signin_config() -> dict:
    """Firebase 에 실제로 켜져 있는 로그인 수단을 조회한다.

    하드코딩하면 콘솔에서 껐다 켰을 때 프론트 버튼과 어긋난다. Admin 자격증명으로
    Identity Toolkit 설정을 읽어 실제 상태를 쓰되, 매 요청 조회는 낭비이므로
    5분 캐시한다. 조회에 실패하면 빈 dict 를 돌려 호출부가 기본값을 쓰게 한다.
    """
    now = time.time()
    if _PROVIDER_CACHE["value"] is not None and now - _PROVIDER_CACHE["at"] < _PROVIDER_TTL:
        return _PROVIDER_CACHE["value"]

    result: dict = {}
    try:
        if init_firebase():
            import google.auth.transport.requests as greq
            import firebase_admin
            app = firebase_admin.get_app()
            creds = app.credential.get_credential()
            creds.refresh(greq.Request())
            project = os.getenv("FIREBASE_PROJECT_ID", "personalfinancialplatform")
            headers = {"Authorization": f"Bearer {creds.token}"}
            cfg = requests.get(
                f"https://identitytoolkit.googleapis.com/v2/projects/{project}/config",
                headers=headers, timeout=8,
            ).json()
            result["password"] = bool((cfg.get("signIn") or {}).get("email", {}).get("enabled"))

            idps = requests.get(
                f"https://identitytoolkit.googleapis.com/v2/projects/{project}"
                "/defaultSupportedIdpConfigs",
                headers=headers, timeout=8,
            ).json()
            enabled = {
                c.get("name", "").rsplit("/", 1)[-1]
                for c in (idps.get("defaultSupportedIdpConfigs") or [])
                if c.get("enabled")
            }
            result["google"] = "google.com" in enabled
            result["apple"] = "apple.com" in enabled
    except Exception as e:
        logger.debug(f"로그인 수단 조회 실패 — 기본값 사용: {e}")

    _PROVIDER_CACHE.update(at=now, value=result)
    return result


# 카카오가 토큰 교환 단계에서 돌려주는 '설정 문제' 오류들.
# 실제 로그인이 실패했을 때 원인을 사용자에게 그대로 보여주기 위해 쓴다.
KAKAO_SETUP_ERRORS = {
    "KOE101": "카카오 REST API 키가 유효하지 않습니다. 개발자 콘솔 → 앱 키 → "
              "'REST API 키'(소문자+숫자 32자)를 넣어 주세요.",
    "KOE010": "Client Secret 이 일치하지 않습니다. 개발자 콘솔 → 보안에서 Client Secret 을 "
              "'사용함'으로 켰다면 .env 의 KAKAO_CLIENT_SECRET 에 그 값을 넣어야 합니다.",
    "KOE004": "카카오 로그인이 활성화되지 않았습니다. 개발자 콘솔 → 제품 설정 → "
              "카카오 로그인 → 활성화 설정을 켜 주세요.",
    "KOE006": "Redirect URI 가 등록되지 않았습니다. 개발자 콘솔 → 제품 설정 → "
              "카카오 로그인 → Redirect URI 에 "
              "http://localhost:3000/auth/kakao/callback 을 추가해 주세요.",
    "KOE003": "Redirect URI 가 일치하지 않습니다. 콘솔에 등록한 주소와 정확히 같아야 합니다.",
    "KOE205": "요청한 동의항목이 설정되지 않았습니다. 개발자 콘솔 → 카카오 로그인 → "
              "동의항목에서 해당 항목을 '사용함'으로 바꾸거나, KAKAO_SCOPE 를 비워 주세요.",
    "KOE207": "요청한 동의항목에 접근 권한이 없습니다. 이메일(account_email)은 "
              "비즈 앱 전환이 필요합니다. KAKAO_SCOPE 를 비워 두면 이메일 없이 로그인됩니다.",
}


def _kakao_config_error() -> str:
    """카카오 설정이 명백히 잘못됐는지 **로컬에서만** 판정한다. 문제없으면 빈 문자열.

    예전에는 카카오 토큰 엔드포인트에 일부러 실패하는 요청을 보내 확인했다.
    그 방식은 상대 서버의 오류 로그를 쌓아 KOE010 경고 메일까지 발송되게 만들었다.
    남의 시스템에 의도적으로 실패 트래픽을 보내는 건 헬스체크로 쓸 방법이 아니다.

    그래서 네트워크 호출 없이 형식만 본다. 실수의 대부분은 키 자리를 바꿔 넣는
    것이고(REST 키 ↔ Client Secret ↔ JavaScript 키), 이건 형식으로 잡힌다.
    나머지 설정 문제(로그인 미활성화, Redirect URI 등)는 실제 로그인이 실패하는
    시점에 KAKAO_SETUP_ERRORS 로 정확한 원인을 알려준다.
    """
    if not KAKAO_REST_API_KEY:
        return "KAKAO_REST_API_KEY 가 설정되지 않았습니다."

    # 카카오 앱 키(네이티브/REST/JavaScript/Admin)는 모두 소문자 16진수 32자다.
    # Client Secret 은 대소문자가 섞인 32자라 여기서 걸러진다.
    if not re.fullmatch(r"[0-9a-f]{32}", KAKAO_REST_API_KEY):
        return ("KAKAO_REST_API_KEY 형식이 올바르지 않습니다. 개발자 콘솔 → 앱 키 → "
                "'REST API 키'(소문자+숫자 32자)를 넣어 주세요. "
                "Client Secret(대소문자 혼합)이나 JavaScript 키가 아닙니다.")

    if KAKAO_CLIENT_SECRET and KAKAO_CLIENT_SECRET == KAKAO_REST_API_KEY:
        return "KAKAO_CLIENT_SECRET 에 REST API 키와 같은 값이 들어 있습니다."

    return ""


@router.get("/providers")
def list_providers():
    """활성화된 로그인 수단 — 프론트가 버튼 노출 여부를 판단한다."""
    live = _firebase_signin_config()
    kakao_reason = _kakao_config_error()
    return {
        "google":   live.get("google", False),
        "password": live.get("password", True),
        "apple":    live.get("apple", False),
        # Naver·Kakao 는 Firebase 가 아니라 우리 서버가 처리하므로 키 유무로 판단한다.
        "naver": bool(NAVER_CLIENT_ID and NAVER_CLIENT_SECRET),
        "kakao": not kakao_reason,
        "kakao_error": kakao_reason,
        # 프론트가 SDK 를 초기화하는 데 필요한 공개 키. 비밀키는 내려보내지 않는다.
        # 비어 있으면 프론트는 REST OAuth(팝업) 경로를 쓴다.
        "kakao_js_key":     KAKAO_JS_KEY if KAKAO_REST_API_KEY else "",
        "naver_client_id":  NAVER_CLIENT_ID if (NAVER_CLIENT_ID and NAVER_CLIENT_SECRET) else "",
    }


@router.post("/naver")
def naver_login(body: OAuthTokenIn):
    """Naver 액세스 토큰 → 검증 → Firebase Custom Token."""
    if not (NAVER_CLIENT_ID and NAVER_CLIENT_SECRET):
        raise HTTPException(status_code=503, detail="네이버 로그인이 설정되지 않았습니다.")
    try:
        r = requests.get(
            "https://openapi.naver.com/v1/nid/me",
            headers={"Authorization": f"Bearer {body.access_token}"},
            timeout=10,
        )
        data = r.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"네이버 인증 서버 오류: {e}")

    if r.status_code != 200 or data.get("resultcode") != "00":
        raise HTTPException(status_code=401, detail="네이버 토큰이 유효하지 않습니다.")

    p = data.get("response") or {}
    if not p.get("id"):
        raise HTTPException(status_code=401, detail="네이버 프로필을 가져올 수 없습니다.")
    return _issue_custom_token(
        uid=f"naver:{p['id']}", email=p.get("email"),
        name=p.get("nickname") or p.get("name"), provider="naver",
        photo=p.get("profile_image"), register=body.signup,
    )


def _kakao_profile_to_token(access_token: str, register: bool = False) -> dict:
    """카카오 액세스 토큰을 검증하고 Firebase Custom Token 을 발급한다.

    클라이언트가 보낸 프로필을 그대로 믿으면 남의 계정을 사칭할 수 있으므로,
    반드시 카카오 API 에 직접 물어 확인한다.
    """
    try:
        r = requests.get(
            "https://kapi.kakao.com/v2/user/me",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        data = r.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"카카오 인증 서버 오류: {e}")

    if r.status_code != 200 or not data.get("id"):
        raise HTTPException(status_code=401, detail="카카오 토큰이 유효하지 않습니다.")

    acc = data.get("kakao_account") or {}
    prof = acc.get("profile") or {}
    # 이메일은 없을 수 있다 — 개인 개발자 앱은 account_email 권한 자체가 없고,
    # 권한이 있어도 사용자가 동의하지 않을 수 있다. 카카오 고유 ID 만으로도
    # 계정을 구분할 수 있으므로 이메일을 필수로 두지 않는다.
    email = acc.get("email")
    if acc.get("is_email_valid") is False or acc.get("is_email_verified") is False:
        email = None
    return _issue_custom_token(
        uid=f"kakao:{data['id']}",
        email=email,
        name=prof.get("nickname"), provider="kakao",
        photo=prof.get("profile_image_url"), register=register,
    )


@router.post("/kakao")
def kakao_login(body: OAuthTokenIn):
    """Kakao 액세스 토큰 → 검증 → Firebase Custom Token (JS SDK 경로)."""
    if not KAKAO_REST_API_KEY:
        raise HTTPException(status_code=503, detail="카카오 로그인이 설정되지 않았습니다.")
    return _kakao_profile_to_token(body.access_token, register=body.signup)


# ── 카카오 REST OAuth (JavaScript 키 없이 REST 키만으로 동작) ────────────────
#
# JS SDK 의 Kakao.init() 은 "JavaScript 키"를 요구한다. REST API 키만 있는 경우를
# 위해 표준 OAuth 인가 코드 흐름을 쓴다:
#   ① 프론트가 팝업으로 카카오 인가 페이지를 연다
#   ② 사용자가 동의하면 redirect_uri 로 code 가 돌아온다
#   ③ 프론트가 그 code 를 서버로 보낸다
#   ④ 서버가 code 를 액세스 토큰으로 교환한다 (REST 키 사용)
#   ⑤ 서버가 프로필을 확인하고 Custom Token 을 발급한다
#
# ④를 서버에서 하는 편이 안전하다 — 클라이언트 시크릿이 브라우저에 노출되지 않는다.

class KakaoCodeIn(BaseModel):
    code:         str = Field(min_length=10)
    redirect_uri: str = Field(min_length=5)
    signup:       bool = False


@router.get("/kakao/authorize-url")
def kakao_authorize_url(redirect_uri: str, state: str = ""):
    """카카오 인가 페이지 URL. 프론트가 이 주소를 팝업으로 연다."""
    if not KAKAO_REST_API_KEY:
        raise HTTPException(status_code=503, detail="카카오 로그인이 설정되지 않았습니다.")
    from urllib.parse import urlencode
    params = {
        "client_id": KAKAO_REST_API_KEY,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": state,
    }
    # 빈 scope 를 보내면 카카오가 거부하므로 값이 있을 때만 넣는다.
    # 생략하면 콘솔에 설정된 동의항목이 그대로 적용된다.
    if KAKAO_SCOPE:
        params["scope"] = KAKAO_SCOPE
    q = urlencode(params)
    return {"url": f"https://kauth.kakao.com/oauth/authorize?{q}"}


@router.post("/kakao/callback")
def kakao_callback(body: KakaoCodeIn):
    """인가 코드 → 액세스 토큰 교환 → 프로필 검증 → Custom Token."""
    if not KAKAO_REST_API_KEY:
        raise HTTPException(status_code=503, detail="카카오 로그인이 설정되지 않았습니다.")

    payload = {
        "grant_type": "authorization_code",
        "client_id": KAKAO_REST_API_KEY,
        "redirect_uri": body.redirect_uri,
        "code": body.code,
    }
    if KAKAO_CLIENT_SECRET:
        payload["client_secret"] = KAKAO_CLIENT_SECRET

    try:
        r = requests.post("https://kauth.kakao.com/oauth/token", data=payload, timeout=10)
        data = r.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"카카오 인증 서버 오류: {e}")

    if r.status_code != 200 or not data.get("access_token"):
        logger.warning(f"카카오 토큰 교환 실패: {data}")
        if data.get("error_code") in KAKAO_SETUP_ERRORS:
            # 설정 실수다. 사용자 탓처럼 보이는 문구 대신 원인을 알려준다.
            raise HTTPException(status_code=503,
                                detail=KAKAO_SETUP_ERRORS[data["error_code"]])
        raise HTTPException(
            status_code=401,
            detail=data.get("error_description") or "카카오 인증에 실패했습니다.",
        )

    return _kakao_profile_to_token(data["access_token"], register=body.signup)


# ══════════════════════════════════════════════════════════════════════════════
# 아이디 · 비밀번호 가입 / 로그인
# ══════════════════════════════════════════════════════════════════════════════
#
# Firebase Auth 는 이메일로만 로그인한다. 사용자에게는 아이디를 받고 싶으므로
# 서버가 중간에서 아이디 → 이메일을 풀어 준다.
#
#   ① 프론트가 {아이디, 비밀번호} 를 서버로 보낸다
#   ② 서버가 아이디로 계정을 찾아 이메일을 얻는다 (이메일은 밖으로 내보내지 않는다)
#   ③ 서버가 Identity Toolkit 에 이메일+비밀번호로 로그인을 시도해 대조한다
#   ④ 성공하면 Admin SDK 로 Custom Token 을 만들어 돌려준다
#   ⑤ 프론트가 그 토큰으로 Firebase 에 로그인한다 → 이후 흐름은 구글 로그인과 같다
#
# ②의 이메일을 프론트로 돌려주면 아이디만으로 가입 이메일을 수집할 수 있게 되므로,
# 조회 결과는 서버 안에서만 쓰고 응답에는 담지 않는다.


class SignupRequest(BaseModel):
    # 길이 제약을 여기서 강하게 걸면 Pydantic 이 영어 원시 오류를 그대로 내보낸다.
    # 형식 검사는 credentials 모듈이 맡고, 여기서는 과도한 입력만 막는다.
    email:    str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=200)


# ── 관리자 예약 계정 ────────────────────────────────────────────────────────────
# 관리자는 'admin' 이라는 아이디로 로그인한다. 인증 자체는 이메일 기반이므로
# 내부적으로는 아래 주소를 쓰고, 로그인 입력만 여기서 바꿔준다.
# 도메인이 .local 이라 실제로 메일이 오갈 수 없다 — 비밀번호 재설정 메일 같은
# 외부 경로가 이 계정을 건드릴 수 없다는 뜻이라, 오히려 안전하다.
ADMIN_LOGIN_ID = "admin"
ADMIN_EMAIL    = "admin@pfp.local"


def _resolve_login_id(raw: str) -> str:
    """로그인 입력을 실제 이메일로 바꾼다. 'admin' 만 특별 취급한다."""
    v = raw.strip().lower()
    return ADMIN_EMAIL if v == ADMIN_LOGIN_ID else v


def _is_reserved(email: str) -> bool:
    """일반 가입이 쓸 수 없는 주소인지. 관리자 계정을 회원가입으로 만들거나
    가로채지 못하게 막는다."""
    return email.strip().lower() in (ADMIN_EMAIL, ADMIN_LOGIN_ID)


class LoginRequest(BaseModel):
    email:    str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=200)


def _custom_token_for(uid: str, provider: str) -> str:
    from firebase_admin import auth as fb_auth
    token = fb_auth.create_custom_token(uid, {"provider": provider})
    return token.decode() if isinstance(token, bytes) else token


@router.get("/email-available")
def email_available(email: str):
    """이메일 사용 가능 여부 — 가입 폼의 중복 확인용.

    가입 여부를 알려주는 것은 이메일 존재 여부를 노출하는 일이기도 하다.
    다만 가입 폼에서는 어차피 시도하면 알 수 있고, 안내가 없으면 사용자가
    원인을 모른 채 막히므로 여기서는 알려준다.
    """
    if _is_reserved(email):
        return {"available": False, "reason": "사용할 수 없는 이메일입니다."}
    ok, reason = validate_email(email)
    if not ok:
        return {"available": False, "reason": reason}
    owner = users_repo.find_by_email(email.strip().lower())
    if owner:
        return {"available": False,
                "reason": f"이미 {_provider_label(owner.get('provider'))}(으)로 가입된 이메일입니다."}
    return {"available": True, "reason": ""}


@router.post("/signup")
def signup(body: SignupRequest):
    """이메일 + 비밀번호로 가입하고 곧바로 로그인시킨다.

    규칙 검사를 서버에서 다시 하는 이유는, 프론트 검증은 우회할 수 있기 때문이다.
    아이디는 따로 받지 않고 이메일에서 표시용 이름을 만들어 둔다.
    """
    for ok, reason in (validate_email(body.email), validate_password(body.password)):
        if not ok:
            raise HTTPException(status_code=400, detail=reason)

    email = body.email.strip().lower()
    # 관리자 계정은 회원가입으로 만들 수 없다 — 시드 스크립트로만 생성된다.
    if _is_reserved(email):
        raise HTTPException(status_code=400, detail="사용할 수 없는 이메일입니다.")

    owner = users_repo.find_by_email(email)
    if owner:
        raise HTTPException(
            status_code=409,
            detail=f"이미 {_provider_label(owner.get('provider'))}(으)로 가입된 이메일입니다. "
                   f"기존 방식으로 로그인해 주세요.",
        )

    if not init_firebase():
        raise HTTPException(status_code=503, detail="인증 서버를 사용할 수 없습니다.")
    from firebase_admin import auth as fb_auth

    try:
        user = fb_auth.create_user(email=email, password=body.password)
    except Exception as e:
        if "EMAIL_EXISTS" in str(e).upper() or "ALREADY_EXISTS" in str(e).upper():
            raise HTTPException(status_code=409, detail="이미 가입된 이메일입니다.")
        logger.error(f"가입 실패 ({email}): {e}")
        raise HTTPException(status_code=400, detail="가입에 실패했습니다. 입력값을 확인해 주세요.")

    display = _auto_username(email.split("@")[0], user.uid)
    users_repo.upsert_user(uid=user.uid, email=email, name=display,
                           provider="password", email_verified=False, username=display)
    if not users_repo.get_user(user.uid):
        # 저장이 실패하면 로그인할 수 없는 반쪽 계정이 남는다 — 되돌린다.
        try:
            fb_auth.delete_user(user.uid)
        except Exception:
            pass
        raise HTTPException(status_code=409, detail="이미 가입된 이메일입니다.")

    return {"uid": user.uid, "email": email, "username": display,
            "custom_token": _custom_token_for(user.uid, "password")}


@router.post("/login")
def login(body: LoginRequest):
    """이메일 + 비밀번호 로그인. 관리자는 이메일 대신 'admin' 을 쓴다."""
    email = _resolve_login_id(body.email)

    # 이메일이 없을 때와 비밀번호가 틀렸을 때를 같은 문구로 돌려준다 —
    # 구분해 주면 어떤 이메일이 가입돼 있는지 하나씩 확인할 수 있다.
    INVALID = "이메일 또는 비밀번호가 올바르지 않습니다."
    account = users_repo.find_by_email(email)
    if not account:
        raise HTTPException(status_code=401, detail=INVALID)
    if account.get("provider") and account["provider"] != "password":
        raise HTTPException(
            status_code=409,
            detail=f"{_provider_label(account['provider'])}(으)로 가입된 계정입니다. "
                   f"해당 방법으로 로그인해 주세요.",
        )

    try:
        r = requests.post(
            f"{IDENTITY_TOOLKIT}/accounts:signInWithPassword?key={FIREBASE_WEB_API_KEY}",
            json={"email": email, "password": body.password, "returnSecureToken": False},
            timeout=10,
        )
    except Exception as e:
        logger.error(f"Identity Toolkit 호출 실패: {e}")
        raise HTTPException(status_code=502, detail="인증 서버에 연결할 수 없습니다.")

    if r.status_code != 200:
        msg = ((r.json().get("error") or {}).get("message") or "").upper()
        if "TOO_MANY_ATTEMPTS" in msg:
            raise HTTPException(status_code=429,
                                detail="시도가 너무 많습니다. 잠시 후 다시 시도해 주세요.")
        raise HTTPException(status_code=401, detail=INVALID)

    if not init_firebase():
        raise HTTPException(status_code=503, detail="인증 서버를 사용할 수 없습니다.")
    users_repo.touch_login(account["uid"])
    return {"uid": account["uid"], "email": email, "username": account.get("username"),
            "custom_token": _custom_token_for(account["uid"], "password")}


@router.post("/social/register")
def social_register(user: dict = Depends(verified_user)):
    """구글·카카오 인증을 마친 계정의 가입 완료 처리.

    Firebase 인증만으로는 가입이 아니다. 여기서 users 행을 만들어야 비로소
    서비스에 접근할 수 있다. 표시용 이름은 이메일에서 자동으로 만든다 —
    가입 단계를 늘리지 않기 위해서다.
    """
    if is_registered(user["uid"]):
        raise HTTPException(status_code=409, detail="이미 가입된 계정입니다. 로그인해 주세요.")

    email = (user.get("email") or "").strip().lower() or None

    # 소셜 제공자가 예약 주소를 발급할 일은 없지만, 값이 어디서 오든
    # 관리자 계정으로 통하는 길은 한 군데도 열어두지 않는다.
    if email and _is_reserved(email):
        raise HTTPException(status_code=400, detail="사용할 수 없는 이메일입니다.")

    # 같은 이메일을 다른 방법으로 이미 쓰고 있으면 계정이 갈라진다 — 막는다.
    if email:
        owner = users_repo.find_by_email(email)
        if owner and owner["uid"] != user["uid"]:
            raise HTTPException(
                status_code=409,
                detail=f"이미 {_provider_label(owner.get('provider'))}(으)로 가입된 "
                       f"이메일입니다. 기존 방식으로 로그인해 주세요.",
            )

    # 카카오·네이버는 Custom Token 으로 들어오므로 sign_in_provider 가 "custom" 이다.
    # 그대로 저장하면 나중에 "다른 방법으로 가입됨"이라는 모호한 안내가 나가므로,
    # uid 접두어에서 실제 공급자를 되살린다.
    provider = user.get("provider")
    for prefix in ("kakao", "naver"):
        if user["uid"].startswith(f"{prefix}:"):
            provider = prefix
            break

    display = _auto_username(user.get("name") or (email or "").split("@")[0], user["uid"])
    users_repo.upsert_user(
        uid=user["uid"], email=email, name=display,
        provider=provider, email_verified=user.get("email_verified", False),
        photo_url=user.get("picture"), username=display,
    )
    if not users_repo.get_user(user["uid"]):
        raise HTTPException(status_code=409, detail="이미 가입된 이메일입니다.")

    return {"uid": user["uid"], "email": email, "username": display, "registered": True}


class PasswordResetIn(BaseModel):
    email: str = Field(min_length=1, max_length=320)


@router.post("/password-reset")
def password_reset(body: PasswordResetIn):
    """비밀번호 재설정 메일 발송.

    Firebase 클라이언트 SDK 로 바로 보내면 안 되는 이유가 있다. 이 프로젝트는
    이메일 열거 보호가 켜져 있어, 가입한 적 없는 주소든 구글로만 가입한 계정이든
    **전부 성공(200)** 을 돌려준다. 그래서 사용자는 "메일을 보냈습니다" 를 보고
    받은 편지함만 하염없이 확인하게 된다.

    여기서 먼저 우리 DB 를 확인해 실제로 메일이 갈 수 있는 경우인지 판정하고,
    안 되는 경우에는 이유를 알려준다.
    """
    ok, reason = validate_email(body.email)
    if not ok:
        raise HTTPException(status_code=400, detail=reason)

    email = body.email.strip().lower()
    account = users_repo.find_by_email(email)
    if not account:
        raise HTTPException(status_code=404, detail="가입되지 않은 이메일입니다.")

    provider = account.get("provider")
    if provider and provider != "password":
        raise HTTPException(
            status_code=409,
            detail=f"{_provider_label(provider)}(으)로 가입된 계정입니다. "
                   f"비밀번호가 없으므로 해당 방법으로 로그인해 주세요.",
        )

    try:
        r = requests.post(
            f"{IDENTITY_TOOLKIT}/accounts:sendOobCode?key={FIREBASE_WEB_API_KEY}",
            json={"requestType": "PASSWORD_RESET", "email": email},
            # Firebase 기본 템플릿의 한국어 번역본으로 보내달라는 표시.
            headers={"X-Firebase-Locale": "ko"},
            timeout=10,
        )
    except Exception as e:
        logger.error(f"재설정 메일 발송 실패: {e}")
        raise HTTPException(status_code=502, detail="메일 발송 서버에 연결할 수 없습니다.")

    if r.status_code != 200:
        msg = ((r.json().get("error") or {}).get("message") or "").upper()
        if "TOO_MANY_ATTEMPTS" in msg:
            raise HTTPException(status_code=429,
                                detail="요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.")
        logger.warning(f"재설정 메일 발송 거부: {r.text[:200]}")
        raise HTTPException(status_code=502, detail="메일을 보내지 못했습니다. 잠시 후 다시 시도해 주세요.")

    return {"sent": True, "email": email}

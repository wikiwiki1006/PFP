"""
db/users_repo.py
────────────────
사용자 레코드 관리.

수집 범위를 최소로 유지한다 — 이메일·표시이름·프로필사진 URL 뿐이다.
비밀번호는 저장하지 않는다 (Firebase Auth 가 관리). 전화번호·생년월일 등
민감 정보는 받지도 저장하지도 않는다.
"""
from __future__ import annotations

import logging
from typing import Optional

from backend.db import get_conn, is_available

logger = logging.getLogger(__name__)

# 탈퇴자가 남긴 공용 리포트의 작성자 자리 (schema.py 가 이 행을 만들어 둔다).
ANONYMIZED_UID = "__deleted__"


def upsert_user(
    uid: str,
    email: Optional[str] = None,
    name: Optional[str] = None,
    provider: Optional[str] = None,
    email_verified: bool = False,
    photo_url: Optional[str] = None,
    username: Optional[str] = None,
) -> bool:
    """로그인 시 사용자 레코드 생성/갱신 + 최종 로그인 시각 기록.

    이름·사진은 사용자가 앱에서 바꿨을 수 있으므로 이미 값이 있으면 덮어쓰지
    않는다(COALESCE). 이메일·인증여부·공급자는 항상 최신으로 맞춘다.
    """
    if not is_available() or not uid:
        return False
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO users(id, email, name, provider, email_verified,
                                         photo_url, username, last_login_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,NOW())
                       ON CONFLICT (id) DO UPDATE SET
                         email          = COALESCE(EXCLUDED.email, users.email),
                         name           = COALESCE(users.name, EXCLUDED.name),
                         provider       = COALESCE(EXCLUDED.provider, users.provider),
                         email_verified = EXCLUDED.email_verified,
                         photo_url      = COALESCE(users.photo_url, EXCLUDED.photo_url),
                         username       = COALESCE(users.username, EXCLUDED.username),
                         last_login_at  = NOW()""",
                    (uid, (email or "").lower() or None, name, provider,
                     bool(email_verified), photo_url, username),
                )
        return True
    except Exception as e:
        logger.error(f"upsert_user({uid}) 실패: {e}")
        return False


def touch_login(uid: str) -> bool:
    """최종 로그인 시각만 갱신한다 — **행을 만들지 않는다**.

    upsert_user 를 로그인마다 부르면 가입한 적 없는 계정도 행이 생겨
    "가입 여부"를 판정할 수 없게 된다. 그래서 갱신 전용 함수를 따로 둔다.
    """
    if not is_available() or not uid:
        return False
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("UPDATE users SET last_login_at=NOW() WHERE id=%s", (uid,))
            return cur.rowcount > 0
    except Exception as e:
        logger.debug(f"touch_login({uid}) 실패: {e}")
        return False


def get_user(uid: str) -> Optional[dict]:
    if not is_available() or not uid:
        return None
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT id, email, name, provider, email_verified, photo_url,
                              created_at, last_login_at, disabled, username, age,
                              COALESCE(is_admin, FALSE), COALESCE(default_market, 'US')
                       FROM users WHERE id = %s""",
                    (uid,),
                )
                r = cur.fetchone()
        if not r:
            return None
        return {
            "uid": r[0], "email": r[1], "name": r[2], "provider": r[3],
            "email_verified": r[4], "photo_url": r[5],
            "created_at": r[6].isoformat() if r[6] else None,
            "last_login_at": r[7].isoformat() if r[7] else None,
            "disabled": bool(r[8]),
            "username": r[9],
            "age": r[10],
            "is_admin": bool(r[11]),
            "default_market": r[12],
        }
    except Exception as e:
        logger.error(f"get_user({uid}) 실패: {e}")
        return None


def update_profile(uid: str, name: Optional[str] = None,
                   age: Optional[int] = None, clear_age: bool = False,
                   default_market: Optional[str] = None) -> bool:
    """표시이름·나이 변경. 이메일은 Firebase Auth 쪽이 원본이라 여기서 바꾸지 않는다.

    나이는 선택 항목이라 "안 보냄"(그대로 두기)과 "비움"(NULL 로 지우기)을
    구분해야 한다. None 하나로는 둘을 표현할 수 없어 clear_age 를 따로 둔다.
    """
    if not is_available() or not uid:
        return False
    sets, args = [], []
    if name is not None:
        sets.append("name=%s"); args.append(name.strip()[:100])
    if clear_age:
        sets.append("age=NULL")
    elif age is not None:
        sets.append("age=%s"); args.append(int(age))
    if default_market is not None:
        # 아는 시장만 저장한다. 임의 문자열이 들어가면 조회가 전부 빗나가
        # 화면이 조용히 빈 채로 열린다.
        from backend.services.markets import normalize
        sets.append("default_market=%s"); args.append(normalize(default_market))
    if not sets:
        return False
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE users SET {', '.join(sets)} WHERE id=%s", (*args, uid))
            return cur.rowcount > 0
    except Exception as e:
        logger.error(f"update_profile({uid}) 실패: {e}")
        return False


def delete_user(uid: str) -> dict:
    """계정 삭제 — 개인 데이터 전부 제거 (탈퇴 요구 대응).

    holdings/trade_log/reports 는 FK ON DELETE CASCADE 로 함께 지워진다.
    공용 리서치 리포트(scope='shared')는 특정 개인의 것이 아니므로,
    작성자만 익명 처리하고 내용은 남긴다.
    """
    if not is_available() or not uid:
        return {"deleted": False}
    if uid == ANONYMIZED_UID:
        # 탈퇴자들의 공용 리포트가 매달린 자리다. 지우면 그 리포트들이
        # CASCADE 로 함께 사라진다.
        return {"deleted": False, "error": "익명 사용자 행은 삭제할 수 없습니다."}
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE reports SET user_id=%s "
                    "WHERE user_id=%s AND scope='shared'", (ANONYMIZED_UID, uid),
                )
                anonymized = cur.rowcount
                cur.execute("DELETE FROM users WHERE id=%s", (uid,))
                deleted = cur.rowcount > 0
        return {"deleted": deleted, "shared_reports_anonymized": anonymized}
    except Exception as e:
        logger.error(f"delete_user({uid}) 실패: {e}")
        return {"deleted": False, "error": str(e)}


# ── 아이디(로그인 식별자) ─────────────────────────────────────────────────────

def username_taken(username: str) -> bool:
    """이미 쓰는 아이디인지. DB 미연결이면 '사용 중'으로 간주해 중복 생성을 막는다."""
    if not username:
        return True
    if not is_available():
        return True
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM users WHERE LOWER(username)=LOWER(%s) LIMIT 1", (username,))
            return cur.fetchone() is not None
    except Exception as e:
        logger.error(f"username_taken({username}) 실패: {e}")
        return True


def find_by_username(username: str) -> Optional[dict]:
    """아이디로 계정 조회. 로그인 시 이메일을 찾는 데만 쓴다.

    이 결과(특히 이메일)는 **절대 클라이언트로 내보내지 않는다** — 아이디만 알면
    가입 이메일을 알아낼 수 있게 되어 이메일 수집·표적 피싱에 쓰인다.
    """
    if not is_available() or not username:
        return None
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, email, username, disabled FROM users "
                "WHERE LOWER(username)=LOWER(%s) LIMIT 1", (username,),
            )
            r = cur.fetchone()
        if not r:
            return None
        return {"uid": r[0], "email": r[1], "username": r[2], "disabled": bool(r[3])}
    except Exception as e:
        logger.error(f"find_by_username({username}) 실패: {e}")
        return None


def find_by_email(email: str) -> Optional[dict]:
    """이메일로 계정 조회. 가입 공급자가 겹치는지 판단하는 데만 쓴다.

    find_by_username 과 마찬가지로 결과를 그대로 클라이언트에 내보내면 안 된다.
    """
    if not is_available() or not email:
        return None
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, username, provider, disabled FROM users "
                "WHERE LOWER(email)=LOWER(%s) LIMIT 1", (email,),
            )
            r = cur.fetchone()
        return {"uid": r[0], "username": r[1], "provider": r[2],
                "disabled": bool(r[3])} if r else None
    except Exception as e:
        logger.error(f"find_by_email 실패: {e}")
        return None


def set_username(uid: str, username: str) -> bool:
    """아이디 등록. 유니크 인덱스 위반이면 False (경쟁 상황에서도 안전하다)."""
    if not is_available() or not uid or not username:
        return False
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("UPDATE users SET username=%s WHERE id=%s", (username, uid))
            return cur.rowcount > 0
    except Exception as e:
        logger.warning(f"set_username({uid}, {username}) 실패: {e}")
        return False


def is_admin(uid: str) -> bool:
    """관리자 계정인지. 권한은 DB 만을 근거로 판단한다 —
    토큰의 custom claim 은 갱신 전까지 옛 값을 들고 있어, 권한을 회수해도
    한동안 관리자로 통과할 수 있다."""
    if not is_available() or not uid:
        return False
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COALESCE(is_admin, FALSE) FROM users WHERE id = %s", (uid,))
                r = cur.fetchone()
        return bool(r and r[0])
    except Exception as e:
        logger.error(f"is_admin 조회 실패: {e}")
        return False


def set_admin(uid: str, value: bool = True) -> bool:
    """관리자 권한 부여/회수. 시드 스크립트에서만 호출한다 —
    HTTP 로 노출하면 권한 상승 경로가 된다."""
    if not is_available() or not uid:
        return False
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE users SET is_admin = %s WHERE id = %s", (value, uid))
                changed = cur.rowcount
            conn.commit()
        return changed > 0
    except Exception as e:
        logger.error(f"set_admin 실패: {e}")
        return False

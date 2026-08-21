"""
services/credentials.py
───────────────────────
아이디·비밀번호 규칙.

**서버가 최종 판정한다.** 프론트에도 같은 규칙을 두어 즉시 피드백을 주지만,
클라이언트 검증은 우회할 수 있으므로 가입 처리 직전에 여기서 다시 검사한다.
"""
from __future__ import annotations

import re
import unicodedata

# 아이디: 영문 소문자·숫자·밑줄·하이픈, 4~20자. 영문으로 시작한다.
# 숫자로 시작하면 다른 사용자의 내부 ID 와 헷갈리고, 대문자를 허용하면
# 'Admin' 과 'admin' 이 다른 계정이 되어 사칭에 쓰일 수 있다.
USERNAME_RE = re.compile(r"^[a-z][a-z0-9_-]{3,19}$")

# 서비스 운영에 쓰이거나 사칭 위험이 있는 아이디는 막는다.
RESERVED_USERNAMES = {
    "admin", "administrator", "root", "system", "support", "help", "official",
    "pfp", "api", "auth", "login", "signup", "user", "users", "me", "null",
    "undefined", "test", "guest", "anonymous", "master", "manager", "staff",
}

PASSWORD_MIN = 8
PASSWORD_MAX = 72          # bcrypt 계열 상한과 맞춘다


def normalize_username(raw: str) -> str:
    """비교·저장용 정규화. 유니코드 유사 문자를 이용한 사칭을 막는다."""
    return unicodedata.normalize("NFKC", (raw or "").strip()).lower()


def validate_username(raw: str) -> tuple[bool, str]:
    """(통과 여부, 실패 사유). 사유는 그대로 사용자에게 보여준다."""
    u = normalize_username(raw)
    if not u:
        return False, "아이디를 입력해 주세요."
    if len(u) < 4 or len(u) > 20:
        return False, "아이디는 4~20자여야 합니다."
    if not USERNAME_RE.match(u):
        return False, "아이디는 영문 소문자로 시작하고, 영문·숫자·밑줄(_)·하이픈(-)만 쓸 수 있습니다."
    if u in RESERVED_USERNAMES:
        return False, "사용할 수 없는 아이디입니다."
    return True, ""


def validate_password(pw: str) -> tuple[bool, str]:
    """비밀번호 정책: 8자 이상 + 영문 + 숫자 + 특수문자.

    길이만으로도 어느 정도 방어가 되지만, 실제 사용자는 짧은 비밀번호를 고르는
    경향이 있어 문자 종류를 강제한다.
    """
    pw = pw or ""
    if len(pw) < PASSWORD_MIN:
        return False, f"비밀번호는 {PASSWORD_MIN}자 이상이어야 합니다."
    if len(pw) > PASSWORD_MAX:
        return False, f"비밀번호는 {PASSWORD_MAX}자 이하여야 합니다."
    if " " in pw:
        return False, "비밀번호에 공백은 쓸 수 없습니다."
    if not re.search(r"[A-Za-z]", pw):
        return False, "비밀번호에 영문자를 포함해 주세요."
    if not re.search(r"\d", pw):
        return False, "비밀번호에 숫자를 포함해 주세요."
    if not re.search(r"[^A-Za-z0-9]", pw):
        return False, "비밀번호에 특수문자를 포함해 주세요."
    return True, ""


def validate_email(email: str) -> tuple[bool, str]:
    e = (email or "").strip()
    if not e or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$", e):
        return False, "이메일 형식이 올바르지 않습니다."
    if len(e) > 254:
        return False, "이메일이 너무 깁니다."
    return True, ""

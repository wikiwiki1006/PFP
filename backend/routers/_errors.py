"""
routers/_errors.py
──────────────────
실패를 응답에 실을 때의 규칙 — **내부 예외 문자열은 사용자에게 보내지 않는다.**

프론트는 서버의 `detail` 을 그대로 화면에 그린다 (브리핑·매크로·최적화·종목
모달·로그인). 그래서 `detail=str(e)` 는 DB 오류, 라이브러리 메시지, 접속 주소,
파이썬 예외 이름을 사용자 화면에 올린다. 사용자는 그 문장으로 할 수 있는 일이
없고, 노출돼서는 안 되는 내부 사정이 섞일 수 있다.

그래서 둘로 가른다.
  · 원인(예외 전체)은 **로그**로만 — `logger.error(..., exc_info=...)`.
  · 응답에는 사용자가 **무엇을 하면 되는지** 담긴 문장만.

서비스가 처음부터 사용자용 문장으로 올리는 예외(예: 브리핑의 '기준일 종가가
아직 없다')는 그 문장이 곧 알려야 할 사유다. 하지만 서비스는 그런 문장도 내부
실패와 같은 `RuntimeError`/`ValueError` 로 올려서 **타입으로는 가를 수 없다.**
그래서 호출자가 그 문장의 모양을 허용 목록(정규식)으로 적고, 목록에 없으면 닫힌
쪽(일반 문장)으로 간다 — 서비스 문구가 바뀌면 사유 대신 일반 문장이 나갈 뿐,
내부 문자열이 새지는 않는다. 그 전환은 로그에 남는다.
"""
from __future__ import annotations

import logging
import re
from typing import Iterable

from fastapi import HTTPException


def log_hidden(logger: logging.Logger, what: str, exc: BaseException) -> None:
    """응답에 싣지 않은 예외를 로그로 남긴다. 원인을 찾을 곳은 여기뿐이다."""
    logger.error("%s 실패 — 원인은 응답에 싣지 않았다 (사용자에게는 일반 안내)",
                 what, exc_info=(type(exc), exc, exc.__traceback__))


def hidden_http_error(logger: logging.Logger, what: str, exc: BaseException, *,
                      status_code: int, message: str) -> HTTPException:
    """원인은 로그로, 응답에는 `message` 만 싣는 HTTPException 을 만든다 (raise 는 호출자)."""
    log_hidden(logger, what, exc)
    return HTTPException(status_code=status_code, detail=message)


def user_sentence(exc: BaseException, allowed: Iterable[re.Pattern[str]]) -> str | None:
    """서비스가 **사용자용 문장**으로 올린 예외면 그 문장, 아니면 None.

    `allowed` 는 문장 전체의 앞부분을 맞추는 정규식들이다 (`re.match`).
    """
    msg = str(exc).strip()
    if not msg:
        return None
    return msg if any(p.match(msg) for p in allowed) else None

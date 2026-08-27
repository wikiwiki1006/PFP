"""
routers/admin.py
────────────────
관리자 전용 API — 사이트 기능 스위치.

모든 라우트가 admin_user 의존성을 거친다. 관리자가 아니면 404 를 받는다
(403 은 "그런 기능이 있다"를 알려주므로 존재 자체를 숨긴다).

권한은 요청마다 DB 에서 확인한다. 토큰의 custom claim 을 쓰면 권한을 회수해도
토큰이 만료될 때까지 관리자로 통과하기 때문이다.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.db import settings_repo
from backend.services.auth import admin_user, current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"])


class FlagUpdate(BaseModel):
    ai_enabled: bool | None = None
    deep_analysis_enabled: bool | None = None
    deep_analysis_daily_limit: bool | None = None


@router.get("/settings")
def read_settings(_admin: dict = Depends(admin_user)):
    """현재 기능 스위치 상태."""
    return settings_repo.get_settings(force=True)


@router.patch("/settings")
def update_settings(body: FlagUpdate, admin: dict = Depends(admin_user)):
    """기능 스위치 변경. 보낸 항목만 바뀐다."""
    changes = {k: v for k, v in body.model_dump().items() if v is not None}
    if not changes:
        raise HTTPException(status_code=400, detail="변경할 항목이 없습니다.")

    for key, value in changes.items():
        if not settings_repo.set_flag(key, value, updated_by=admin["uid"]):
            raise HTTPException(status_code=503, detail="설정을 저장하지 못했습니다.")
        logger.info(f"관리자 {admin['uid']} 가 {key} 를 {value} 로 변경")

    return settings_repo.get_settings(force=True)


# ── 일반 사용자도 읽는 공개 상태 ────────────────────────────────────────────────

public_router = APIRouter(prefix="/api", tags=["admin"])


@public_router.get("/features")
def read_features(user: dict = Depends(current_user)):
    """로그인한 사용자에게 **자기에게 적용되는** 기능 가용 상태를 알려준다.

    프론트가 이 값을 보고 심층 분석 옵션을 숨기거나 생성 버튼을 잠근다.
    화면을 잠그는 것만으로는 부족하고 서버에서도 막지만(ai_feature_user,
    resolve_model_tier), 눌러도 실패하는 버튼을 보여주지 않으려면 이 정보가 필요하다.

    관리자는 스위치와 무관하게 전부 사용할 수 있으므로 항상 True 를 받는다.
    """
    from backend.db.users_repo import is_admin as _is_admin

    s = settings_repo.get_settings()
    admin = _is_admin(user["uid"])
    return {
        "is_admin":              admin,
        "ai_enabled":            True if admin else bool(s["ai_enabled"]),
        "deep_analysis_enabled": True if admin else bool(s["deep_analysis_enabled"]),
        # 제한이 켜져 있어도 관리자 본인은 걸리지 않는다.
        "deep_analysis_daily_limit": False if admin else bool(s["deep_analysis_daily_limit"]),
    }

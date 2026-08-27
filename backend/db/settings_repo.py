"""
db/settings_repo.py
───────────────────
사이트 전역 설정 — 관리자만 바꿀 수 있는 기능 스위치.

AI 기능은 호출마다 외부 API 비용이 나간다. 비용이 튀거나 키를 교체하는 동안
서비스 전체를 내리지 않고 해당 기능만 끌 수 있어야 해서 만들었다.

값은 DB 에 두되 짧게 캐시한다. 매 요청마다 DB 를 읽으면 리포트 생성처럼
잦은 경로에서 왕복이 쌓이고, 반대로 캐시가 길면 관리자가 스위치를 내려도
한참 동안 먹히지 않는다. 몇 초면 두 문제를 모두 피한다.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

from backend.db import get_conn, is_available

logger = logging.getLogger(__name__)

# 기본값 — DB 에 행이 없거나 DB 를 못 읽을 때 쓰인다.
# 둘 다 True 다: 설정을 못 읽었다는 이유로 사용자 기능이 갑자기 막히면 안 된다.
DEFAULTS: dict[str, Any] = {
    # False 면 일반 사용자는 AI 기능(리포트·시나리오 생성)을 쓸 수 없다.
    "ai_enabled": True,
    # False 면 일반 사용자는 '심층 분석'을 고를 수 없고 '기본 분석'만 쓴다.
    "deep_analysis_enabled": True,
    # True 면 일반 사용자의 심층 분석을 계정당 24시간에 1회로 제한한다.
    # 심층 분석은 기본 분석보다 토큰을 훨씬 많이 쓰므로, 기능을 통째로 끄지 않고
    # 빈도만 조이고 싶을 때 쓴다.
    "deep_analysis_daily_limit": False,
}

_CACHE_TTL_SECONDS = 5

_lock = threading.Lock()
_cache: dict[str, Any] = {}
_cache_at: float = 0.0


def _load_from_db() -> dict[str, Any]:
    if not is_available():
        return {}
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT key, value FROM site_settings")
                rows = cur.fetchall()
        out: dict[str, Any] = {}
        for key, value in rows:
            out[key] = value if not isinstance(value, str) else json.loads(value)
        return out
    except Exception as e:
        logger.error(f"site_settings 조회 실패: {e}")
        return {}


def get_settings(force: bool = False) -> dict[str, Any]:
    """현재 설정 전체. 기본값 위에 DB 값을 덮어쓴 결과."""
    global _cache, _cache_at
    with _lock:
        fresh = (time.time() - _cache_at) < _CACHE_TTL_SECONDS
        if not force and fresh and _cache:
            return dict(_cache)
    stored = _load_from_db()
    merged = {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}
    with _lock:
        _cache = merged
        _cache_at = time.time()
    return dict(merged)


def get_flag(key: str) -> Any:
    return get_settings().get(key, DEFAULTS.get(key))


def set_flag(key: str, value: Any, updated_by: str) -> bool:
    """설정 변경. 아는 키만 받는다 — 임의 키를 쓰게 두면 오타가 조용히 저장된다."""
    if key not in DEFAULTS:
        raise ValueError(f"알 수 없는 설정 항목입니다: {key}")
    if not is_available():
        return False
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO site_settings (key, value, updated_by, updated_at)
                       VALUES (%s, %s::jsonb, %s, NOW())
                       ON CONFLICT (key) DO UPDATE
                         SET value = EXCLUDED.value,
                             updated_by = EXCLUDED.updated_by,
                             updated_at = NOW()""",
                    (key, json.dumps(value), updated_by),
                )
            conn.commit()
        # 관리자가 방금 내린 스위치는 즉시 반영돼야 한다.
        get_settings(force=True)
        return True
    except Exception as e:
        logger.error(f"site_settings 저장 실패: {e}")
        return False

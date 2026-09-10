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

# 기본값 — site_settings 에 그 키의 행이 아직 없을 때 쓰인다.
# 셋 다 "허용" 쪽 값이다: 관리자가 아직 아무것도 설정하지 않았다는 이유로
# 사용자 기능이 막히면 안 된다.
#
# **DB 를 못 읽었을 때 쓰는 값이 아니다.** 예전에는 그 두 경우가 같았고, 그래서
# DB 가 잠깐 흔들리면 관리자가 켜 둔 심층 분석 제한이 조용히 풀렸다.
# `deep_analysis_daily_limit` 만 폴라리티가 반대(True 가 '제한')인데 같은
# "허용 쪽으로 통일" 규칙을 적용한 결과였다. 지금은 get_settings() 가 마지막
# 성공값 / _FALLBACK_WHEN_UNKNOWN 으로 그 경우를 따로 처리한다.
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

# DB 를 못 읽었고 마지막 성공값도 아직 없을 때(기동 직후) 쓰는 값.
#
# 관리자의 실제 설정을 모르는 상태다. 이때 심층 분석을 허용하면 상한이 걸려
# 있는지 없는지 모르는 채로 비싼 LLM 호출이 나간다. 그래서 심층만 닫는다.
# 제한(daily_limit)이 아니라 기능 스위치(deep_analysis_enabled)를 내리는 이유:
# resolve_model_tier 가 이 값을 보고 deep → basic 으로 **강등**하므로 사용자는
# 429 를 맞지 않고 기본 분석 결과를 받는다. 닫되 잠그지는 않는다.
_FALLBACK_WHEN_UNKNOWN: dict[str, Any] = {
    **DEFAULTS,
    "deep_analysis_enabled": False,
}

_CACHE_TTL_SECONDS = 5
# 열화 상태가 길어져도 로그가 매 요청마다 쌓이지 않게 한다.
_DEGRADED_LOG_EVERY = 60.0

_lock = threading.Lock()
# _cache 는 "지금 서빙 중인 값" 이다 — 열화 중에는 임시값일 수도 있다.
# _last_good 은 "DB 에서 마지막으로 실제로 읽어낸 값" 이다. 둘을 나눠야
# 임시값을 마지막 성공값으로 착각하지 않는다.
_cache: dict[str, Any] = {}
_cache_at: float = 0.0
_last_good: dict[str, Any] | None = None
# 마지막 조회가 실패했는가. 마지막 성공값을 서빙 중이라는 뜻이기도 하다.
_degraded: bool = False
_degraded_logged_at: float = 0.0


def _load_from_db() -> dict[str, Any] | None:
    """site_settings 전체. **읽지 못하면 None** 을 돌려준다.

    `{}` 는 "행이 없다"(아직 아무것도 설정하지 않았다)는 뜻으로만 쓴다.
    예전에는 둘이 같은 값이라 호출자가 구별할 수 없었고, 그게 관리자가 켠
    제한이 조용히 풀리던 원인이었다 (§1.3).
    """
    if not is_available():
        return None
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
        return None


def _note_degraded(has_last_good: bool) -> None:
    """열화 상태를 로그에 남긴다. 진입할 때 한 번, 그 뒤로는 주기적으로.

    조용히 옛 값을 서빙하면 그것대로 §1.3 이다 — 관리자가 스위치를 내렸는데
    반영이 안 되는 상황과 구별되지 않는다. 반드시 보이게 남긴다.
    """
    global _degraded_logged_at
    now = time.time()
    if _degraded and (now - _degraded_logged_at) < _DEGRADED_LOG_EVERY:
        return
    _degraded_logged_at = now
    if has_last_good:
        logger.error(
            "site_settings 를 읽지 못해 마지막으로 성공한 값을 계속 쓰는 중이다. "
            "관리자가 지금 스위치를 바꿔도 반영되지 않는다."
        )
    else:
        logger.error(
            "site_settings 를 읽지 못했고 마지막 성공값도 없다. "
            f"심층 분석을 닫은 임시값으로 동작한다: {_FALLBACK_WHEN_UNKNOWN}"
        )


def get_settings(force: bool = False) -> dict[str, Any]:
    """현재 설정 전체. 기본값 위에 DB 값을 덮어쓴 결과.

    DB 를 못 읽으면 **마지막으로 성공한 값을 계속 쓴다.** 기본값으로 되돌아가면
    관리자가 켜 둔 제한이 DB 가 흔들리는 동안 조용히 풀린다. 마지막 성공값이
    아직 없으면(기동 직후) _FALLBACK_WHEN_UNKNOWN 으로 떨어진다.
    어느 쪽이든 로그를 남긴다.
    """
    global _cache, _cache_at, _last_good, _degraded
    with _lock:
        fresh = (time.time() - _cache_at) < _CACHE_TTL_SECONDS
        if not force and fresh and _cache:
            return dict(_cache)

    stored = _load_from_db()

    if stored is None:
        with _lock:
            _note_degraded(_last_good is not None)
            _degraded = True
            _cache = dict(_last_good) if _last_good else dict(_FALLBACK_WHEN_UNKNOWN)
            # 실패해도 _cache_at 은 갱신한다. 안 하면 DB 가 죽어 있는 동안
            # 모든 요청이 그대로 조회를 재시도해 장애 위에 부하를 더한다.
            _cache_at = time.time()
            return dict(_cache)

    merged = {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}
    with _lock:
        if _degraded:
            logger.info("site_settings 조회가 복구됐다. DB 값으로 돌아간다.")
        _degraded = False
        _last_good = merged
        _cache = merged
        _cache_at = time.time()
    return dict(merged)


def is_degraded() -> bool:
    """마지막 조회가 실패해 옛 값(또는 임시값)을 서빙 중인가."""
    with _lock:
        return _degraded


def get_flag(key: str) -> Any:
    return get_settings().get(key, DEFAULTS.get(key))


def set_flag(key: str, value: Any, updated_by: str) -> bool:
    """설정 변경. 아는 키만 받는다 — 임의 키를 쓰게 두면 오타가 조용히 저장된다."""
    if key not in DEFAULTS:
        raise ValueError(f"알 수 없는 설정 항목입니다: {key}")
    if not is_available():
        logger.error(f"DB 미연결: 설정 {key} 을(를) 저장하지 못했다")
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

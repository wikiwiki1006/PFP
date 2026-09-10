"""
backend/tests/helpers/json_safety.py
────────────────────────────────────
응답 페이로드에 남은 NaN/Inf 를 **중첩까지 따라가며** 찾는다.

평평한 dict 만 훑으면 `/holdings-detail` 처럼 리스트 안에 dict 가 들어가는
응답에서 아무것도 못 본다 — 그리고 그런 응답일수록 필드가 많아 누락이 숨기 쉽다.
"""
from __future__ import annotations

import math
from typing import Any, Iterator


def walk_non_finite(obj: Any, path: str = "$") -> Iterator[tuple[str, float]]:
    """(경로, 값) 을 내놓는다. 경로가 있어야 어느 필드인지 바로 찾는다."""
    if isinstance(obj, bool):
        return                                   # bool 은 float 가 아니다
    if isinstance(obj, float):
        if not math.isfinite(obj):
            yield path, obj
        return
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield from walk_non_finite(value, f"{path}.{key}")
        return
    if isinstance(obj, (list, tuple)):
        for i, value in enumerate(obj):
            yield from walk_non_finite(value, f"{path}[{i}]")


def non_finite(obj: Any) -> dict[str, float]:
    """{경로: 값}. 비어 있으면 페이로드가 깨끗하다."""
    return dict(walk_non_finite(obj))

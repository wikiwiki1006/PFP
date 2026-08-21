"""
services/job_store.py
─────────────────────
백그라운드 잡 상태 저장소.

macro / reports / optimizer 라우터가 각자 동일한 구현을 갖고 있었다
(macro 와 reports 는 문자 단위로 같았고, optimizer 는 락이 없어 경쟁 위험이 있었다).
하나로 합쳐 동작을 통일하고, 잡 종류별로 인스턴스를 따로 두어 서로 밀어내지 않게 한다.

프로세스 메모리에만 존재하므로 서버 재시작 시 초기화된다 —
잡 결과가 유실되면 프론트엔드는 404 를 받고 재요청하면 된다.

잡 결과에는 요청자의 포트폴리오 분석이 담긴다. 그래서 잡마다 소유자(uid)를
기록하고 조회·취소 시 대조한다. job_id 는 UUID 라 추측이 어렵지만, 그건
접근 제어가 아니라 난독화일 뿐이다.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Optional


class JobStore:
    """스레드 안전 LRU 잡 저장소.

    max_jobs 를 넘으면 가장 오래된 잡부터 제거한다. 잡 종류마다 별도 인스턴스를
    쓰므로, 리포트 잡이 많이 쌓여도 매크로 잡이 밀려나지 않는다.
    """

    def __init__(self, max_jobs: int = 50):
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._max = max_jobs

    def set(self, job_id: str, data: dict, owner: Optional[str] = None) -> None:
        """잡 상태 저장. owner 는 최초 생성 시 한 번만 주면 이후 갱신에도 유지된다."""
        with self._lock:
            prev = self._jobs.get(job_id) or {}
            self._jobs[job_id] = {
                **data,
                "_ts": time.time(),
                "_owner": owner or prev.get("_owner"),
            }
            while len(self._jobs) > self._max:
                oldest = min(self._jobs, key=lambda k: self._jobs[k]["_ts"])
                del self._jobs[oldest]

    def get(self, job_id: str, owner: Optional[str] = None) -> Optional[dict]:
        """내부 필드를 뺀 사본 반환. 없거나 소유자가 다르면 None.

        소유자가 다를 때도 None 을 돌려준다 — 403 으로 구분해 주면 "그 잡은
        존재한다"는 사실이 새어나가므로, 없는 것과 똑같이 취급한다.
        """
        with self._lock:
            j = self._jobs.get(job_id)
            if not j:
                return None
            if owner is not None and j.get("_owner") not in (None, owner):
                return None
            return {k: v for k, v in j.items() if k not in ("_ts", "_owner")}

    def update_if(self, job_id: str, expect_status: str, data: dict) -> bool:
        """현재 상태가 expect_status 일 때만 갱신. 갱신했으면 True.

        취소 처리 경쟁을 막기 위해 필요하다 — 사용자가 취소한 잡에
        백그라운드 스레드가 뒤늦게 결과를 덮어쓰는 것을 방지한다.
        """
        with self._lock:
            cur = self._jobs.get(job_id)
            if not cur or cur.get("status") != expect_status:
                return False
            self._jobs[job_id] = {**data, "_ts": time.time(), "_owner": cur.get("_owner")}
            return True

    def cancel(self, job_id: str, owner: Optional[str] = None) -> Optional[str]:
        """실행 중이면 취소 처리. 이전 상태를 반환하고, 잡이 없거나 남의 것이면 None."""
        with self._lock:
            cur = self._jobs.get(job_id)
            if not cur:
                return None
            if owner is not None and cur.get("_owner") not in (None, owner):
                return None
            prev = cur.get("status")
            if prev in ("pending", "running"):
                self._jobs[job_id] = {**cur, "status": "cancelled", "_ts": time.time()}
            return prev

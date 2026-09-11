"""
services/job_store.py
─────────────────────
백그라운드 잡 상태 저장소.

macro / reports / optimizer 라우터가 각자 동일한 구현을 갖고 있었다
(macro 와 reports 는 문자 단위로 같았고, optimizer 는 락이 없어 경쟁 위험이 있었다).
하나로 합쳐 동작을 통일하고, 잡 종류별로 kind 를 달리해 서로 구분한다.

**상태는 DB(jobs 테이블)에 둔다.**
예전에는 프로세스 메모리에만 있었다. Cloud Run 은 인스턴스를 여러 개 띄우고
세션 고정도 없어서, 생성은 A 인스턴스에서 도는데 폴링이 B 로 가면
"잡을 찾을 수 없습니다" 가 떴다 — 사용자에게는 리포트가 증발한 것으로 보인다.
DB 에 두면 어느 인스턴스가 받아도 같은 상태를 보고, 서버가 재시작돼도 남는다.

DB 를 못 쓰는 환경(로컬 개발 등)에서는 메모리로 자동 폴백한다. 그때는 예전과
똑같이 동작한다 — 단일 프로세스라 어차피 문제가 없다.

잡 결과에는 요청자의 포트폴리오 분석이 담긴다. 그래서 잡마다 소유자(uid)를
기록하고 조회·취소 시 대조한다. job_id 는 UUID 라 추측이 어렵지만, 그건
접근 제어가 아니라 난독화일 뿐이다.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# 취소 여부를 DB 에서 다시 확인하는 최소 간격(초).
# LLM 스트리밍은 조각이 초당 수십 개씩 오는데 그때마다 DB 를 때릴 수는 없다.
# 다른 인스턴스에서 누른 취소가 이 간격 안에 반영되면 사람이 느끼기엔 즉시다.
_CANCEL_POLL_SECONDS = 2.0

# 청소 기준 — 이보다 오래된 잡은 지운다. 폴링은 길어야 몇 분이면 끝난다.
_JOB_TTL_HOURS = 24


class JobCancelled(Exception):
    """사용자가 잡을 취소해 작업을 중단했음을 알린다.

    백그라운드 작업은 중간중간 취소 여부를 확인하고, 취소됐으면 이 예외를
    올려 즉시 빠져나온다. 일반 오류와 구분되어야 한다 — 취소는 실패가 아니라서
    저장·알림을 하지 않고 조용히 끝내야 하기 때문이다.
    """


class _Anonymous:
    """`owner` 자리에 쓰는 '로그인하지 않은 호출자' 표식.

    `None` 을 재사용할 수 없다. `None` 은 이미 **"소유자 검사를 하지 마"**
    (내부 호출·정리 작업)라는 뜻이고, 그 한 값이 두 뜻을 갖는 동안
    **비로그인 호출자가 검사 면제 경로를 탔다** — 남의 잡을 읽고 취소할 수
    있었다 (`/api/optimizer/ai-optimize-job/{id}` 가 `optional_user` 다).

    빈 문자열이나 `"anonymous"` 같은 문자열도 안 된다. `user_id` 컬럼에
    그대로 저장될 수 있고, 그러면 그 값을 가진 '사용자' 가 생긴다.
    객체 식별자라 DB 에 새어 들어갈 자리가 없다.
    """
    __slots__ = ()
    def __repr__(self) -> str:      # 로그에 <object at 0x…> 로 찍히지 않게
        return "ANONYMOUS"


#: 소유자 인자의 세 번째 상태. `None`(검사 안 함) · `ANONYMOUS` · uid 문자열.
ANONYMOUS = _Anonymous()

#: `owner` 인자가 받는 것. 세 상태를 한 자리에 담는다.
Owner = Optional[str] | _Anonymous


def _db():
    """DB 모듈. 사용할 수 없으면 None."""
    try:
        from backend.db import get_conn, is_available
        return (get_conn, is_available) if is_available() else None
    except Exception:
        return None


class JobStore:
    """잡 상태 저장소. 기본은 DB, 안 되면 메모리."""

    def __init__(self, kind: str = "job", max_jobs: int = 50):
        self.kind = kind
        self._max = max_jobs
        # 메모리 폴백용
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        # 취소 신호 — 같은 인스턴스에서 누른 취소를 DB 왕복 없이 즉시 전달한다.
        self._events: dict[str, threading.Event] = {}
        # job_id → 마지막으로 DB 를 확인한 시각
        self._last_poll: dict[str, float] = {}

    # ── 저장 ────────────────────────────────────────────────────────────────

    def set(self, job_id: str, data: dict, owner: Optional[str] = None) -> None:
        """잡 상태 저장. owner 는 최초 생성 시 한 번만 주면 이후 갱신에도 유지된다."""
        with self._lock:
            self._events.setdefault(job_id, threading.Event())

        db = _db()
        if db is None:
            self._mem_set(job_id, data, owner)
            return

        get_conn, _ = db
        status  = data.get("status", "pending")
        message = data.get("message")
        # status·message 를 뺀 나머지는 통째로 담는다. 라우터마다 넣는 필드가
        # 달라서(리포트는 result, 최적화는 stage/stage_text) 컬럼을 고정할 수 없다.
        payload = {k: v for k, v in data.items() if k not in ("status", "message")}
        result  = payload or None
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO jobs (id, kind, user_id, status, result, message)
                           VALUES (%s, %s, %s, %s, %s::jsonb, %s)
                           ON CONFLICT (id) DO UPDATE SET
                               status     = EXCLUDED.status,
                               result     = EXCLUDED.result,
                               message    = EXCLUDED.message,
                               -- owner 는 최초 값을 유지한다 (갱신 때 None 이 와도 덮지 않음)
                               user_id    = COALESCE(jobs.user_id, EXCLUDED.user_id),
                               updated_at = NOW()""",
                        (job_id, self.kind, owner, status,
                         json.dumps(result) if result is not None else None, message),
                    )
                conn.commit()
        except Exception as e:
            logger.error(f"jobs 저장 실패({job_id}): {e}")
            self._mem_set(job_id, data, owner)

    def _mem_set(self, job_id: str, data: dict, owner: Optional[str]) -> None:
        with self._lock:
            prev = self._jobs.get(job_id) or {}
            self._jobs[job_id] = {
                **data, "_ts": time.time(), "_owner": owner or prev.get("_owner"),
            }
            while len(self._jobs) > self._max:
                oldest = min(self._jobs, key=lambda k: self._jobs[k]["_ts"])
                del self._jobs[oldest]
                self._events.pop(oldest, None)

    # ── 조회 ────────────────────────────────────────────────────────────────

    @staticmethod
    def _visible(job_owner: Optional[str], owner: "Owner") -> bool:
        """이 호출자에게 이 잡이 보이는가.

        DB 경로와 메모리 경로가 **같은 규칙**을 써야 해서 한 곳에 둔다.
        갈라 두면 한쪽만 고쳐진다 — 실제로 익명 결함이 양쪽에 똑같이 있었다.

            owner is None        소유자 검사 안 함 (내부 호출·정리) → 전부
            owner is ANONYMOUS   비로그인 호출자                  → 무주공산만
            owner == uid         로그인 호출자                    → 자기 것 + 무주공산

        무주공산(`job_owner is None`)이 로그인·비로그인 모두에게 보이는 것은
        의도다. 비로그인 최적화 잡이 거기 들어가고, 그 사용자가 자기 잡의
        진행 상황을 봐야 한다.
        """
        if owner is None:
            return True
        if owner is ANONYMOUS:
            return job_owner is None
        return job_owner in (None, owner)

    def get(self, job_id: str, owner: "Owner" = None) -> Optional[dict]:
        """상태 반환. 없거나 소유자가 다르면 None.

        소유자가 다를 때도 None 을 돌려준다 — 403 으로 구분해 주면 "그 잡은
        존재한다"는 사실이 새어나가므로, 없는 것과 똑같이 취급한다.

        `owner` 는 **세 상태**다 (`_visible` 참고). 비로그인 호출자는 `None`
        이 아니라 `ANONYMOUS` 를 넘겨야 한다.
        """
        db = _db()
        if db is None:
            return self._mem_get(job_id, owner)

        get_conn, _ = db
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT status, result, message, user_id, cancelled "
                        "FROM jobs WHERE id = %s AND kind = %s",
                        (job_id, self.kind),
                    )
                    r = cur.fetchone()
        except Exception as e:
            logger.error(f"jobs 조회 실패({job_id}): {e}")
            return self._mem_get(job_id, owner)

        if not r:
            return None
        status, result, message, user_id, cancelled = r
        if not self._visible(user_id, owner):
            return None

        payload = result if isinstance(result, dict) else (
            json.loads(result) if isinstance(result, str) else {}
        )
        out: dict[str, Any] = {**(payload or {}),
                               "status": "cancelled" if cancelled else status}
        if message is not None:
            out["message"] = message
        return out

    def _mem_get(self, job_id: str, owner: "Owner") -> Optional[dict]:
        with self._lock:
            j = self._jobs.get(job_id)
            if not j:
                return None
            if not self._visible(j.get("_owner"), owner):
                return None
            return {k: v for k, v in j.items() if k not in ("_ts", "_owner")}

    # ── 원자적 갱신 ─────────────────────────────────────────────────────────

    def update_if(self, job_id: str, expect_status: str, data: dict) -> bool:
        """현재 상태가 expect_status 이고 취소되지 않았을 때만 갱신. 갱신했으면 True.

        취소 처리 경쟁을 막기 위해 필요하다 — 사용자가 취소한 잡에 백그라운드
        스레드가 뒤늦게 결과를 덮어쓰는 것을 방지한다. DB 에서는 WHERE 절이
        이 검사를 원자적으로 처리하므로 인스턴스가 여러 개여도 안전하다.
        """
        db = _db()
        if db is None:
            return self._mem_update_if(job_id, expect_status, data)

        get_conn, _ = db
        payload = {k: v for k, v in data.items() if k not in ("status", "message")}
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE jobs
                           SET status = %s, result = %s::jsonb, message = %s, updated_at = NOW()
                           WHERE id = %s AND kind = %s
                             AND status = %s AND cancelled = FALSE""",
                        (data.get("status", "done"),
                         json.dumps(payload) if payload else None,
                         data.get("message"), job_id, self.kind, expect_status),
                    )
                    changed = cur.rowcount
                conn.commit()
            return changed > 0
        except Exception as e:
            logger.error(f"jobs 갱신 실패({job_id}): {e}")
            return self._mem_update_if(job_id, expect_status, data)

    def _mem_update_if(self, job_id: str, expect_status: str, data: dict) -> bool:
        with self._lock:
            cur = self._jobs.get(job_id)
            if not cur or cur.get("status") != expect_status:
                return False
            self._jobs[job_id] = {**data, "_ts": time.time(), "_owner": cur.get("_owner")}
            return True

    # ── 취소 ────────────────────────────────────────────────────────────────

    def cancel(self, job_id: str, owner: "Owner" = None) -> Optional[str]:
        """실행 중이면 취소 처리. 이전 상태를 반환하고, 잡이 없거나 남의 것이면 None.

        소유자 판정은 아래 `get()` 하나를 지난다 — 취소에 따로 규칙을 두면
        읽기와 쓰기의 권한이 갈린다. 읽을 수 없는 잡은 취소도 못 한다.
        """
        cur = self.get(job_id, owner=owner)
        if cur is None:
            return None
        prev = cur.get("status")

        # 같은 인스턴스에서 도는 작업에는 즉시 알린다.
        with self._lock:
            ev = self._events.get(job_id)
        if ev is not None:
            ev.set()

        if prev not in ("pending", "running"):
            return prev

        db = _db()
        if db is None:
            with self._lock:
                j = self._jobs.get(job_id)
                if j:
                    self._jobs[job_id] = {**j, "status": "cancelled", "_ts": time.time()}
            return prev

        get_conn, _ = db
        try:
            with get_conn() as conn:
                with conn.cursor() as cur_:
                    cur_.execute(
                        """UPDATE jobs SET cancelled = TRUE, status = 'cancelled', updated_at = NOW()
                           WHERE id = %s AND kind = %s AND status IN ('pending','running')""",
                        (job_id, self.kind),
                    )
                conn.commit()
        except Exception as e:
            logger.error(f"jobs 취소 실패({job_id}): {e}")
        return prev

    def is_cancelled(self, job_id: str) -> bool:
        """취소됐는지.

        같은 인스턴스에서 취소했다면 로컬 신호로 즉시 알 수 있다. 다른 인스턴스가
        취소한 경우를 위해 DB 도 확인하되, 매번 읽으면 스트리밍이 느려지므로
        _CANCEL_POLL_SECONDS 간격으로만 확인한다.
        """
        with self._lock:
            ev = self._events.get(job_id)
            if ev is not None and ev.is_set():
                return True
            last = self._last_poll.get(job_id, 0.0)
            now = time.time()
            if now - last < _CANCEL_POLL_SECONDS:
                return False
            self._last_poll[job_id] = now

        db = _db()
        if db is None:
            with self._lock:
                j = self._jobs.get(job_id)
                # 메모리에서 밀려났다면 결과를 돌려줄 곳도 없다 — 취소로 본다.
                return j is None or j.get("status") == "cancelled"

        get_conn, _ = db
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT cancelled FROM jobs WHERE id = %s AND kind = %s",
                                (job_id, self.kind))
                    r = cur.fetchone()
        except Exception:
            # DB 를 못 읽었다는 이유로 진행 중인 작업을 죽이지는 않는다.
            return False

        if r is None:
            return False          # 아직 안 만들어졌거나 지워졌다 — 계속 진행
        if r[0]:
            with self._lock:
                e = self._events.get(job_id)
                if e is not None:
                    e.set()       # 다음 확인부터는 DB 를 안 봐도 된다
            return True
        return False

    def cancel_token(self, job_id: str) -> Callable[[], bool]:
        """작업 함수에 넘길 취소 확인 함수.

        JobStore 자체를 넘기지 않는 이유는, 서비스 계층이 잡 저장소를 알 필요가
        없기 때문이다 — "멈춰야 하나?"만 물으면 된다.
        """
        with self._lock:
            self._events.setdefault(job_id, threading.Event())
        return lambda: self.is_cancelled(job_id)

    # ── 청소 ────────────────────────────────────────────────────────────────

    def purge_stale(self, ttl_hours: int = _JOB_TTL_HOURS) -> int:
        """오래된 잡 삭제. 지운 개수를 반환한다."""
        db = _db()
        if db is None:
            return 0
        get_conn, _ = db
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM jobs WHERE created_at < NOW() - (%s * INTERVAL '1 hour')",
                        (ttl_hours,),
                    )
                    n = cur.rowcount
                conn.commit()
            return n
        except Exception as e:
            logger.error(f"jobs 청소 실패: {e}")
            return 0

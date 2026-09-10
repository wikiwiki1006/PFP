"""
거절된 요청은 pending 잡을 남기지 않는다 — 429·503·500 셋 다.

심층 분석은 잡을 만들고 `{job_id}` 를 돌려준 뒤 백그라운드에서 돈다. 화면은
그 잡을 폴링한다. 그래서 **거절하면서 잡을 먼저 만들면** 화면은 영원히
끝나지 않는 분석을 폴링한다 — 사용자에게 거절이 거절로 보이지 않고, 서비스가
느린 것처럼 보인다. 다시 눌러도 같은 일이 반복된다.

세 가지 거절이 전부 같아야 한다:

    429  이미 오늘 썼다      (`usage_repo.consume` → False)
    503  DB 가 혼잡하다      (`consume` → DBBusy, main.py 핸들러가 번역)
    500  확인 자체가 불가능   (`consume` → RuntimeError 등)

**셋 다 "닫힘" 이다.** 확인할 수 없는 상태에서 통과시키면 제한이 사실상
없어진다. 그러니 세 경우 모두 잡이 없어야 하고, 잡이 없다는 것을 **저장소를
직접 보고** 확인한다 — `_job_set` 을 모킹해 "안 불렸다" 만 재면, 다른 경로로
잡이 생기는 변경을 놓친다.

## 왜 미니 앱을 세우는가

`backend.main.app` 을 그대로 쓰면 startup 이벤트(DB 풀·스케줄러)가 돈다.
그렇다고 라우터 함수를 직접 부르면 **상태 코드를 못 잰다** — `DBBusy` 는
`main.py` 의 예외 핸들러가 503 으로 번역하는 것이라 함수 밖에서 일어난다.
사용자가 보는 것은 그 번역 결과다. 그래서 라우터와 **main.py 의 진짜 핸들러**만
얹은 앱을 세운다.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.db import DBBusy, PoolExhausted
from backend.routers import macro
from backend.services.auth import ai_feature_user
from backend.services.markets import market_param

UID = "reject-test-uid"


@pytest.fixture
def client(monkeypatch):
    """macro 라우터 + main.py 의 실제 DBBusy 핸들러만 얹은 앱.

    심층 티어로 고정한다. 사전 검사(`enforce_deep_limit`)는 통과시키고,
    원자적 소비(`consume`)만 테스트가 갈아끼운다 — 재는 대상이 그것이다.
    """
    from backend.main import _db_busy_handler

    app = FastAPI()
    # 라우터가 이미 prefix="/api/macro" 를 들고 있다. 여기서 또 붙이면 경로가
    # 겹쳐 전부 404 가 되는데, 404 도 "잡이 안 생겼다" 는 통과하므로 거절
    # 테스트만 있었다면 초록불로 지나갔다. 대조군이 그걸 잡았다.
    app.include_router(macro.router)
    app.add_exception_handler(DBBusy, _db_busy_handler)

    app.dependency_overrides[ai_feature_user] = lambda: {"uid": UID}
    app.dependency_overrides[market_param] = lambda: "US"

    monkeypatch.setattr(macro, "resolve_model_tier", lambda *a, **kw: "deep")
    monkeypatch.setattr(macro, "enforce_deep_limit", lambda *a, **kw: None)

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def job_count():
    """잡 저장소를 직접 센다. DB 가 없으면 메모리 폴백이라 결정적이다."""
    def count() -> int:
        # `set()` 은 잡을 담기 전에 취소 이벤트부터 만든다. 둘 다 세어야
        # '만들다 만' 흔적도 잡힌다.
        return len(macro._store._jobs) + len(macro._store._events)
    return count


def _set_consume(monkeypatch, outcome):
    """`usage_repo.consume` 을 갈아끼운다. 예외 인스턴스를 주면 던진다."""
    from backend.db import usage_repo

    def fake(user_id, kind, *a, **kw):
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(usage_repo, "consume", fake)


_BODY = {"event": "관세 인상", "model": "claude-sonnet-4-6", "mode": "quick"}


@pytest.mark.parametrize("outcome, status, why", [
    (False, 429, "이미 오늘 썼다"),
    (PoolExhausted("커넥션이 없습니다. 잠시 후 다시 시도해 주세요."), 503, "DB 혼잡"),
    (RuntimeError("DB 미연결"), 500, "확인 자체가 불가능"),
])
def test_rejected_deep_request_creates_no_job(client, job_count, monkeypatch,
                                              outcome, status, why):
    """거절은 거절로 끝난다 — 상태 코드가 맞고, 잡이 생기지 않는다."""
    _set_consume(monkeypatch, outcome)
    before = job_count()

    resp = client.post("/api/macro/analyze", json=_BODY)

    assert resp.status_code == status, (
        f"{why}: got {resp.status_code}, expected {status} -- body={resp.text[:200]}"
    )
    assert job_count() == before, (
        f"{why}: a job was created for a rejected request -- the UI will poll "
        "an analysis that never runs, so the rejection never reaches the user. "
        "(거절된 요청이 pending 잡을 남겼다.)"
    )


def test_accepted_request_does_create_a_job(client, job_count, monkeypatch):
    """대조군 — 소비에 성공하면 잡이 생긴다.

    이게 없으면 위 세 개는 "어떤 요청도 잡을 만들지 않는" 구현으로도 전부
    통과한다. 그건 거절을 제대로 다루는 것이 아니라 기능이 죽은 것이다.
    """
    _set_consume(monkeypatch, True)
    # 잡이 생기면 백그라운드 스레드가 실제 분석을 시작한다. 네트워크를 타지
    # 않도록 즉시 끝나게 한다 — 재는 것은 잡 생성이지 분석 결과가 아니다.
    monkeypatch.setattr(macro, "run_macro_agents",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("stub")))
    before = job_count()

    resp = client.post("/api/macro/analyze", json=_BODY)

    assert resp.status_code == 200, resp.text
    assert "job_id" in resp.json(), resp.text
    assert job_count() > before, "accepted request created no job"


def test_basic_tier_does_not_consume_quota(client, job_count, monkeypatch):
    """심층이 아니면 할당량을 건드리지 않는다.

    `consume` 이 basic 요청에서도 불리면 하루 한 번 제한이 심층과 무관하게
    소모된다. 여기서는 불리면 즉시 실패하게 두어 그 경로를 고정한다.
    """
    from backend.db import usage_repo

    def must_not_run(*a, **kw):
        raise AssertionError("basic tier consumed deep-analysis quota")

    monkeypatch.setattr(macro, "resolve_model_tier", lambda *a, **kw: "basic")
    monkeypatch.setattr(usage_repo, "consume", must_not_run)
    monkeypatch.setattr(macro, "run_macro_agents",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("stub")))

    resp = client.post("/api/macro/analyze",
                       json={**_BODY, "model": "claude-haiku-4-5-20251001"})

    assert resp.status_code == 200, resp.text


def test_db_busy_is_translated_to_503_not_500():
    """`DBBusy` → 503 매핑이 `main.py` 에 살아 있는지.

    위 테스트는 그 핸들러를 직접 얹어 쓴다. 앱에서 핸들러가 빠지면 같은
    상황이 500 으로 나가는데, 500 은 사용자에게 **재시도해도 되는지를 알려주지
    않는다.** 테스트가 쓰는 전제와 앱의 실제 배선이 갈라지지 않게 고정한다.
    """
    from backend.main import app

    assert DBBusy in app.exception_handlers, (
        "main.py no longer registers a DBBusy handler -- pool exhaustion and "
        "lock timeouts go out as 500 instead of 503. "
        "(재시도 가능한 혼잡이 '우리가 망가졌다' 로 나간다.)"
    )

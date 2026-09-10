"""
공용 캐시로 돌려준 요청은 심층 할당량을 쓰지 않는다.

리포트는 24시간 공용 캐시가 있다. 다른 사용자가 같은 종목을 이미 분석했으면
그 결과를 그대로 돌려준다 — LLM 을 부르지 않으니 비용도 들지 않는다. 그런데
그 경로에서 할당량을 소비하면, **아무것도 만들지 않고 하루 한 번을 태운다.**
사용자는 자기가 쓴 적 없는 분석 때문에 하루를 기다린다.

`usage_repo.consume` 에는 **되돌리기가 없다.** 그래서 "나중에 취소하면 돌려준다"
가 성립하지 않고, 애초에 **실제로 만들기로 확정된 지점에서만** 불려야 한다.
캐시 확인이 소비보다 앞에 있어야 한다는 뜻이다.

## 호출 여부가 아니라 행 수를 센다

`consume` 을 모킹해 "안 불렸다" 를 재는 방법도 있다. 그건 규약의 절반만
검사한다 — 캐시 히트가 **다른 경로로** 소비하게 되는 변경을 놓친다.
사용자에게 의미하는 것은 "그 함수가 안 불렸다" 가 아니라 **"할당량이 안
깎였다"** 이므로, `deep_analysis_usage` 의 행 수를 직접 센다.

그래서 실DB 가 필요하다. 행 수를 세는 것은 모킹으로 대체할 수 없다.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.db as db
from backend.db import DBBusy
from backend.routers import reports
from backend.services.auth import ai_feature_user
from backend.services.markets import market_param

_CACHED_RESULT = {
    "raw": "# 캐시된 리포트",
    "sections": {},
    "file_path": "cached.md",
    "model_tier": "deep",
    "from_cache": True,
    "report_type": "equity",
    "ticker": "AAPL",
    "company_name": "Apple",
}


@pytest.fixture
def quota_uid(db_uid):
    """`db_uid` 에 사용 기록 정리를 더한다.

    `deep_analysis_usage` 는 `users` 를 FK 로 물지 않아서, 사용자를 지워도
    사용 기록은 CASCADE 로 따라가지 않는다. 남겨두면 다음 실행이 "이미 썼다"
    로 판정돼 테스트가 자기 꼬리를 문다.
    """
    yield db_uid
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM deep_analysis_usage WHERE user_id=%s", (db_uid,))
            cur.execute("DELETE FROM jobs WHERE user_id=%s", (db_uid,))


@pytest.fixture
def usage_count(quota_uid):
    """이 사용자의 심층 사용 기록 수."""
    def count() -> int:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM deep_analysis_usage WHERE user_id=%s",
                    (quota_uid,),
                )
                return cur.fetchone()[0]
    return count


@pytest.fixture
def client(quota_uid, monkeypatch):
    """reports 라우터만 얹은 앱. 심층 티어로 고정하고 사전 검사는 통과시킨다.

    `usage_repo.consume` 은 **모킹하지 않는다** — 실제로 행을 넣는 그 함수가
    돌아야 행 수가 의미를 갖는다.
    """
    from backend.main import _db_busy_handler

    app = FastAPI()
    app.include_router(reports.router)      # 라우터가 자기 prefix 를 들고 있다
    app.add_exception_handler(DBBusy, _db_busy_handler)

    app.dependency_overrides[ai_feature_user] = lambda: {"uid": quota_uid}
    app.dependency_overrides[market_param] = lambda: "US"

    monkeypatch.setattr(reports, "resolve_model_tier", lambda *a, **kw: "deep")
    monkeypatch.setattr(reports, "enforce_deep_limit", lambda *a, **kw: None)
    # 캐시 미스일 때 백그라운드 스레드가 실제 리포트를 쓰지 않게 한다.
    # 재는 것은 할당량이지 리포트 내용이 아니다.
    monkeypatch.setattr(
        reports, "write_equity_report",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("stub")))

    return TestClient(app, raise_server_exceptions=False)


def _start(client):
    return client.post("/api/reports/equity-research/start", json={
        "ticker": "AAPL", "model_tier": "deep",
    })


# ── 본론 ──────────────────────────────────────────────────────────────────────

def test_cache_hit_does_not_consume_quota(client, usage_count, monkeypatch):
    """공용 캐시로 돌려주면 `deep_analysis_usage` 행이 늘지 않는다."""
    monkeypatch.setattr(reports, "_cached_shared_result", lambda *a, **kw: _CACHED_RESULT)
    before = usage_count()

    resp = _start(client)

    assert resp.status_code == 200, resp.text
    assert resp.json().get("cached") is True, (
        f"expected a cache hit, got {resp.json()} -- the test is not measuring "
        "the path it claims to"
    )
    assert usage_count() == before, (
        "a cache hit burned a deep-analysis slot -- nothing was generated, yet "
        "the user waits 24 hours for an analysis they never received. "
        "consume() has no release, so the cache check must come first. "
        "(만들지도 않고 하루치를 태웠다.)"
    )


def test_cache_miss_does_consume_quota(client, usage_count, monkeypatch):
    """대조군 — 실제로 만들면 행이 정확히 하나 늘어난다.

    이게 없으면 위 테스트는 **아무것도 소비하지 않는 구현**으로도 통과한다.
    그건 할당량이 제대로 지켜지는 것이 아니라 제한이 죽은 것이다.
    """
    monkeypatch.setattr(reports, "_cached_shared_result", lambda *a, **kw: None)
    before = usage_count()

    resp = _start(client)

    assert resp.status_code == 200, resp.text
    assert resp.json().get("cached") is not True, "expected a cache miss"
    assert usage_count() == before + 1, (
        f"cache miss consumed {usage_count() - before} slot(s), expected 1 -- "
        "the quota is not being recorded where the work actually starts."
    )


def test_second_deep_request_is_refused_and_creates_no_job(client, usage_count, monkeypatch):
    """이미 쓴 뒤의 두 번째 요청은 429 이고, 잡을 남기지 않는다.

    첫 요청이 진짜로 소비했는지는 두 번째가 막히는 것으로만 확인된다 —
    행이 늘어난 것과 그 행이 실제로 판정에 쓰이는 것은 다른 성질이다.
    """
    monkeypatch.setattr(reports, "_cached_shared_result", lambda *a, **kw: None)

    first = _start(client)
    assert first.status_code == 200, first.text
    used = usage_count()

    second = _start(client)

    assert second.status_code == 429, (
        f"second deep request returned {second.status_code}, expected 429 -- "
        f"body={second.text[:200]}"
    )
    assert usage_count() == used, "a refused request still recorded usage"


def test_cache_hit_is_not_blocked_by_a_spent_quota(client, usage_count, monkeypatch):
    """할당량을 다 쓴 뒤에도 캐시 히트는 돌아온다.

    캐시 확인이 소비보다 앞이라는 것은 두 방향으로 의미가 있다. 소비를
    건너뛰는 것과, **이미 소비한 사용자도 캐시는 받는 것.** 뒤쪽이 막히면
    아무 비용도 들지 않는 응답을 거절하는 셈이다.
    """
    monkeypatch.setattr(reports, "_cached_shared_result", lambda *a, **kw: None)
    assert _start(client).status_code == 200          # 할당량 소진
    spent = usage_count()

    monkeypatch.setattr(reports, "_cached_shared_result", lambda *a, **kw: _CACHED_RESULT)
    resp = _start(client)

    assert resp.status_code == 200, (
        f"a cache hit was refused with {resp.status_code} because the quota was "
        "already spent -- returning a cached report costs nothing. "
        f"body={resp.text[:200]}"
    )
    assert resp.json().get("cached") is True, resp.text
    assert usage_count() == spent, "the cache hit consumed another slot"

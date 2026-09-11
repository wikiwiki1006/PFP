"""
스키마 적용이 실패했을 때 **호출자가 그걸 알 수 있는가.**

예전에는 `init_schema()` 가 반환값 없이 예외를 삼켰다. 실패해도 앱은 정상
기동하고 로그 한 줄만 남는다. 그 상태에서 새 코드가 옛 스키마를 만나면
그 경로의 쓰기가 통째로 죽는데(`no unique or exclusion constraint matching
the ON CONFLICT specification`), 증상은 한참 뒤에 "저장이 안 된다" 로
나타난다 — 원인과 증상이 멀어 아무도 못 찾는다. §1.3(c) 그대로다.

실제로 세 창이 여기 걸렸고, 운영 확인을 할 때도 **기동 로그가 아니라 인덱스
정의를 직접 읽어야** 했다. 로그는 "뭔가 돌았다" 만 잰다.

## 기동을 막지 않는 것은 판단이다

예외를 올려 기동을 세우면 DB 순단 한 번에 배포가 멈춘다. 그래서 지금은
**신호만** 세웠다. 그 판단이 성립하려면 신호가 실제로 호출자에게 닿아야
하므로, 여기서 두 쪽을 다 잰다:

  ① `init_schema()` 가 실패를 **값으로** 돌려준다 (세 경로 전부).
  ② `main.py` 의 기동 경로가 그 값을 **읽고 기록한다.**

①만 재면 호출자가 무시해도 초록이고, ②만 재면 반환값이 항상 True 여도
초록이다.
"""
from __future__ import annotations

import logging
from unittest import mock

import pytest

from backend.db import schema


def _boom(*a, **kw):
    raise RuntimeError("DDL 실패")


# ── ① 실패가 값으로 나온다 ────────────────────────────────────────────────────

def test_an_unreachable_database_is_reported_as_not_applied(caplog):
    """DB 가 없으면 False 다 — **그리고 기록이 남는다.**

    이 경로는 예전에 그냥 `return` 이었다. 호출자에게는 "아무 일도 없었다"
    와 "적용했다" 가 똑같이 보였다.
    """
    with mock.patch.object(schema, "is_available", lambda: False), \
         caplog.at_level(logging.ERROR):
        applied = schema.init_schema()

    assert applied is False, (
        "schema application reported success while the database was not "
        "reachable -- the caller cannot tell that nothing was applied."
    )
    assert caplog.records, "적용을 못 했는데 아무 기록도 없다"


def test_a_failing_ddl_is_reported_as_not_applied(caplog):
    """DDL 이 터지면 False 다. 예외를 삼키되 **삼켰다는 사실은 남긴다.**"""
    with mock.patch.object(schema, "is_available", lambda: True), \
         mock.patch.object(schema, "get_conn", _boom), \
         caplog.at_level(logging.ERROR):
        applied = schema.init_schema()

    assert applied is False, (
        "a failed DDL reported success -- new code then runs on an old schema "
        "and the write paths die much later, far from the cause."
    )
    assert caplog.records, "DDL 이 터졌는데 아무 기록도 없다"


def test_a_successful_application_is_reported_as_applied(live_db):
    """대조군 — 실제로 적용되면 True 다.

    없으면 위 둘은 "언제나 False" 라는 구현으로도 통과하고, 그러면 기동
    로그가 매번 거짓 경고를 띄워 아무도 안 읽게 된다.
    """
    assert schema.init_schema() is True


def test_it_does_not_stop_the_process(caplog):
    """실패해도 예외를 올리지 않는다 — 기동을 막지 않는 쪽이 판단이다.

    DB 순단 한 번에 배포가 멈추지 않게 한 선택이다. 바꾸려면 전환 경로
    (새 리비전이 건강해지지 않고 트래픽이 옛 리비전에 남는가)를 먼저 봐야
    한다. 지금 판단을 여기 적어 둔다.
    """
    with mock.patch.object(schema, "is_available", lambda: True), \
         mock.patch.object(schema, "get_conn", _boom), \
         caplog.at_level(logging.ERROR):
        schema.init_schema()      # 예외가 새면 여기서 실패한다


# ── ② 기동 경로가 그 값을 읽는다 ──────────────────────────────────────────────

def test_startup_reports_what_is_at_risk_when_the_schema_did_not_apply(caplog):
    """기동이 실패를 읽고 **무엇이 위험한지**를 남긴다.

    반환값만 재면 호출자가 그걸 버려도 초록이다. 여기서는 기동 경로를
    실제로 돌려 기록이 남는지 본다.

    메시지는 "실패했다" 로 끝나면 안 된다 — 읽는 사람이 다음에 무엇을
    확인해야 하는지 알아야 한다. 그래서 결과(저장 경로가 죽을 수 있다)와
    참조(§7.4)가 같이 실렸는지 본다.
    """
    import backend.main as app_main

    with mock.patch.object(app_main, "logger") as fake_log, \
         mock.patch("backend.db.init_pool", lambda *a, **kw: True), \
         mock.patch("backend.db.schema.init_schema", lambda: False), \
         mock.patch("backend.routers.reports._store") as store:
        store.purge_stale.return_value = 0
        try:
            app_main.on_startup()
        except Exception:
            # 기동의 나머지 단계(스케줄러·프리패치)는 이 검사의 대상이 아니다.
            pass

    said = " ".join(str(c) for c in fake_log.error.call_args_list)
    assert said, (
        "startup ignored a failed schema application -- the return value "
        "exists but nobody reads it, which is where it was before."
    )
    assert "§7.4" in said or "7.4" in said, (
        f"the startup message does not point at what to check next: {said!r} "
        "-- '실패했다' alone leaves the reader with nowhere to go."
    )


def test_startup_says_nothing_when_the_schema_applied(caplog):
    """대조군 — 정상 적용이면 그 오류를 안 남긴다.

    없으면 위 검사는 "언제나 경고한다" 는 구현으로도 통과하고, 매 기동마다
    뜨는 경고는 곧 아무도 안 읽는 경고가 된다.
    """
    import backend.main as app_main

    with mock.patch.object(app_main, "logger") as fake_log, \
         mock.patch("backend.db.init_pool", lambda *a, **kw: True), \
         mock.patch("backend.db.schema.init_schema", lambda: True), \
         mock.patch("backend.routers.reports._store") as store:
        store.purge_stale.return_value = 0
        try:
            app_main.on_startup()
        except Exception:
            pass

    said = " ".join(str(c) for c in fake_log.error.call_args_list)
    assert "스키마" not in said, f"정상인데 스키마 오류를 남겼다: {said!r}"

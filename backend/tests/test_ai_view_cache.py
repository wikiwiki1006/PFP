"""
`backend/db/ai_view_cache.py` — 최적화의 종목별 AI 뷰 공용 캐시 (77fb691).

사용자 요구는 "다른 사용자가 이미 분석한 종목은 AI 를 다시 부르지 말고, 하루
한 번 갱신" 이다. 이 캐시가 틀리면 **모든 사용자에게** 틀린다 — 다른 시장의
뷰, 다른 투자 기간의 뷰, 만료된 뷰, 깨진 뷰가 다음 초기화까지 퍼진다. 그리고
실패는 전부 "캐시 미스" 처럼 보인다 (`{}` / `0`). 그래서 경계마다 실DB 로 잰다.

DB 역할의 측정 스크립트(검사 51개 · 교착 비교)를 게이트로 옮긴 것이다. 측정과
다르게 한 곳:

- **만료 '정각'** 은 같은 트랜잭션 안에서 잰다. `NOW()` 는 트랜잭션 시작 시각이라,
  `UPDATE ... SET expires_at = NOW()` 를 커밋하고 다른 트랜잭션에서 읽으면 그
  행은 이미 과거다 — `>` 를 `>=` 로 바꿔도 통과한다. 조회를 **같은 트랜잭션**에
  태워 `expires_at == NOW()` 를 실제로 만든다.
- **첫 저장 우선**은 `next_close_reset` 을 고정 시각으로 바꿔 잰다. 진짜 시계로
  재면 저장 사이에 초기화 시각이 지나갈 때 결과가 달라진다.
- **교착**은 가볍게 — 겹치는 500종목을 두 스레드가 반대 순서로 동시에 저장한다.
  정렬을 빼면 실측 5회 중 5회 교착(회당 약 1초), 정렬하면 0회 0.26초였다.

사용자 행은 테스트마다 고유 접두사를 쓰고 끝나면 그 접두사만 지운다.
"""
from __future__ import annotations

import logging
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

import backend.db as db
from backend.db import ai_view_cache as avc

_LOGGER = "backend.db.ai_view_cache"


def _view(er=0.12, conf=0.6, sent="Bullish", drv="실적 가이던스 상향"):
    return {"expected_return": er, "confidence": conf, "sentiment": sent, "key_driver": drv}


def _sql(query, params=None, fetch=False):
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchall() if fetch else cur.rowcount


def _row(market, months, ticker):
    rows = _sql("SELECT data, updated_at, expires_at FROM common_cache WHERE cache_type = %s",
                (avc._cache_key(market, months, ticker),), fetch=True)
    return rows[0] if rows else None


def _warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records
            if r.name == _LOGGER and r.levelno >= logging.WARNING]


@pytest.fixture
def tag(live_db):
    """이 테스트만의 티커 접두사. 끝나면 그 접두사의 행만 지운다."""
    prefix = "ZZT" + uuid.uuid4().hex[:8].upper()
    yield prefix
    _sql("DELETE FROM common_cache WHERE left(cache_type, 9) = 'ai_view::' "
         "AND split_part(cache_type, '::', 5) LIKE %s", (prefix + "%",))


@pytest.fixture
def fixed_reset(monkeypatch):
    """`next_close_reset` 을 고정 시각으로. `.at` 을 바꾸면 다음 주기가 된다."""
    state = type("Reset", (), {})()
    state.at = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=2)
    state.calls = []

    def fake(market, now=None, delay_minutes=30):
        state.calls.append(market)
        return state.at

    monkeypatch.setattr("backend.services.market_calendar.next_close_reset", fake)
    return state


# ── 저장 → 읽기 ───────────────────────────────────────────────────────────────

def test_saved_views_come_back_for_the_same_market_and_horizon(tag, caplog):
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    a, b = f"{tag}A", f"{tag}B"
    va, vb = _view(0.12, 0.6, "Bullish", "AI 수요"), _view(-0.05, 0.4, "Bearish", "밸류에이션")

    written = avc.save_ai_views("US", 1.0, {a: va, f" {b.lower()} ": vb})
    got = avc.get_ai_views("US", 1.0, [a, b.lower()])

    assert written == 2
    assert set(got) == {a, b}, "keys come back as stripped upper-case tickers"
    for ticker, sent in ((a, va), (b, vb)):
        assert {k: got[ticker][k] for k in sent} == sent
        assert set(got[ticker]) == set(sent) | {"cached_at"}
        stored = _row("US", 12, ticker)
        cached_at = datetime.fromisoformat(got[ticker]["cached_at"])
        assert cached_at.utcoffset() == timedelta(0) and cached_at == stored[1], (
            f"cached_at {got[ticker]['cached_at']} is not the row's DB write time {stored[1]}"
        )
    assert _warnings(caplog) == []


def test_a_partial_hit_returns_only_what_is_cached(tag):
    cached = [f"{tag}{i}" for i in range(3)]
    avc.save_ai_views("US", 0.5, {t: _view(drv=f"뷰 {t}") for t in cached})

    got = avc.get_ai_views("US", 0.5, [cached[0], f"{tag}X", cached[1], f"{tag}Y", cached[2]])

    assert set(got) == set(cached)


# ── 만료 ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("expired", ["NOW() - interval '1 second'", "NULL"],
                         ids=["1초-전", "만료-없음"])
def test_expired_or_unbounded_rows_are_not_returned(tag, expired):
    """`NULL` 도 내보내지 않는다 — `get_common` 은 영구 유효로 읽지만, 여기서 만료
    없는 행은 영원히 모든 사용자에게 퍼진다."""
    gone, kept = f"{tag}GONE", f"{tag}KEPT"
    avc.save_ai_views("US", 1.0, {gone: _view(), kept: _view()})
    _sql(f"UPDATE common_cache SET expires_at = {expired} WHERE cache_type = %s",
         (avc._cache_key("US", 12, gone),))

    got = avc.get_ai_views("US", 1.0, [gone, kept])

    assert set(got) == {kept}, f"got {sorted(got)} -- the other row is the control"


@contextmanager
def _one_transaction(update_sql: str, key: str):
    """풀에서 커넥션 하나를 잡아 `update_sql` 을 **커밋하지 않고** 실행한 채 모듈의
    `get_conn` 으로 내준다. 모듈의 조회가 같은 트랜잭션 — 같은 `NOW()` — 에서 돈다."""
    conn = db._pool.getconn()
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute(update_sql, (key,))
            assert cur.rowcount == 1

        @contextmanager
        def same_conn():
            yield conn

        yield same_conn
    finally:
        conn.rollback()
        db._pool.putconn(conn)


def test_expiry_boundary_is_exclusive(tag, monkeypatch):
    """`expires_at == NOW()` 인 행은 만료다. 커밋된 상태는 **반대 답**이 나오게
    둔다 — 트랜잭션 안의 값을 보지 못하면 두 단언 중 하나가 틀린다."""
    t = f"{tag}EDGE"
    avc.save_ai_views("US", 1.0, {t: _view()})
    key = avc._cache_key("US", 12, t)

    # 커밋된 행은 유효(미래). 트랜잭션 안에서만 정각으로 → 안 나와야 한다.
    with _one_transaction("UPDATE common_cache SET expires_at = NOW() WHERE cache_type = %s",
                          key) as same_conn, monkeypatch.context() as m:
        m.setattr(avc, "get_conn", same_conn)
        at_the_instant = avc.get_ai_views("US", 1.0, [t])

    # 커밋된 행은 만료(과거). 트랜잭션 안에서만 1µs 뒤로 → 나와야 한다.
    _sql("UPDATE common_cache SET expires_at = NOW() - interval '1 hour' WHERE cache_type = %s",
         (key,))
    with _one_transaction(
            "UPDATE common_cache SET expires_at = NOW() + interval '1 microsecond' "
            "WHERE cache_type = %s", key) as same_conn, monkeypatch.context() as m:
        m.setattr(avc, "get_conn", same_conn)
        just_before = avc.get_ai_views("US", 1.0, [t])

    assert at_the_instant == {}, "a row whose expiry equals NOW() was served"
    assert set(just_before) == {t}, (
        "control: a row expiring 1us after NOW() inside the same transaction was not "
        "served -- the query did not run in that transaction, so the boundary above "
        "was not measured"
    )


def test_expiry_is_the_markets_next_close_reset(tag, monkeypatch):
    """만료 시각은 저장 시점의 `next_close_reset(시장)` 그대로다. 여기서 다시
    계산하면 DB·최적화·테스트가 서로 다른 경계를 본다."""
    from backend.services import market_calendar

    real = market_calendar.next_close_reset
    seen: list[tuple[str, datetime]] = []

    def recording(market, *a, **kw):
        value = real(market, *a, **kw)
        seen.append((market, value))
        return value

    monkeypatch.setattr(market_calendar, "next_close_reset", recording)
    t = f"{tag}EXP"
    avc.save_ai_views("us", 2, {t: _view()})
    avc.save_ai_views("KR", 2, {t: _view()})

    assert [m for m, _ in seen] == ["US", "KR"], f"called with {seen}"
    assert _row("US", 24, t)[2] == seen[0][1]
    assert _row("KR", 24, t)[2] == seen[1][1]


# ── 격리 ──────────────────────────────────────────────────────────────────────

def test_markets_keep_separate_rows(tag, caplog):
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    t = f"{tag}SAME"

    assert avc.save_ai_views("US", 1.0, {t: _view(0.30, 0.7, "Bullish", "미국 뷰")}) == 1
    assert avc.get_ai_views("KR", 1.0, [t]) == {}, "a US view was served to Korea"
    assert avc.save_ai_views("KR", 1.0, {t: _view(-0.10, 0.5, "Bearish", "한국 뷰")}) == 1, (
        "the Korean save for the same ticker string did not get its own row"
    )
    assert avc.get_ai_views("US", 1.0, [t])[t]["key_driver"] == "미국 뷰"
    assert avc.get_ai_views("KR", 1.0, [t])[t]["key_driver"] == "한국 뷰"

    caplog.clear()
    assert avc.get_ai_views("JP", 1.0, [t]) == {}, "an unknown market fell back to another market"
    assert avc.save_ai_views("JP", 1.0, {t: _view()}) == 0
    assert len(_warnings(caplog)) == 2, "refusing an unknown market must say so"


def test_horizons_keep_separate_rows(tag, caplog):
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    t, q = f"{tag}HOR", f"{tag}QTR"
    avc.save_ai_views("US", 1.0, {t: _view()})
    avc.save_ai_views("US", 0.25, {q: _view()})

    assert avc.get_ai_views("US", 0.5, [t]) == {}
    assert avc.get_ai_views("US", 2, [t]) == {}
    assert set(avc.get_ai_views("US", 1, [t])) == {t}, "int 1 and float 1.0 are the same horizon"
    assert set(avc.get_ai_views("US", 12 / 12, [t])) == {t}
    assert set(avc.get_ai_views("US", 3 / 12, [q])) == {q}
    assert _warnings(caplog) == []


def test_a_horizon_that_is_not_whole_months_is_not_cached(tag, caplog):
    """0.3년(3.6개월)을 반올림하면 다른 기간을 물은 뷰를 나눠 쓴다. 캐시를 안 쓰고
    말한다."""
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    t = f"{tag}ODD"

    assert avc.save_ai_views("US", 0.3, {t: _view()}) == 0
    assert avc.get_ai_views("US", 0.3, [t]) == {}
    assert len(_warnings(caplog)) == 2
    assert _sql("SELECT count(*) FROM common_cache WHERE left(cache_type, 9) = 'ai_view::' "
                "AND split_part(cache_type, '::', 5) = %s", (t,), fetch=True)[0][0] == 0


# ── 저장 거부 ─────────────────────────────────────────────────────────────────

_BROKEN = {
    "필수키-없음": {"expected_return": 0.1, "confidence": 0.5, "sentiment": "Bullish"},
    "NaN": _view(er=float("nan")),
    "Inf": _view(conf=float("inf")),
    "불리언": _view(er=True),
    "숫자-문자열": _view(er="0.1"),
    "confidence-1초과": _view(conf=1.5),
    "confidence-음수": _view(conf=-0.01),
    "sentiment-허용밖": _view(sent="Positive"),
    "key_driver-빈문자열": _view(er=0.0, conf=0.5, sent="Neutral", drv=""),
    "key_driver-공백만": _view(drv="   "),
    "dict-아님": [0.1, 0.5, "Bullish", "x"],
}


@pytest.mark.parametrize("case", sorted(_BROKEN))
def test_a_broken_view_is_refused_and_the_rest_are_saved(tag, caplog, case):
    """캐시는 다음 초기화까지 모든 사용자에게 나간다. 빈 key_driver 는 AI 가 답하지
    않은 종목의 기본값 모양이라 특히 — 저장하면 "모름" 이 "보합 전망" 이 된다."""
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    bad, ok = f"{tag}BAD", f"{tag}OK"

    written = avc.save_ai_views("KR", 0.25, {bad: _BROKEN[case], ok: _view()})

    assert written == 1
    assert _row("KR", 3, bad) is None, f"{case}: the broken view was stored"
    assert _row("KR", 3, ok) is not None, "the valid view in the same batch was dropped"
    assert any(bad in m for m in _warnings(caplog)), f"{case}: refused without naming it"


@pytest.mark.parametrize("value", [0, -0.35, 0.0, 1.0, 1, "numpy"],
                         ids=["int-0", "음수", "confidence-0", "confidence-1", "int-1", "numpy"])
def test_valid_edges_are_accepted(tag, value):
    """대조군 — 거부 검사가 너무 넓으면 멀쩡한 뷰가 캐시에서 빠지고, 그건 실패가
    아니라 "매번 AI 를 새로 부른다" 로만 보인다."""
    t = f"{tag}EDGE"
    if value == "numpy":
        import numpy as np
        view = _view(er=np.float32(0.25), conf=np.float64(0.5))
    elif value in (-0.35,):
        view = _view(er=value)
    else:
        view = _view(er=0.1, conf=value)

    assert avc.save_ai_views("US", 1.0, {t: view}) == 1
    got = avc.get_ai_views("US", 1.0, [t])[t]
    assert type(got["expected_return"]) is float and type(got["confidence"]) is float


def test_case_only_duplicates_with_different_views_are_both_refused(tag, caplog):
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    t = f"{tag}DUP"

    assert avc.save_ai_views("US", 1.0, {t.lower(): _view(0.1), t: _view(0.2)}) == 0
    assert _row("US", 12, t) is None
    assert _warnings(caplog), "choosing silently between two views is the failure"
    assert avc.save_ai_views("US", 1.0, {t.lower(): _view(0.1), t: _view(0.1)}) == 1


def test_a_corrupt_stored_row_is_not_served(tag, caplog):
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    bad, ok = f"{tag}CORRUPT", f"{tag}FINE"
    avc.save_ai_views("US", 1.0, {ok: _view()})
    _sql("""INSERT INTO common_cache (cache_type, data, updated_at, expires_at)
            VALUES (%s, '{"expected_return": 0.1}'::jsonb, NOW(), NOW() + interval '1 hour')""",
         (avc._cache_key("US", 12, bad),))

    got = avc.get_ai_views("US", 1.0, [bad, ok])

    assert set(got) == {ok}
    assert any(bad in m for m in _warnings(caplog))


# ── 실패는 {} / 0 이되 조용하지 않다 ──────────────────────────────────────────

def test_a_failing_connection_returns_empty_and_warns(tag, caplog, monkeypatch):
    caplog.set_level(logging.WARNING, logger=_LOGGER)

    @contextmanager
    def broken():
        raise RuntimeError("simulated connection failure")
        yield  # noqa

    monkeypatch.setattr(avc, "get_conn", broken)

    assert avc.get_ai_views("US", 1.0, [f"{tag}A"]) == {}
    assert avc.save_ai_views("US", 1.0, {f"{tag}A": _view()}) == 0
    assert len(_warnings(caplog)) == 2


def test_no_database_returns_empty_and_warns_without_touching_a_connection(caplog, monkeypatch):
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    monkeypatch.setattr(avc, "is_available", lambda: False)

    def must_not_be_called():
        raise AssertionError("took a connection with no database")

    monkeypatch.setattr(avc, "get_conn", must_not_be_called)

    assert avc.get_ai_views("US", 1.0, ["ZZNODB"]) == {}
    assert avc.save_ai_views("US", 1.0, {"ZZNODB": _view()}) == 0
    assert len(_warnings(caplog)) == 2


def test_a_failing_expiry_calculation_writes_nothing(tag, caplog, monkeypatch):
    caplog.set_level(logging.WARNING, logger=_LOGGER)

    def broken(*a, **kw):
        raise RuntimeError("calendar broke")

    monkeypatch.setattr("backend.services.market_calendar.next_close_reset", broken)
    t = f"{tag}NOEXP"

    assert avc.save_ai_views("US", 1.0, {t: _view()}) == 0
    assert _row("US", 12, t) is None
    assert _warnings(caplog)


def test_empty_input_is_not_a_failure(caplog):
    """대조군 — 빈 요청에 경고를 내면 경고가 늘 뜨고, 늘 뜨는 경고는 안 읽힌다 (§1.3)."""
    caplog.set_level(logging.WARNING, logger=_LOGGER)

    assert avc.get_ai_views("US", 1.0, []) == {}
    assert avc.save_ai_views("US", 1.0, {}) == 0
    assert _warnings(caplog) == []


def test_a_bare_string_is_not_read_as_a_list_of_tickers(caplog):
    """문자열을 그대로 돌면 글자 하나하나가 티커가 된다 ("A" 는 실제 종목이다)."""
    caplog.set_level(logging.WARNING, logger=_LOGGER)

    assert avc.get_ai_views("US", 1.0, "AAPL") == {}
    assert _warnings(caplog)


# ── 한 주기에 한 번 (첫 저장 우선) ────────────────────────────────────────────

def test_the_first_view_of_a_cycle_wins(tag, fixed_reset):
    """같은 날 사용자마다 뷰가 달라지지 않게 — 사용자 요청 "하루 한 번 갱신"."""
    t = f"{tag}FWW"
    assert avc.save_ai_views("US", 1.0, {t: _view(0.10, 0.5, "Bullish", "먼저 온 뷰")}) == 1
    first = _row("US", 12, t)

    later = avc.save_ai_views("US", 1.0, {t: _view(0.50, 0.9, "Bullish", "나중에 온 뷰")})

    assert later == 0, "a second save in the same cycle was counted as written"
    assert _row("US", 12, t)[:2] == first[:2], "the same cycle's first view was overwritten"

    cached = avc.get_ai_views("US", 1.0, [t])[t]
    assert avc.save_ai_views("US", 1.0, {t: cached}) == 0
    assert avc.get_ai_views("US", 1.0, [t])[t]["cached_at"] == cached["cached_at"], (
        "passing a cached view back re-stamped cached_at"
    )


def test_the_next_cycle_overwrites_but_an_older_cycle_does_not(tag, fixed_reset):
    t = f"{tag}CYC"
    avc.save_ai_views("US", 1.0, {t: _view(drv="오늘 주기")})

    today = fixed_reset.at
    fixed_reset.at = today + timedelta(days=1)
    assert avc.save_ai_views("US", 1.0, {t: _view(drv="다음 주기")}) == 1
    assert avc.get_ai_views("US", 1.0, [t])[t]["key_driver"] == "다음 주기"

    # 초기화 경계에서 시계가 조금 늦은 인스턴스는 옛 주기의 만료를 계산한다.
    fixed_reset.at = today
    assert avc.save_ai_views("US", 1.0, {t: _view(drv="늦은 인스턴스의 옛 주기")}) == 0
    assert avc.get_ai_views("US", 1.0, [t])[t]["key_driver"] == "다음 주기"


def test_a_row_without_expiry_is_replaced(tag, fixed_reset):
    t = f"{tag}NULLEXP"
    avc.save_ai_views("US", 1.0, {t: _view(drv="만료 없는 행")})
    _sql("UPDATE common_cache SET expires_at = NULL WHERE cache_type = %s",
         (avc._cache_key("US", 12, t),))

    assert avc.save_ai_views("US", 1.0, {t: _view(drv="새 뷰")}) == 1
    assert avc.get_ai_views("US", 1.0, [t])[t]["key_driver"] == "새 뷰"


# ── 동시 저장 ─────────────────────────────────────────────────────────────────

def test_overlapping_saves_in_opposite_order_do_not_deadlock(tag, caplog, monkeypatch):
    """두 요청이 같은 종목들을 반대 순서로 잠그면 교착이다. 교착은 예외가 아니라
    경고 + `0` 으로 나와, 겉으로는 "캐시 미스" 와 같다.

    **두 문장의 출발을 SQL 바로 앞에서 맞춘다.** 함수 입구에서 맞추면 500개 뷰
    검사가 GIL 을 나눠 쓰는 동안 한쪽 문장이 먼저 끝나 버려, 정렬을 뺀 변이에서도
    교착이 안 났다(변이로 확인). 문장과 행 순서는 건드리지 않는다.
    """
    import psycopg2.extras

    caplog.set_level(logging.WARNING, logger=_LOGGER)
    tickers = [f"{tag}{i:03d}" for i in range(500)]
    view = _view(drv="동시 저장")
    errors: list[BaseException] = []
    real_execute_values = psycopg2.extras.execute_values
    rounds = {"gate": None}

    def execute_values_together(*args, **kwargs):
        try:
            rounds["gate"].wait(timeout=10)
        except threading.BrokenBarrierError:
            pass                                  # 다른 쪽이 문장까지 못 왔다 — 그대로 간다
        return real_execute_values(*args, **kwargs)

    monkeypatch.setattr(psycopg2.extras, "execute_values", execute_values_together)

    for _ in range(3):
        rounds["gate"] = threading.Barrier(2)

        def save(order):
            try:
                avc.save_ai_views("US", 1.0, {t: view for t in order})
            except BaseException as e:           # 스레드 예외는 여기서 잡아야 보인다
                errors.append(e)

        threads = [threading.Thread(target=save, args=(tickers,)),
                   threading.Thread(target=save, args=(list(reversed(tickers)),))]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=60)
        if any("deadlock" in m for m in _warnings(caplog)):
            break

    assert not errors, errors
    deadlocks = [m for m in _warnings(caplog) if "deadlock" in m]
    assert not deadlocks, deadlocks[0][:300]
    assert len(avc.get_ai_views("US", 1.0, tickers)) == len(tickers)

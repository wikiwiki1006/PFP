"""
backend/tests/conftest.py
─────────────────────────
실DB 에 붙는 테스트의 공용 배관.

이 리포의 테스트는 거의 전부 모킹이다. 그래도 실DB 가 필요한 것이 있다 —
`user_write_lock` 은 PostgreSQL advisory lock 이라 모킹하면 검증이 성립하지
않는다 (가짜 락은 언제나 성공하므로 락이 아예 없어도 통과한다). 창마다 전용
DB 를 둔 것이 그걸 가능하게 하려는 것이다.

실DB 테스트에는 매번 같은 세 가지가 필요한데, 셋 다 빼먹으면 조용히 틀린다.
그래서 파일마다 다시 쓰지 않고 여기 모아 둔다.

  1. 풀 생명주기 — 열고, **끝나면 반드시 원상복구**
  2. 붙은 DB 가드 — 로컬인지, 그리고 남의 DB 가 아닌지
  3. 사용자 행 준비·정리 — holdings·trade_log 가 users(id) 를 FK 로 문다

**autouse 로 만들지 않는다.** 필요한 테스트만 `live_db` / `db_uid` 를 인자로
받아 간다. 전역으로 켜면 나머지 모킹 테스트가 전부 실DB 를 물게 된다.
"""
from __future__ import annotations

import asyncio.proactor_events
import socket
import uuid
from urllib.parse import urlsplit

import pytest

import backend.db as db

# ── 바깥 네트워크를 막는다 ─────────────────────────────────────────────────────
#
# 테스트가 조용히 인터넷을 타면 두 가지가 동시에 나빠진다.
#
# **결과가 비결정적이 된다.** 받아 온 값이 프롬프트나 단언에 섞이면, 같은
# 코드가 날마다 다른 것을 검사한다.
#
# **그리고 다섯 창이 같이 죽는다.** yfinance 는 IP 단위로 레이트리밋을 걸고
# (§7.7), 게이트를 돌릴 때마다 다섯 창이 같이 나가면 한 창이 걸릴 때 전부
# 걸린다.
#
# 이걸 넣게 된 계기가 정확히 그 형태였다. `get_sector_changes` 가 빈 결과를
# 주는지 재는 테스트에서 `get_close_df` 만 막았는데, 그 함수는 그걸 쓰지 않고
# `_get_sector_etf_df_1mo` 를 쓴다. 그래서 **진짜 섹터 등락률을 받아 왔다.**
# 단언이 `== {}` 라 실패로 드러났지, 조금만 느슨했으면 매 실행마다 조용히
# 나가면서 통과했을 것이다.
#
# 막고 재봤더니 전체 스위트가 **바깥으로 18번** 나가고 있었고 그래도 전부
# 통과했다 — 실패가 전부 폴백에 흡수돼서 아무도 몰랐다. 그래서 이 가드는
# 지금 아무 테스트도 깨뜨리지 않으면서 그 부류를 통째로 막는다.
#
# localhost 는 연다. 실DB 테스트가 로컬 도커 postgres 를 쓴다.
#
# ## 파이썬 소켓만 막으면 C 로 나가는 길이 샌다
#
# `socket.socket.connect` 는 파이썬이 여는 연결만 본다. 두 길이 그걸 지나지
# 않는다 — 둘 다 이 가드를 건 채로 실제로 나가는 것을 재현했다:
#
#   curl_cffi   yfinance 1.4 가 쓰는 HTTP 클라이언트. libcurl 이 C 에서 소켓을
#               연다. `yf.Ticker("^VIX").history("5d")` 가 5행을 받아 왔고
#               같은 프로세스의 `requests` 는 막혔다.
#   asyncio     윈도우 기본 루프(Proactor)는 `ConnectEx` 로 연결한다.
#               `asyncio.open_connection` 이 OutboundBlocked 대신 타임아웃이
#               됐다. (리눅스 셀렉터 루프는 `sock.connect` 를 불러 원래 막힌다.)
#
# 그래서 셋을 건다. curl 은 요청 URL 을 넣는 `Curl.setopt(CurlOpt.URL)` —
# 동기·비동기·웹소켓이 전부 이 한 곳을 지난다. asyncio 는 `sock_connect`.
#
# **이 가드가 안 덮는 것**: 다른 C 확장이 여는 연결(grpc 코어 등 — 지금
# 백엔드는 쓰지 않는다), 그리고 curl 에서 URL 은 로컬인데 프록시 옵션으로
# 바깥에 붙는 경우.
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0", ""}


class OutboundBlocked(RuntimeError):
    """테스트가 바깥으로 연결을 시도했다."""


# 막은 시도를 **어느 테스트가** 했는지 남기고, 그 테스트를 실패시킨다.
#
# 막기만 하면 부족하다. 호출한 코드가 예외를 폴백으로 삼키면 테스트는 초록이고
# "나가려 했다" 는 사실은 어디에도 안 남는다 — 위의 18번이 그렇게 숨어 있었다.
# curl 가드를 걸고 재 보니 게이트마다 야후로 3번 나가고 있었다 (KRX 관측
# 캘린더). 기대고 있던 테스트는 둘이다: 하나는 `in (True, False)` 라는 늘 참인
# 단언이라 통과했고, 다른 하나는 **앞 테스트가 받아 온 캘린더를 프로세스 메모로
# 물려받아** 통과했다 — 혼자 돌리면 그 자신이 나갔고, 오프라인이면 실패했다.
# 결과가 네트워크 상태와 실행 순서에 달려 있었다.
#
# 그래서 테스트 안에서 막힌 시도는 그 테스트의 **teardown 오류**가 된다.
# 가드를 일부러 건드리는 검사는 `refused_outbound` 로 시도를 받아 간다.
# 어느 테스트의 검사 구간에도 들지 않은 시도(수집 중·테스트 사이·모듈 범위
# 픽스처 준비)는 세션을 실패로 만든다.
#
# 한계: 테스트가 끝난 뒤에도 도는 백그라운드 스레드의 시도는 그다음 테스트에
# 붙는다. 그런 오류가 나면 앞 테스트가 띄운 스레드부터 본다. 그리고 프로세스
# 메모가 있는 경로는 **처음 부른 테스트만** 나간다 — 오류는 그 테스트에 뜬다.
_ATTEMPTS: list[tuple[str, str, str]] = []          # (테스트, 경로, 호스트)
_CLAIMED: set[int] = set()                           # 일부러 낸 시도
_OWNED: set[int] = set()                             # 어느 테스트의 검사 구간에 든 시도
_OUTSIDE_TESTS = ("(수집 중)", "(테스트 사이)")
_RUNNING = [_OUTSIDE_TESTS[0]]


def _refuse(via: str, host: str) -> OutboundBlocked:
    _ATTEMPTS.append((_RUNNING[0], via, host))
    return OutboundBlocked(
        f"a test tried to reach {host} via {via}. Tests must not use the network: "
        "the result stops being deterministic, and five windows running "
        "the gate together will trip the same rate limit. Mock the entry "
        "point this code path actually uses -- note that two functions "
        "with similar names often do not share one. "
        "(테스트가 바깥으로 나갔다.)"
    )


def _host_of(address) -> str:
    if isinstance(address, tuple) and address:
        return str(address[0])
    return str(address)


_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex
_real_proactor_sock_connect = asyncio.proactor_events.BaseProactorEventLoop.sock_connect


def _guard(self, address, *args, **kwargs):
    host = _host_of(address)
    if host not in _LOCAL_HOSTS:
        raise _refuse("socket", host)
    return _real_connect(self, address, *args, **kwargs)


def _guard_ex(self, address, *args, **kwargs):
    host = _host_of(address)
    if host not in _LOCAL_HOSTS:
        raise _refuse("socket", host)
    return _real_connect_ex(self, address, *args, **kwargs)


async def _guard_proactor(self, sock, address):
    host = _host_of(address)
    if host not in _LOCAL_HOSTS:
        raise _refuse("asyncio", host)
    return await _real_proactor_sock_connect(self, sock, address)


def _curl_url_host(value) -> str:
    raw = value.decode("utf-8", "replace") if isinstance(value, (bytes, bytearray)) else str(value)
    try:
        return (urlsplit(raw).hostname or "").lower()
    except ValueError:
        return ""


_curl_restore = None


def _install_curl_guard() -> None:
    """curl_cffi 가 없으면 걸 곳도 새는 길도 없다."""
    global _curl_restore
    try:
        from curl_cffi.curl import Curl, CurlOpt
    except ImportError:
        return
    real_setopt = Curl.setopt

    def guarded_setopt(self, option, value):
        if option == CurlOpt.URL:
            host = _curl_url_host(value)
            # URL 에서 호스트를 못 읽으면 로컬로 치지 않는다 — 소켓 쪽의 "" 는
            # '모든 인터페이스' 지만 URL 에서는 '모름' 이다.
            if not host or host not in _LOCAL_HOSTS:
                raise _refuse("curl_cffi", host or repr(value)[:80])
        return real_setopt(self, option, value)

    Curl.setopt = guarded_setopt

    def restore():
        Curl.setopt = real_setopt

    _curl_restore = restore


def pytest_configure(config):
    """**수집 전에** 건다.

    autouse 픽스처로 하면 수집이 끝난 뒤에야 걸린다. 그런데 모듈 수준에서
    무언가를 만드는 테스트 파일이 있으면(이 리포의 프롬프트 코퍼스가 그렇다)
    그 호출은 import 시점, 즉 **픽스처보다 먼저** 일어난다. 실제로 그 코퍼스가
    수집할 때마다 네이버에 붙고 있었고, 픽스처 방식으로는 그게 안 잡혔다.
    """
    socket.socket.connect = _guard
    socket.socket.connect_ex = _guard_ex
    asyncio.proactor_events.BaseProactorEventLoop.sock_connect = _guard_proactor
    _install_curl_guard()


def pytest_unconfigure(config):
    socket.socket.connect = _real_connect
    socket.socket.connect_ex = _real_connect_ex
    asyncio.proactor_events.BaseProactorEventLoop.sock_connect = _real_proactor_sock_connect
    if _curl_restore is not None:
        _curl_restore()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    _RUNNING[0] = item.nodeid
    yield
    _RUNNING[0] = _OUTSIDE_TESTS[1]


@pytest.fixture
def refused_outbound():
    """가드를 **일부러** 건드리는 검사용. 부르면 이 테스트에서 막힌 시도를
    `[(경로, 호스트)]` 로 돌려주고, 그것들을 의도한 것으로 표시한다.

    부르지 않으면 그 시도는 아래 자동 검사에 걸린다 — 가드 검사도 자기가 무엇을
    막았는지 확인하게 하려는 것이다.
    """
    start = len(_ATTEMPTS)

    def seen() -> list[tuple[str, str]]:
        end = len(_ATTEMPTS)
        _CLAIMED.update(range(start, end))
        return [(via, host) for _, via, host in _ATTEMPTS[start:end]]

    return seen


@pytest.fixture(autouse=True)
def _outbound_attempt_fails_the_test():
    start = len(_ATTEMPTS)
    yield
    end = len(_ATTEMPTS)
    _OWNED.update(range(start, end))
    stray = [(via, host) for i, (_, via, host) in enumerate(_ATTEMPTS[start:end], start)
             if i not in _CLAIMED]
    if stray:
        pytest.fail(
            f"this test tried to reach the network and was refused: {sorted(set(stray))}. "
            "The code under test may have swallowed the refusal, in which case the test "
            "passed while measuring its offline fallback -- and outside the guard its "
            "result depended on the network. Mock the entry point the code actually uses. "
            "(테스트가 바깥으로 나가려 했다 — 막혔지만 삼켜졌을 수 있다)",
            pytrace=False,
        )


def _unowned() -> list[int]:
    return [i for i in range(len(_ATTEMPTS)) if i not in _CLAIMED and i not in _OWNED]


def pytest_sessionfinish(session, exitstatus):
    if _unowned() and session.exitstatus == 0:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """의도하지 않은 시도만 적는다. 가드 검사가 일부러 낸 시도까지 매번 찍으면
    게이트 출력에 늘 뜨는 줄이 되고, 늘 뜨는 줄은 곧 아무도 안 읽는다."""
    unowned = set(_unowned())
    by_test: dict[str, list[str]] = {}
    for i, (test, via, host) in enumerate(_ATTEMPTS):
        if i not in _CLAIMED:
            mark = " (no test owns this)" if i in unowned else ""
            by_test.setdefault(test, []).append(f"{via}:{host}{mark}")
    if not by_test:
        return
    terminalreporter.section("outbound attempts refused by the network guard")
    for test, hits in by_test.items():
        terminalreporter.line(f"{test}  x{len(hits)}  {sorted(set(hits))}")
    if unowned:
        terminalreporter.line(
            "attempts marked 'no test owns this' happened at import/collection, in a "
            "module- or session-scoped fixture, or in a leftover thread -- the session is "
            "marked failed because no single test reported them.", red=True)

# 원격이면 아무것도 하지 않는다. Neon 에는 실사용자 데이터가 있고
# (users 12 · holdings 30 · reports 46) 역할 창이 붙을 곳이 아니다.
_ALLOWED_HOSTS = {"localhost", "127.0.0.1", "::1", "", None}


@pytest.fixture(scope="module")
def live_db():
    """실제 연결 풀. 붙지 못하면 fail 이고, 끝나면 풀을 원래 상태로 되돌린다.

    **skip 이 아니라 fail 인 이유**: `user_write_lock` 은
    `if not is_available(): yield; return` 이라 DB 가 없으면 락 없이 그냥
    통과한다. 그 상태의 초록불은 "락이 동작한다" 가 아니라 "락을 재보지도
    못했다" 는 뜻인데, skip 은 CI 에서 조용히 사라져 그 구분이 안 남는다.

    **모듈 스코프이고 반드시 되돌리는 이유**: 나머지 테스트는 전부 모킹이고
    그중 일부는 `is_available()` 이 False 인 것에 기대어 동작한다. 풀을 열어둔
    채 나가면 뒤따르는 파일이 통째로 깨진다 (실제로 그랬다 — test_job_cancel
    2건). 세션 스코프로 올리면 세션 끝까지 열려 있어 같은 사고가 난다.
    """
    previous = db._pool
    if previous is None and not db.init_pool(minconn=2, maxconn=10):
        pytest.fail(
            "cannot reach the database -- this test measures a real advisory "
            "lock and has no mocked equivalent. "
            "(로컬 도커 postgres(5433)를 띄우고 backend/.env 의 DB_NAME 이 "
            "이 워크트리 전용 DB 인지 확인하라.)"
        )
    try:
        target = _assert_safe_target()
        _ensure_schema(target)
        yield target
    finally:
        if previous is None:
            db.close_pool()          # 우리가 연 것만 닫는다
        db._pool = previous


_schema_applied: set[str] = set()


def _ensure_schema(dbname: str) -> None:
    """이 창 DB 에 앱 스키마를 맞춘다 — **가드를 통과한 뒤에만.**

    테스트는 이미 앱 스키마를 전제하고 있다. `save_report` 를 부르는 검사는
    그 인덱스가 있다고 가정한다. 여기서 맞추는 것은 새 권한을 주는 게 아니라
    **이미 하고 있던 가정을 명시**하는 것이다. `init_schema()` 는 앱이 기동할
    때마다 스스로 돌리는 바로 그 DDL 이고 멱등이다.

    이게 없을 때 무슨 일이 나는지는 실측했다: 창 DB 가 옛 스키마면
    `save_report` 가 **전부** 실패하고(`no unique or exclusion constraint
    matching the ON CONFLICT specification`), 증상은 "내 테스트가 이상하게
    깨진다" 로 나온다. 같은 데 세 창이 걸렸고 수동 절차는 세 번 다 실패했다.

    **진짜 위험은 "테스트가 DDL 을 돈다" 가 아니라 "그 DDL 이 엉뚱한 DB 에
    간다" 이다.** `postgres` 템플릿에 걸면 이후 만들어지는 모든 창이
    물려받고, Neon 은 실데이터다. 그래서 `_assert_safe_target()` 를 통과한
    뒤에만 부른다 — 호스트가 로컬이고 이름이 `pfp_*` 이며 `postgres` 가
    아닐 때만.

    실패하면 **조용히 건너뛰지 않는다.** 건너뛰면 "스키마가 안 맞는데
    테스트는 돈다" 가 되어 이 함수가 없던 상태로 되돌아간다.
    """
    if dbname in _schema_applied:
        return
    from backend.db import schema

    # `init_schema()` 를 부르지 않고 같은 DDL 을 직접 돌린다. 그 함수는 자기
    # 실패를 삼키고(`except ... logger.error`) 아무것도 돌려주지 않아서,
    # 불러 봐야 **적용됐는지 알 수 없다.** 여기서 조용히 넘어가면 이 함수가
    # 없던 상태와 같아진다 — 그게 막으려는 것이다.
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(schema._DDL)
    except Exception as e:
        pytest.fail(
            f"could not apply the app schema to '{dbname}': {e} -- tests assume "
            "the same DDL the app runs at startup, and running them against an "
            "older schema fails in ways that look like broken tests "
            "(e.g. 'no unique or exclusion constraint matching the ON CONFLICT "
            "specification'). (창 DB 에 스키마를 못 맞췄다.)"
        )
    _schema_applied.add(dbname)


def _assert_safe_target() -> str:
    """붙은 DB 가 이 창의 전용 DB 가 맞는지 확인한다. 아니면 즉시 멈춘다.

    `postgres` 거부가 특히 중요하다. 그건 다섯 창이 복제해 온 **원본 템플릿**이라
    테스트 행이 섞이면 이후 만들어지는 모든 창이 그걸 물려받는다. 실제로 한 번
    걸렸다 — 병합 후 메인에서 게이트를 돌렸더니 `backend/.env` 가 아직
    `DB_NAME=postgres` 였고, 이 가드가 없었으면 조용히 오염됐을 것이다.
    """
    with db.get_conn() as conn:
        # 서버가 스스로 말하는 주소(inet_server_addr)를 보면 안 된다 — 도커 포트
        # 매핑을 거치면 컨테이너 내부 IP(172.17.x.x)가 나와서 로컬인데도 원격으로
        # 읽힌다. 우리가 실제로 다이얼한 클라이언트 쪽 호스트를 본다.
        host = (conn.info.host or "").lower()
        with conn.cursor() as cur:
            cur.execute("SELECT current_database()")
            dbname = cur.fetchone()[0]

    if host not in _ALLOWED_HOSTS:
        pytest.fail(
            f"connected to a remote database (host={host}) -- local only. "
            "(원격 DB 에 붙었다.)"
        )
    if dbname == "postgres":
        pytest.fail(
            "connected to 'postgres', the template every window was cloned from -- "
            "test rows here are inherited by every window created later. "
            "Point backend/.env DB_NAME at this worktree's own database. "
            "(원본 템플릿이다. 여기에 쓰면 격리가 무의미해진다.)"
        )
    if not dbname.startswith("pfp_"):
        pytest.fail(
            f"connected to '{dbname}', which is not one of this project's "
            "per-window databases (pfp_*). Check backend/.env DB_NAME."
        )
    return dbname


@pytest.fixture
def db_uid(live_db):
    """테스트마다 새 사용자. 남의 행을 건드리지 않고, 끝나면 자기 행만 지운다.

    holdings·trade_log·reports 가 users(id) 를 FK 로 물고 있어 사용자 행이 먼저
    있어야 한다. 지울 때는 ON DELETE CASCADE 가 딸린 행까지 걷어가므로 사용자
    한 줄만 지우면 된다.
    """
    uid = f"pytest-{uuid.uuid4().hex[:12]}"
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users(id, name, email) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                (uid, "pytest", f"{uid}@example.invalid"),
            )
    yield uid
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE id=%s", (uid,))

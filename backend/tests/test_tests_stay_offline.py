"""
테스트는 바깥 네트워크를 타지 않는다 — 그리고 그 가드가 실제로 걸려 있다.

가드는 `conftest.pytest_configure` 에 있다. 이 파일은 **그 가드가 살아 있는지**
를 잰다. 가드가 조용히 풀리면 스위트는 그대로 통과하면서 다시 인터넷을
타기 시작하고, 그건 출력에서 통과와 구별되지 않는다 — 오늘 내내 경계한 그
형태다.

## 이 검사가 무엇을 막는가

**비결정성.** 받아 온 값이 프롬프트나 단언에 섞이면 같은 코드가 날마다 다른
것을 검사한다.

**다섯 창의 동시 사망.** yfinance 는 IP 단위로 레이트리밋을 걸고(§7.7),
게이트를 돌릴 때마다 다섯 창이 같이 나가면 한 창이 걸릴 때 전부 걸린다.

**그리고 느린 게이트.** 가드를 넣자 전체 스위트가 25초에서 12초로 줄었다.
절반이 응답 없는 바깥 호출을 기다리는 시간이었다.

## 왜 픽스처가 아니라 훅인가

`pytest_configure` 는 **수집 전에** 돈다. autouse 픽스처는 수집이 끝난 뒤에야
걸리는데, 모듈 수준에서 무언가를 만드는 테스트 파일이 있으면(프롬프트 코퍼스가
그렇다) 그 호출은 import 시점에 일어난다. 픽스처로 했을 때 그 경로가 그대로
새고 있었다.

## 소켓만 재면 안 된다

파이썬 소켓 가드가 살아 있는 채로 yfinance 가 야후에서 5행을 받아 왔다.
yfinance 1.4 는 curl_cffi(libcurl) 로 연결해 `socket.connect` 를 지나지 않는다.
윈도우의 asyncio 루프도 `ConnectEx` 로 우회한다. 그래서 경로마다 따로 잰다 —
소켓이 막힌다는 사실은 나머지 둘에 대해 아무것도 말하지 않는다.

주소는 `192.0.2.1`(RFC 5737 문서용, 라우팅되지 않음)을 쓴다. 가드가 풀린 변이
에서도 실제 호스트에 닿지 않고 타임아웃으로 끝난다.
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import re
import socket
import subprocess
import sys

import pytest

from backend.tests.conftest import OutboundBlocked

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
_UNROUTABLE = "192.0.2.1"


# ── 파이썬 소켓 ───────────────────────────────────────────────────────────────

def test_an_outbound_connection_is_refused(refused_outbound):
    """바깥으로 나가려 하면 막힌다.

    가드가 풀리면 이 테스트가 **연결을 시도하다 다른 예외로 실패**하거나,
    최악의 경우 정말로 연결돼 통과한다. 어느 쪽이든 초록불은 아니다.
    """
    s = socket.socket()
    s.settimeout(1.0)
    try:
        with pytest.raises(OutboundBlocked):
            s.connect(("93.184.216.34", 80))      # example.com, 고정 IP
    finally:
        s.close()
    assert refused_outbound() == [("socket", "93.184.216.34")]


def test_a_hostname_is_refused_before_it_is_resolved(refused_outbound):
    """호스트 이름으로 나가는 것도 막힌다 — IP 만 막으면 대부분이 샌다."""
    s = socket.socket()
    s.settimeout(1.0)
    try:
        with pytest.raises(OutboundBlocked):
            s.connect(("finance.naver.com", 80))
    finally:
        s.close()
    assert refused_outbound() == [("socket", "finance.naver.com")]


def test_localhost_is_still_open(refused_outbound):
    """로컬은 열려 있어야 한다 — 실DB 테스트가 로컬 도커 postgres 를 쓴다.

    이게 없으면 "전부 막는다" 는 구현으로도 위 둘이 통과하는데, 그건
    네트워크를 막은 게 아니라 실DB 테스트를 죽인 것이다.
    """
    s = socket.socket()
    s.settimeout(0.5)
    try:
        # 연결 성공 여부는 상관없다. **가드가 거부하지 않는 것**만 본다.
        with pytest.raises(OSError) as exc:
            s.connect(("127.0.0.1", 1))           # 아무도 안 듣는 포트
        assert not isinstance(exc.value, OutboundBlocked), (
            "localhost was blocked -- the live-database fixtures cannot reach "
            "the local docker postgres. (로컬까지 막으면 실DB 테스트가 죽는다.)"
        )
    finally:
        s.close()
    assert refused_outbound() == []


# ── curl_cffi (yfinance) ──────────────────────────────────────────────────────

def test_curl_cffi_is_refused(refused_outbound):
    from curl_cffi import requests as curl_requests

    with pytest.raises(OutboundBlocked):
        curl_requests.get(f"http://{_UNROUTABLE}:9/", timeout=1)
    assert refused_outbound() == [("curl_cffi", _UNROUTABLE)], (
        "curl_cffi opened its own connection -- libcurl does not go through "
        "socket.connect, so only the Curl.setopt(URL) guard can stop it."
    )


def test_yfinance_does_not_reach_yahoo(refused_outbound):
    """실제로 샌 그 길 그대로 — yfinance 로 야후 시세를 달라고 한다.

    yfinance 는 요청 실패를 삼키고 빈 프레임을 준다. 그래서 "비었다" 만으로는
    막혔는지 네트워크가 없었는지 구별이 안 된다 — 가드가 **야후 호스트를
    curl 경로에서** 막았다는 기록까지 본다.
    """
    import yfinance as yf

    try:
        frame = yf.Ticker("^VIX").history(period="5d")
    except OutboundBlocked:
        frame = None
    seen = refused_outbound()

    assert frame is None or frame.empty, (
        f"yfinance returned {len(frame)} rows of ^VIX -- it reached Yahoo through curl_cffi "
        "while the socket guard was up. (yfinance 가 야후에 닿았다)"
    )
    assert any(via == "curl_cffi" and host.endswith("yahoo.com") for via, host in seen), (
        f"no refused curl request to Yahoo was recorded ({seen}) -- the empty frame "
        "came from somewhere other than the guard."
    )


def test_curl_cffi_to_localhost_is_not_refused(refused_outbound):
    from curl_cffi import requests as curl_requests
    from curl_cffi.requests.exceptions import RequestException

    with pytest.raises(RequestException):             # 거부·타임아웃 — 가드가 아니다
        curl_requests.get("http://127.0.0.1:1/", timeout=0.3)
    assert refused_outbound() == []


# ── asyncio ───────────────────────────────────────────────────────────────────

async def _open(host: str, port: int, timeout: float):
    return await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)


def test_asyncio_connection_is_refused(refused_outbound):
    """윈도우 Proactor 루프는 `ConnectEx` 로 연결해 소켓 가드를 우회했다.
    리눅스 셀렉터 루프는 `sock.connect` 를 불러 소켓 가드에 걸린다 — 어느
    경로로 막혔든 막힌 것이다."""
    with pytest.raises(OutboundBlocked):
        asyncio.run(_open(_UNROUTABLE, 80, timeout=1.5))
    seen = refused_outbound()
    assert seen and all(host == _UNROUTABLE for _, host in seen), seen
    assert {via for via, _ in seen} <= {"asyncio", "socket"}, seen


def test_asyncio_to_localhost_is_not_refused(refused_outbound):
    # 윈도우는 닫힌 로컬 포트에도 1초 넘게 재시도한다 — 거부든 타임아웃이든 OSError 다.
    with pytest.raises(OSError) as exc:
        asyncio.run(_open("127.0.0.1", 1, timeout=0.3))
    assert not isinstance(exc.value, OutboundBlocked)
    assert refused_outbound() == []


# ── 막힌 시도는 삼켜져도 테스트를 실패시킨다 ────────────────────────────────────

_PROBE = f'''
import socket

def test_swallowed_attempt():
    try:
        socket.create_connection(("{_UNROUTABLE}", 9), timeout=1)
    except Exception:
        pass                      # 폴백이 삼킨 것처럼

def test_claimed_attempt(refused_outbound):
    try:
        socket.create_connection(("{_UNROUTABLE}", 9), timeout=1)
    except Exception:
        pass
    assert refused_outbound() == [("socket", "{_UNROUTABLE}")]

def test_offline_test():
    assert True
'''


def _inner_pytest(tmp_path, source: str) -> subprocess.CompletedProcess:
    probe = tmp_path / "test_probe.py"
    probe.write_text(source, encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8")
    return subprocess.run(
        [sys.executable, "-m", "pytest", str(probe), "-p", "backend.tests.conftest",
         "-p", "no:cacheprovider", "-q", "-rA"],
        cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=120,
    )


_PROBE_AT_IMPORT = f'''
import socket

try:                                   # 코퍼스처럼 모듈 수준에서 무언가를 만든다
    socket.create_connection(("{_UNROUTABLE}", 9), timeout=1)
except Exception:
    pass

def test_offline_test():
    assert True
'''


_PROBE_IN_MODULE_FIXTURE = f'''
import socket
import pytest

@pytest.fixture(scope="module")
def warmed_up():                       # 테스트별 검사가 시작되기 **전에** 준비된다
    try:
        socket.create_connection(("{_UNROUTABLE}", 9), timeout=1)
    except Exception:
        pass
    yield

def test_offline_test(warmed_up):
    assert True
'''


@pytest.mark.parametrize("probe", [_PROBE_AT_IMPORT, _PROBE_IN_MODULE_FIXTURE],
                         ids=["수집-중", "모듈-범위-픽스처"])
def test_a_refusal_outside_any_test_fails_the_session(tmp_path, probe):
    """어느 테스트의 검사 구간에도 들지 않은 시도 — 수집(import) 중이거나, 테스트별
    검사보다 먼저 준비되는 모듈 범위 픽스처 안. 프롬프트 코퍼스가 import 시점에
    네이버로 나갔었다. 테스트가 전부 통과해도 세션이 실패해야 한다.

    모듈 범위 픽스처 쪽은 처음 구현이 놓쳤다 — '수집 중' 이라는 이름표만 봤는데,
    그 시도에는 첫 테스트의 이름표가 붙는다. 이 사례를 넣고 나서 고쳤다.
    """
    proc = _inner_pytest(tmp_path, probe)
    out = proc.stdout + proc.stderr

    assert re.search(r"PASSED .*test_offline_test", out), out[-2000:]
    assert proc.returncode != 0, (
        f"an attempt at import time left the session green (exit {proc.returncode}):\n"
        f"{out[-2000:]}"
    )
    assert _UNROUTABLE in out, out[-2000:]


def test_a_swallowed_refusal_still_fails_its_test(tmp_path):
    """가드가 막아도 코드가 예외를 삼키면 테스트는 초록이다. 그 상태로는 두 테스트가
    게이트마다 야후로 나가면서 아무도 몰랐다. 삼킨 시도가 그 테스트의 오류가
    되는지, 일부러 낸 시도(`refused_outbound`)와 무관한 테스트는 그대로인지 —
    이 conftest 를 플러그인으로 얹은 **별도 pytest 프로세스**에서 잰다.
    """
    proc = _inner_pytest(tmp_path, _PROBE)
    out = proc.stdout + proc.stderr

    assert proc.returncode != 0, f"the inner session passed:\n{out[-2000:]}"
    assert re.search(r"ERROR .*test_swallowed_attempt", out), (
        f"the swallowed attempt did not fail its test:\n{out[-2000:]}"
    )
    for name in ("test_claimed_attempt", "test_offline_test"):
        assert re.search(rf"PASSED .*{name}", out) and not re.search(rf"ERROR .*{name}", out), (
            f"{name} should pass cleanly -- a claimed attempt or an offline test was "
            f"treated as a failure:\n{out[-2000:]}"
        )
    assert _UNROUTABLE in out, out[-2000:]

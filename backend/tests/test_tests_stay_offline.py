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
"""
from __future__ import annotations

import socket

import pytest

from backend.tests.conftest import OutboundBlocked


def test_an_outbound_connection_is_refused():
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


def test_a_hostname_is_refused_before_it_is_resolved():
    """호스트 이름으로 나가는 것도 막힌다 — IP 만 막으면 대부분이 샌다."""
    s = socket.socket()
    s.settimeout(1.0)
    try:
        with pytest.raises(OutboundBlocked):
            s.connect(("finance.naver.com", 80))
    finally:
        s.close()


def test_localhost_is_still_open():
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

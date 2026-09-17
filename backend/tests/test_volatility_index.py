"""
`market_data.volatility_index` — 시장별 변동성 지수 (40e2a94 · f547868).

한국 `/metrics` 가 이 함수를 동기로 탄다. 출처(인베스팅)가 멈추면 예전에는 동시에
들어온 요청이 **각자** 출처를 두드리고 **각자** 기다렸다 (통합 실측: 동시 10요청 →
호출 10회, 전부 대기). 지표 한 칸 때문에 포트폴리오 화면 전체가 늦어진 것이다.

지키는 성질:

    한 번에 하나만 가져온다       — 잠금을 못 얻은 요청은 기다리지 않는다
    가져오는 중·실패일 때         — 1시간 이내 마지막 성공값을 준다 (같은 순간 요청은
                                    같은 값). 없으면 None
    다른 시장 값으로 메우지 않는다 — 한국에 미국 VIX 가 있어도 한국은 None
    출처 대기에 상한이 있다        — requests 타임아웃 (연결, 읽기)

## 시간을 재지 않고 순서를 만든다

"기다리지 않는다" 를 `sleep` 과 경과시간으로 재면 느린 머신에서 흔들린다. 대신
가로챈 출처가 **이벤트를 기다리게** 한다: 첫 요청이 출처 안에 들어간 것을 확인한
뒤 나머지를 보내고, 그 요청들이 **출처가 아직 막혀 있는 동안** 끝났는지 본다.

DB 공용 캐시(`get_common`·`save_common`)는 대역이다 — 이 창 DB 에 남은 값이
결과를 바꾸지 않게.
"""
from __future__ import annotations

import logging
import threading
import time

import pytest
import requests

from backend.services import market_data as md

_KR_KEY = "volatility_index:KR"
_US_KEY = "volatility_index:US"
_PAYLOAD = {"data": [
    {"last_close": "43.02", "rowDateTimestamp": "2026-09-17T00:00:00Z", "rowDateRaw": 1758067200},
    {"last_close": "44.40", "rowDateTimestamp": "2026-09-16T00:00:00Z", "rowDateRaw": 1757980800},
]}
_US_VIX = {"value": 15.66, "prev_close": 17.71, "change_pct": -11.58, "as_of": "2026-09-17",
           "label": "VIX", "source": "yahoo"}


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


@pytest.fixture
def state(monkeypatch):
    """모듈 상태를 비우고, DB 캐시를 대역으로, 출처를 가로챈다."""
    monkeypatch.setattr(md, "_vol_memo", {})
    monkeypatch.setattr(md, "_vol_last_good", {})
    monkeypatch.setattr(md, "_vol_locks", {})
    saved: list[tuple[str, dict]] = []
    monkeypatch.setattr("backend.db.market_cache.get_common", lambda key, *a, **kw: None)
    monkeypatch.setattr("backend.db.market_cache.save_common",
                        lambda key, value, *a, **kw: saved.append((key, value)))

    class Source:
        calls: list[dict] = []
        behave = staticmethod(lambda: _Response(_PAYLOAD))
        saved_rows = saved

    def fake_get(url, *args, **kwargs):
        assert "investing.com" in url, f"unexpected HTTP call: {url}"
        Source.calls.append(kwargs)
        return Source.behave()

    Source.calls = []
    monkeypatch.setattr(requests, "get", fake_get)
    return Source


def _stalled(source, outcome):
    """출처가 불리면 `entered` 를 세우고 `release` 까지 멈춘 뒤 `outcome()` 을 낸다."""
    entered, release = threading.Event(), threading.Event()

    def behave():
        entered.set()
        assert release.wait(20), "the test never released the stalled source"
        return outcome()

    source.behave = behave
    return entered, release


def _fail():
    raise requests.exceptions.ReadTimeout("simulated stall")


# ── 한 번에 하나만, 나머지는 기다리지 않는다 ──────────────────────────────────

@pytest.mark.parametrize("last_good_age, others_get", [
    (None, None),
    (30 * 60, 43.02),
    (61 * 60, None),
], ids=["기억값-없음", "기억값-30분", "기억값-61분"])
def test_concurrent_requests_fetch_once_and_do_not_wait(state, monkeypatch, last_good_age, others_get):
    # 미국 값은 언제나 있다 — 한국이 그걸로 메우지 않는지도 같이 본다.
    md._vol_last_good[_US_KEY] = (time.time(), _US_VIX)
    md._vol_memo[_US_KEY] = (time.time() + 600, _US_VIX)
    if last_good_age is not None:
        md._vol_last_good[_KR_KEY] = (time.time() - last_good_age, {**_US_VIX, "value": 43.02,
                                                                   "label": "VKOSPI", "source": "investing"})
    entered, release = _stalled(state, _fail)
    results: dict[int, dict] = {}

    def request(i):
        results[i] = md.volatility_index("KR")

    holder = threading.Thread(target=request, args=(0,))
    holder.start()
    assert entered.wait(10), "the first request never reached the source"

    others = [threading.Thread(target=request, args=(i,)) for i in range(1, 10)]
    for t in others:
        t.start()
    # 마감 하나를 같이 쓴다 — 스레드마다 따로 기다리면 회귀가 났을 때 9배로 늦게 빨개진다.
    deadline = time.monotonic() + 5
    for t in others:
        t.join(timeout=max(0.0, deadline - time.monotonic()))
    still_waiting = sum(t.is_alive() for t in others)
    release.set()
    holder.join(timeout=20)

    assert still_waiting == 0, (
        f"{still_waiting}/9 requests were still waiting while the source was stalled -- "
        "a slow VKOSPI source holds every Korean /metrics request. (다른 요청이 기다렸다)"
    )
    assert len(state.calls) == 1, f"the source was called {len(state.calls)} times for 10 requests"
    values = {results[i]["value"] for i in range(1, 10)}
    assert values == {others_get}, (
        f"requests that did not fetch got {values}, expected {others_get} "
        f"(last good value age {last_good_age}s; the US VIX {_US_VIX['value']} must never "
        "stand in for Korea)"
    )
    assert all(results[i]["label"] == "VKOSPI" for i in results)
    assert results[0]["value"] == others_get, "the fetching request disagrees with the others"


# ── 실패와 기억값 ─────────────────────────────────────────────────────────────

def test_a_failed_fetch_serves_the_last_good_value_and_says_so(state, caplog):
    md._vol_last_good[_KR_KEY] = (time.time() - 1200, {"value": 43.02, "label": "VKOSPI"})
    state.behave = _fail
    caplog.set_level(logging.WARNING, logger=md._logger.name)

    got = md.volatility_index("KR")
    again = md.volatility_index("KR")

    assert got["value"] == 43.02 and again["value"] == 43.02
    assert len(state.calls) == 1, "a failure was not remembered briefly -- every request hits the dead source"
    assert any("마지막 성공값" in r.getMessage() for r in caplog.records), (
        "serving a stale value left no trace"
    )


def test_a_failed_fetch_with_nothing_remembered_is_empty_not_another_market(state, caplog):
    md._vol_last_good[_US_KEY] = (time.time(), _US_VIX)
    md._vol_memo[_US_KEY] = (time.time() + 600, _US_VIX)
    state.behave = _fail
    caplog.set_level(logging.WARNING, logger=md._logger.name)

    got = md.volatility_index("KR")

    assert got["value"] is None and got["label"] == "VKOSPI", got
    assert caplog.records, "an empty volatility cell left no trace"
    assert state.saved_rows == [], "a failed lookup was written to the shared cache"


def test_a_successful_fetch_is_parsed_shared_and_remembered(state):
    got = md.volatility_index("KR")
    again = md.volatility_index("KR")

    assert (got["value"], got["prev_close"], got["change_pct"]) == (43.02, 44.40, -3.11)
    assert got["label"] == "VKOSPI" and again == got
    assert len(state.calls) == 1
    assert [k for k, _ in state.saved_rows] == [_KR_KEY]


def test_the_source_request_has_a_short_timeout(state):
    """(연결, 읽기) 모두 유한하고 짧다. 15초였을 때 인베스팅이 멈추면 한국 /metrics
    요청마다 그만큼 기다렸다. 상한으로 적는다 — 더 짧게 바꾸는 것은 막지 않는다."""
    md.volatility_index("KR")

    timeout = state.calls[0].get("timeout")
    assert isinstance(timeout, tuple) and len(timeout) == 2, f"timeout={timeout!r}"
    connect, read = timeout
    assert 0 < connect <= 3 and 0 < read <= 5, f"timeout={timeout!r}"

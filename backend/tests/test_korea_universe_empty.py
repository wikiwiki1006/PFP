"""
한국 유니버스가 비는 것이 **드러나야** 한다 — 그리고 빈 것을 캐시에 굳히면 안 된다.

네이버가 시총 페이지를 옮겨(HTML → JSON API) 수집이 0종이 됐다. 화면의 한국
매매 신호에 후보가 하나도 없었는데, **오류가 아니라 빈 목록으로** 나타나서
아무도 몰랐다. 로그는 있었다 — `.KS 목표 200 중 0종목만 수집` 이 기동마다
찍혔다. 신호가 없던 게 아니라 **읽히지 않았다.**

그리고 이 경로에는 시한이 붙어 있다. 유니버스·이름 사전은 7일 캐시라, 0종을
받은 상태로 재생성이 돌면 **그 0 이 7일 굳는다.**

## 이 파일이 고정하는 것

**① 응답 형태가 또 바뀌어도 요약 한 줄이 남는가.** 행마다 경고를 남기므로
100행이면 100줄인데, 사람이 읽는 것은 마지막 요약이다. 그 줄이 사라지면
"드러나는 0" 이 "소음에 묻힌 0" 이 된다.

**② 부분 실패의 문턱.** 네이버 결과가 목표의 80% 에 못 미치면 느린 yfinance
폴백으로 간다. KOSPI 만 오고 KOSDAQ 이 0 이면 200종 — 문턱(280) 아래라
폴백으로 가야 한다. 통과시키면 **반쪽 유니버스가 7일 굳는다.**

**③ 좁은 사전이 넓은 사전을 지우지 않는 것.** 시총 상위 350종의 이름으로
전체(2,600종 이상)를 덮어쓰면 나머지는 화면에 코드만 남는다. 이 자리는 네이버가
막혀 있는 동안 **한 번도 돌지 않아서** 그 버그가 안 보였다.
"""
from __future__ import annotations

import logging
from unittest import mock

import pytest

from backend.services import korea_universe as ku


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _session_returning(pages: dict[str, list[list[dict]]]):
    """보드별 페이지 목록을 순서대로 돌려주는 가짜 세션.

    페이지를 다 쓰면 빈 목록을 준다 — 실제 API 도 마지막 페이지 뒤로는 그렇다.
    """
    calls = {"KOSPI": 0, "KOSDAQ": 0}

    class _Sess:
        headers: dict = {}

        def get(self, url, params=None, timeout=None):
            board = "KOSDAQ" if "KOSDAQ" in url else "KOSPI"
            i = calls[board]
            calls[board] += 1
            seq = pages.get(board, [])
            return _FakeResponse({"stocks": seq[i] if i < len(seq) else []})

    return _Sess


def _rows(prefix: str, n: int, *, shape_ok: bool = True) -> list[dict]:
    """보통주 코드로 보이는 행 n 개. `shape_ok=False` 면 응답 형태가 바뀐 것.

    코드를 `0` 으로 끝나게 만든다 — `_is_common_stock` 이 우선주를 그 자리로
    거른다. 아무 숫자나 쓰면 10 분의 1만 통과해서, 이 테스트가 "목표만큼
    수집" 을 재려는데 실제로는 필터를 재게 된다.
    """
    out = []
    for i in range(n):
        code = f"{prefix}{i:03d}0"
        out.append({"itemCode": code, "stockName": f"종목{i}"} if shape_ok
                   else {"symbol": code, "name": f"종목{i}"})     # 키 이름이 바뀌었다
    return out


def _fetch_with(pages, caplog):
    with mock.patch("requests.Session", _session_returning(pages)), \
         caplog.at_level(logging.WARNING):
        return ku.fetch_top_by_marketcap()


# ── ① 응답 형태가 바뀌면 0 이 드러나는가 ───────────────────────────────────────

def test_a_changed_response_shape_yields_nothing_and_says_so(caplog):
    """키 이름이 바뀌면 한 종목도 못 받고, **요약 한 줄이 남는다.**

    행마다 남기는 경고는 100행이면 100줄이라 사람이 읽지 않는다. 마지막
    요약(`목표 N 중 0종목만 수집`)이 실제로 읽히는 줄이고, 그게 사라지면
    "드러나는 0" 이 "소음에 묻힌 0" 이 된다.
    """
    universe, names = _fetch_with(
        {"KOSPI": [_rows("00", 100, shape_ok=False)],
         "KOSDAQ": [_rows("01", 100, shape_ok=False)]}, caplog)

    assert universe == [] and names == {}

    messages = [r.getMessage() for r in caplog.records]
    summaries = [m for m in messages if "종목만 수집" in m]
    assert summaries, (
        "nothing was collected and no summary line was written -- per-row "
        "warnings are too many to read, so the summary is the line a person "
        "actually sees. Without it the emptiness is silent. "
        "(요약이 없으면 0 이 소음에 묻힌다.)"
    )
    assert any("0종목" in m for m in summaries), f"요약이 0 을 말하지 않는다: {summaries}"


def test_a_normal_response_collects_and_stays_quiet(caplog):
    """대조군 — 정상 응답이면 목표만큼 받고 경고가 없다.

    경고가 정상 상태에서도 찍히면 그 경고는 배경이 되어 읽히지 않게 된다.
    이 리포에서 실제로 그렇게 됐다.
    """
    universe, names = _fetch_with(
        {"KOSPI": [_rows("00", 100), _rows("02", 100)],
         "KOSDAQ": [_rows("01", 100), _rows("03", 100)]}, caplog)

    assert len(universe) == ku.KOSPI_TOP + ku.KOSDAQ_TOP, len(universe)
    assert len(names) == len(universe), "이름이 티커 수와 다르다"
    assert not caplog.records, (
        f"정상 수집인데 경고가 찍혔다: {[r.getMessage() for r in caplog.records]}"
    )


def test_one_board_failing_is_reported_separately(caplog):
    """한쪽 보드만 비어도 그 보드에 대해 따로 말한다.

    합쳐서 "좀 모자람" 으로 뭉뚱그리면 어느 시장이 죽었는지 알 수 없다.
    """
    universe, _ = _fetch_with(
        {"KOSPI": [_rows("00", 100), _rows("02", 100)],
         "KOSDAQ": [_rows("01", 100, shape_ok=False)]}, caplog)

    assert len(universe) == ku.KOSPI_TOP, len(universe)
    assert any(".KQ" in r.getMessage() and "0종목" in r.getMessage()
               for r in caplog.records), (
        f"KOSDAQ 이 0인데 그 사실이 따로 남지 않았다: "
        f"{[r.getMessage() for r in caplog.records if '수집' in r.getMessage()]}"
    )


# ── ② 부분 수집은 문턱 아래면 폴백으로 간다 ────────────────────────────────────

@pytest.fixture
def cache():
    """`get_common`/`save_common` 을 메모리 dict 로 바꾼다."""
    store: dict = {}

    def fake_get(key):
        return store.get(key)

    def fake_save(key, value, ttl_seconds=None):
        store[key] = value

    with mock.patch("backend.db.market_cache.get_common", fake_get), \
         mock.patch("backend.db.market_cache.save_common", fake_save):
        yield store


def test_a_half_universe_does_not_get_cached_as_the_universe(cache):
    """KOSPI 만 오고 KOSDAQ 이 0 이면 그 200종을 유니버스로 굳히지 않는다.

    캐시는 7일이다. 반쪽을 저장하면 **일주일 동안 KOSDAQ 신호가 통째로
    없는 상태**가 되고, 그건 오류가 아니라 "후보가 없음" 으로 보인다.
    """
    with mock.patch.object(ku, "fetch_top_by_marketcap",
                           return_value=([f"{i:06d}.KS" for i in range(200)], {})), \
         mock.patch.object(ku, "get_listed_all", return_value=[]):
        out = ku.rebuild_scan_universe()

    assert out.get("ok") is False, f"반쪽 수집이 성공으로 끝났다: {out}"
    assert ku._SCAN_KEY not in cache, (
        f"a half-sized universe was cached for seven days: "
        f"{len(cache.get(ku._SCAN_KEY, []))} tickers -- KOSDAQ signals simply "
        "vanish for a week and it looks like 'no candidates'. "
        "(반쪽 유니버스가 7일 굳는다.)"
    )


def test_a_full_universe_is_cached(cache):
    """대조군 — 목표를 채우면 저장한다.

    없으면 위 검사는 "아무것도 저장하지 않는다" 는 구현으로도 통과한다.
    """
    full = [f"{i:06d}.KS" for i in range(ku.KOSPI_TOP + ku.KOSDAQ_TOP)]

    with mock.patch.object(ku, "fetch_top_by_marketcap", return_value=(full, {})):
        out = ku.rebuild_scan_universe()

    assert out.get("ok") is True and out.get("source") == "naver", out
    assert cache.get(ku._SCAN_KEY) == full


# ── ③ 좁은 사전이 넓은 사전을 지우지 않는다 ────────────────────────────────────

def test_the_name_map_is_merged_not_replaced(cache):
    """시총 상위 이름으로 전체 이름 사전을 덮어쓰지 않는다.

    상장 전체는 2,600종이 넘는데 시총 상위는 350종이다. 덮어쓰면 나머지는
    화면에 **코드만** 남는다 — '044490.KQ' 만 보면 무슨 회사인지 알 수 없다.

    이 자리는 네이버가 막혀 있는 동안 한 번도 돌지 않아서, 그 버그가 코드에
    있는 채로 보이지 않았다.
    """
    cache[ku._NAME_KEY] = {"999999.KQ": "옛날회사", "005930.KS": "구 이름"}
    full = [f"{i:06d}.KS" for i in range(ku.KOSPI_TOP + ku.KOSDAQ_TOP)]

    with mock.patch.object(ku, "fetch_top_by_marketcap",
                           return_value=(full, {"005930.KS": "삼성전자"})):
        ku.rebuild_scan_universe()

    merged = cache[ku._NAME_KEY]
    assert merged.get("999999.KQ") == "옛날회사", (
        f"a 350-name table erased the wider one: {len(merged)} names left -- "
        "every other holding shows as a bare code on screen. "
        "(좁은 사전이 넓은 사전을 지웠다.)"
    )
    assert merged.get("005930.KS") == "삼성전자", (
        "새로 받은 이름이 옛 이름을 이기지 않았다 — 개명·재상장이 반영되지 않는다"
    )


def test_an_empty_name_map_does_not_overwrite_the_cached_one(cache):
    """이름을 하나도 못 받았으면 기존 사전을 건드리지 않는다.

    빈 사전을 저장하면 그 상태가 **7일 굳는다.** 못 받은 것과 없는 것은 다르고,
    캐시에 굳는 순간 그 차이가 사라진다.
    """
    cache[ku._NAME_KEY] = {"005930.KS": "삼성전자"}
    full = [f"{i:06d}.KS" for i in range(ku.KOSPI_TOP + ku.KOSDAQ_TOP)]

    with mock.patch.object(ku, "fetch_top_by_marketcap", return_value=(full, {})):
        ku.rebuild_scan_universe()

    assert cache[ku._NAME_KEY] == {"005930.KS": "삼성전자"}, (
        f"an empty name map replaced the cached one: {cache[ku._NAME_KEY]} -- "
        "it would stay empty for seven days, and 'could not fetch' becomes "
        "indistinguishable from 'has no name'. (빈 사전이 7일 굳는다.)"
    )


def test_a_total_failure_caches_nothing(cache):
    """네이버도 상장목록도 실패하면 아무것도 저장하지 않는다.

    실패 결과를 캐시에 넣으면 **다음 시도가 그 캐시를 보고 성공했다고 여긴다.**
    """
    with mock.patch.object(ku, "fetch_top_by_marketcap", return_value=([], {})), \
         mock.patch.object(ku, "get_listed_all", return_value=[]):
        out = ku.rebuild_scan_universe()

    assert out.get("ok") is False, out
    assert cache == {}, f"실패했는데 캐시에 쓴 것이 있다: {sorted(cache)}"


def test_an_empty_universe_is_what_callers_see(cache):
    """캐시가 비면 `get_scan_universe()` 는 빈 목록이다 — 여기서 만들지 않는다.

    요청 처리 중에 한 시간짜리 재생성을 시작하면 안 된다. 다만 **그 빈 목록이
    화면에서는 '후보 없음' 으로 보인다** — 이 파일이 고정하려는 것이 바로
    그 0 이 어디서 오는지가 로그에 남는가이다.
    """
    assert ku.get_scan_universe() == []

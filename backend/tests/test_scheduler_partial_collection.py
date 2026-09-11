"""
수집 루프 — **부분 성공이 완전 성공처럼 보이지 않아야** 한다.

Cloud Run 은 요청이 끝나면 CPU 를 회수하므로 500종을 한 번에 받다 타임아웃되면
아무것도 안 남는다. 그래서 `max_tickers` 로 나눠 받고, 남은 종목은 다음 호출이
이어받는다 — **stale 목록이 줄어들기 때문에.** 그 이어받기가 실제로 되는지는
아무도 확인한 적이 없었다.

## 이 파일이 고정하는 것

**① 진척이 실제로 남는가.** 1회차가 받은 종목이 신선해져서 2회차가 **다른
종목**을 집는지. 안 되면 앞쪽 N 종만 영원히 신선하고 뒤쪽은 영원히 stale 인데,
겉으로는 매 주기 "N개 저장" 이 찍혀 정상으로 보인다.

**② 반쪽 데이터로 스캔을 갱신하지 않는가.** 스캔 결과는 6시간 캐시라, 남은
종목이 있는 채로 갱신하면 그 반쪽이 6시간 박힌다.

**③ 빈 응답을 진척으로 세지 않는가.** yfinance 가 빈 프레임을 주면 저장한 것이
없으므로 `remaining` 이 줄면 안 된다.

**④ 유니버스가 비면 조용히 0종을 도는 대신 그렇다고 말하는가.**
"""
from __future__ import annotations

import logging
from unittest import mock

import pandas as pd
import pytest

from backend.db import scheduler as sch


def _frame(tickers: list[str]) -> pd.DataFrame:
    idx = pd.bdate_range("2026-09-07", periods=3)
    return pd.DataFrame({t: [100.0, 101.0, 102.0] for t in tickers}, index=idx)


class _Collector:
    """수집 상태를 흉내 낸다 — 저장된 티커는 신선해진다.

    이게 이어받기의 전제다. `stale` 이 줄지 않으면 다음 호출이 같은 앞부분을
    다시 집는다.
    """

    def __init__(self, universe: list[str], *, never_fresh: set[str] | None = None):
        self.universe = universe
        self.fresh: set[str] = set()
        self.never_fresh = never_fresh or set()
        self.requested: list[list[str]] = []

    def stale(self, tickers, max_age_hours=None):
        return [t for t in tickers if t not in self.fresh]

    def download(self, tickers, period=None, inter_batch_sleep=None):
        self.requested.append(list(tickers))
        got = [t for t in tickers if t not in self.never_fresh]
        return _frame(got), _frame(got)

    def save(self, close_df, volume_df=None):
        self.fresh.update(c for c in close_df.columns)


def _run(collector, **kw):
    """`_update_daily_prices` 를 수집기 대역에 물려 한 번 돌린다."""
    from backend.db import market_cache as mcache

    with mock.patch.object(sch, "_universe_for", lambda market: collector.universe), \
         mock.patch.object(mcache, "get_stale_tickers", collector.stale), \
         mock.patch.object(mcache, "get_volume_stale_tickers",
                           lambda tickers, **k: collector.stale(tickers)), \
         mock.patch.object(mcache, "_yf_download_ohlcv_batched", collector.download), \
         mock.patch.object(mcache, "save_prices_to_db", collector.save), \
         mock.patch.object(mcache, "save_common", lambda *a, **k: None), \
         mock.patch.object(sch, "_run_safe", lambda name, fn, *a: None):
        return sch._update_daily_prices(market="KR", **kw)


# ── ① 나눠 받기가 실제로 이어지는가 ────────────────────────────────────────────

def test_each_call_takes_a_different_slice():
    """2회차가 1회차와 **다른 종목**을 집어야 한다.

    같은 앞부분을 다시 집으면 뒤쪽은 영원히 안 받는데, 로그에는 매번
    "N개 저장" 이 찍혀 정상으로 보인다. 부분 성공이 완전 성공처럼 보이는
    자리다.
    """
    universe = [f"T{i:03d}.KS" for i in range(50)]
    c = _Collector(universe)

    first = _run(c, max_tickers=20)
    second = _run(c, max_tickers=20)

    assert first["processed"] == 20 and first["remaining"] == 30, first
    assert second["processed"] == 20 and second["remaining"] == 10, second
    assert not set(c.requested[0]) & set(c.requested[1]), (
        "the second call asked for tickers the first had already collected -- "
        "progress is not being carried over, so the tail is never reached "
        "while the log still reports N saved every cycle. "
        "(같은 앞부분을 다시 집으면 뒤쪽은 영원히 안 받는다.)"
    )


def test_collection_finishes_and_reports_nothing_remaining():
    """대조군 — 반복하면 결국 전부 받고 `remaining` 이 0 이 된다.

    없으면 위 검사는 "매번 다른 20개를 무한히 집는" 구현으로도 통과한다.
    """
    universe = [f"T{i:03d}.KS" for i in range(50)]
    c = _Collector(universe)

    for _ in range(3):
        out = _run(c, max_tickers=20)

    assert out["remaining"] == 0 and out["stale"] == 10, out
    assert c.fresh == set(universe), f"{len(c.fresh)}종만 신선하다"


def test_a_ticker_that_never_returns_data_keeps_its_slot():
    """받아지지 않는 종목은 계속 stale 이라 **자리를 계속 차지한다.**

    상장폐지 등으로 yfinance 가 영영 안 주는 종목이 목록 앞쪽에 있으면,
    `sorted(...)[:max_tickers]` 라 매 호출이 그 자리를 다시 쓴다. 그 수가
    `max_tickers` 에 이르면 뒤쪽은 **한 번도 시도되지 않는다.**

    지금 동작을 그대로 적어 둔다 — 고칠지는 소유 창(pfp-0c) 판단이고,
    적어 두지 않으면 다음 사람이 이어받기를 무조건 진행한다고 읽는다.
    """
    universe = [f"T{i:03d}.KS" for i in range(10)]
    dead = {"T000.KS", "T001.KS"}
    c = _Collector(universe, never_fresh=dead)

    _run(c, max_tickers=3)
    _run(c, max_tickers=3)

    assert dead <= set(c.requested[0]), "전제: 죽은 종목이 앞쪽에 있다"
    assert dead <= set(c.requested[1]), (
        "a permanently uncollectable ticker stopped occupying its slot -- if "
        "that changed, this note is stale and the starvation risk is gone."
    )


# ── ② 반쪽으로 스캔을 갱신하지 않는다 ──────────────────────────────────────────

def test_the_scan_is_not_refreshed_while_work_remains():
    """남은 종목이 있으면 매매신호 스캔을 미룬다.

    스캔 결과는 6시간 캐시다. 반쪽 데이터로 갱신하면 **그 반쪽이 6시간
    박히고** 다음 수집분이 반영되지 않는다.
    """
    universe = [f"T{i:03d}.KS" for i in range(50)]
    c = _Collector(universe)

    out = _run(c, max_tickers=20)

    assert out["remaining"] > 0, "전제: 아직 남았다"
    assert out["scan_refreshed"] is False, (
        "the signal scan was refreshed from half the universe -- that result "
        "sits in a six-hour cache and the rest of the collection never "
        "reaches it. (반쪽이 6시간 박힌다.)"
    )


def test_the_scan_is_refreshed_once_nothing_remains():
    """대조군 — 다 받으면 스캔을 갱신한다."""
    universe = [f"T{i:03d}.KS" for i in range(10)]
    c = _Collector(universe)

    out = _run(c)

    assert out["remaining"] == 0 and out["scan_refreshed"] is True, out


# ── ③ 빈 응답을 진척으로 세지 않는다 ───────────────────────────────────────────

def test_an_empty_download_claims_no_progress(caplog):
    """yfinance 가 빈 프레임을 주면 `remaining` 이 줄지 않는다.

    저장한 것이 없는데 진척으로 세면, 다음 호출이 "남은 것 없음" 으로 보고
    영영 안 받는다.
    """
    universe = [f"T{i:03d}.KS" for i in range(10)]
    c = _Collector(universe)
    c.download = lambda tickers, **kw: (pd.DataFrame(), pd.DataFrame())

    with caplog.at_level(logging.WARNING):
        out = _run(c, max_tickers=5)

    assert out["processed"] == 0, out
    assert out["remaining"] == out["stale"] == 10, (
        f"an empty response was counted as progress: {out} -- the next call "
        "sees nothing left to do and those tickers are never fetched."
    )
    assert out["scan_refreshed"] is False
    assert caplog.records, "빈 응답인데 아무 기록도 없다"


# ── ④ 유니버스가 비면 그렇다고 말한다 ──────────────────────────────────────────

def test_an_empty_universe_says_so_instead_of_looping_over_nothing(caplog):
    """유니버스가 비면 경고를 남기고 건너뛴다.

    조용히 0종을 돌면 "수집은 정상" 으로 보인다 — 한국 유니버스가 0종이던
    동안 실제로 그렇게 보였다.
    """
    with mock.patch.object(sch, "_universe_for", lambda market: []), \
         caplog.at_level(logging.WARNING):
        out = sch._update_daily_prices(market="KR")

    assert out == {"stale": 0, "processed": 0, "remaining": 0, "scan_refreshed": False}
    assert caplog.records, "유니버스가 비었는데 아무 말도 없다"


def test_pairs_targets_are_empty_when_the_universe_is():
    """유니버스가 비면 페어 대상도 빈다 — 예외가 아니라.

    `universe[:20]` 이라 자연히 빈 리스트가 되는데, 그게 화면에서는
    "페어 없음" 으로 보인다. 0 이 어디서 왔는지는 위 경고에만 남는다.
    """
    assert sch._pairs_targets("KR", []) == []
    assert sch._pairs_targets("KR", ["A.KS", "B.KS"]) == ["A.KS", "B.KS"]
    assert sch._pairs_targets("US", []) == sch._PAIRS_PRECOMPUTE_TICKERS


# ── 요청 경로에서 한 시간짜리가 도는 경우 ──────────────────────────────────────

def test_an_empty_korean_universe_is_rebuilt_once():
    """캐시가 비면 즉석 생성을 **한 번** 부른다.

    docstring 은 "7 요청 · 2초" 라고 적고 있는데, 그건 네이버가 살아 있을
    때다. 네이버가 죽으면 `rebuild_scan_universe` 가 yfinance 폴백으로
    빠져 종목당 1초가 넘는 조회를 수천 번 돈다 — **요청 경로에서.**
    오늘 네이버가 실제로 죽어 있었다.

    여기서는 호출 횟수만 고정한다. 두 번 부르면 그 비용이 두 배가 된다.
    """
    calls = {"n": 0}

    def fake_rebuild(*a, **kw):
        calls["n"] += 1
        return {"ok": False}

    from backend.services import korea_universe as ku

    with mock.patch.object(ku, "get_scan_universe", lambda: []), \
         mock.patch.object(ku, "rebuild_scan_universe", fake_rebuild):
        out = sch._universe_for("KR")

    assert out == [], "재생성이 실패했는데 유니버스가 생겼다"
    assert calls["n"] == 1, f"재생성을 {calls['n']}번 불렀다"


def test_a_cached_korean_universe_is_not_rebuilt():
    """대조군 — 캐시가 있으면 즉석 생성을 부르지 않는다.

    없으면 위 검사는 "언제나 재생성한다" 는 구현으로도 통과하는데, 그러면
    요청마다 그 비용을 낸다.
    """
    from backend.services import korea_universe as ku

    with mock.patch.object(ku, "get_scan_universe", lambda: ["005930.KS"]), \
         mock.patch.object(ku, "rebuild_scan_universe",
                           side_effect=AssertionError("캐시가 있는데 재생성했다")):
        assert sch._universe_for("KR") == ["005930.KS"]

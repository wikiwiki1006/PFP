"""
보유 종목은 **유니버스 밖이어도** 일봉이 갱신돼야 한다.

스캔 유니버스는 S&P500 / KOSPI200·KOSDAQ150 이다. 그 밖의 종목을 가진
사용자는 수집 대상이 아예 아니어서 일봉이 그 자리에 멈춘다. 현재가는
`live_quotes` 가 요청마다 받아오므로 **총자산은 맞는다** — 그래서 안 보인다.
멈추는 것은 자산곡선·기간 수익률·베타이고, 화면은 그걸 측정값으로 그린다.

운영 실측 (2026-09-11):

    CIFR        17일 정지
    AB           8일 정지
    207940.KS    2일 정지   (삼성바이오로직스 — 유니버스에서 밀려남)

## 무엇을 재는가

  ① `_held_tickers` 가 그 시장의 보유 종목을 준다 — 실DB 로.
  ② 유니버스 **밖** 보유가 수집 대상에 들어간다.
  ③ 유니버스 **안** 보유가 두 번 들어가지 않는다 (대조군).
  ④ 스캔 유니버스는 **안 넓어진다** — 남의 소형주가 매매신호에 섞이면
     그 스캔이 무엇을 훑은 것인지가 달라진다. 이건 의도된 비대칭이라
     안 재면 다음 사람이 "둘 다 넓히는 게 맞겠지" 로 고친다.

yfinance 는 부르지 않는다. 재려는 것은 **대상 목록에 무엇이 들어가는가**
이지 그 종목을 실제로 받아오는지가 아니다.
"""
from __future__ import annotations

import logging
from unittest import mock

import pandas as pd
import pytest

from backend.db import scheduler as sch

UID = "__TEST_U_held"
OUTSIDE = "CIFR"          # 유니버스 밖 — 운영에서 17일 멈춰 있던 종목
INSIDE = "T000.KS"        # 아래 합성 유니버스 안에 있는 티커
_UNIVERSE = [f"T{i:03d}.KS" for i in range(5)]


@pytest.fixture
def holder(live_db):
    """보유 종목을 직접 넣고 지운다."""
    from backend.db import get_conn
    from backend.db import users_repo as ur

    def purge():
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM holdings WHERE user_id=%s", (UID,))
            cur.execute("DELETE FROM users    WHERE id=%s", (UID,))

    def hold(ticker, market="KR"):
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO holdings(user_id, ticker, market, qty, avg_cost) "
                "VALUES(%s,%s,%s,1,1) ON CONFLICT DO NOTHING",
                (UID, ticker, market),
            )

    purge()
    assert ur.upsert_user(UID, email=f"{UID}@example.com") is True
    yield hold
    purge()


# ── ① 보유 종목을 실제로 읽는다 ───────────────────────────────────────────────

def test_held_tickers_come_from_that_market_only(holder):
    """그 시장의 보유만 준다 — CASH 는 빼고, 다른 시장은 섞지 않는다.

    시장을 안 가르면 미국 수집이 한국 티커를 받으러 가고, 그 요청은
    조용히 빈 응답으로 끝난다 (§1.1).
    """
    holder(OUTSIDE, market="US")
    holder(INSIDE, market="KR")
    holder("CASH", market="KR")

    kr = sch._held_tickers("KR")
    us = sch._held_tickers("US")

    assert INSIDE in kr and OUTSIDE not in kr, f"KR 목록이 섞였다: {kr}"
    assert OUTSIDE in us and INSIDE not in us, f"US 목록이 섞였다: {us}"
    assert "CASH" not in kr + us, "현금이 수집 대상에 들어갔다"


def test_a_lookup_failure_yields_nothing_but_says_so(caplog):
    """조회가 실패하면 빈 목록이다 — **그리고 기록이 남는다.**

    유니버스 수집까지 막을 이유는 없다. 다만 조용히 넘어가면 "보유 종목만
    계속 멈춰 있다" 를 설명할 단서가 없다 (§1.3).
    """
    with mock.patch("backend.db.is_available", lambda: False), \
         caplog.at_level(logging.WARNING):
        assert sch._held_tickers("KR") == []

    assert caplog.records, "보유 조회를 못 했는데 아무 기록도 없다"


# ── ②③④ 수집 대상에 들어가되 스캔은 안 넓어진다 ──────────────────────────────

class _Collector:
    """수집 상태 대역 — 무엇을 요청했는지만 기록한다."""

    def __init__(self):
        self.requested: list[list[str]] = []

    def stale(self, tickers, max_age_hours=None):
        return list(tickers)

    def download(self, tickers, period=None, inter_batch_sleep=None):
        self.requested.append(list(tickers))
        idx = pd.bdate_range("2026-09-07", periods=3)
        frame = pd.DataFrame({t: [100.0, 101.0, 102.0] for t in tickers}, index=idx)
        return frame, frame

    def save(self, close_df, volume_df=None):
        return len(close_df.columns)


def _run(held, universe=_UNIVERSE):
    """`_update_daily_prices` 를 대역에 물려 한 번 돌린다. yfinance 는 안 탄다."""
    from backend.db import market_cache as mcache

    c = _Collector()

    with mock.patch.object(sch, "_universe_for", lambda market: list(universe)), \
         mock.patch.object(sch, "_held_tickers", lambda market: list(held)), \
         mock.patch.object(mcache, "get_stale_tickers", c.stale), \
         mock.patch.object(mcache, "get_volume_stale_tickers",
                           lambda tickers, **k: c.stale(tickers)), \
         mock.patch.object(mcache, "_yf_download_ohlcv_batched", c.download), \
         mock.patch.object(mcache, "save_prices_to_db", c.save), \
         mock.patch.object(mcache, "save_common", lambda *a, **k: None), \
         mock.patch.object(sch, "_run_safe", lambda name, fn, *a: None):
        out = sch._update_daily_prices(market="KR")
    return c, out


def test_a_holding_outside_the_universe_is_collected(holder):
    """유니버스 밖 보유가 **수집 대상에 들어간다.**

    안 들어가면 그 종목의 일봉이 그대로 멈추고, 총자산은 맞는데
    자산곡선·기간 수익률·베타만 조용히 낡는다.
    """
    c, out = _run(held=[OUTSIDE])

    requested = [t for batch in c.requested for t in batch]
    assert OUTSIDE in requested, (
        f"a held ticker outside the universe was never collected: {requested} "
        "-- its daily bars stop while live quotes keep the total looking right."
    )


def test_an_in_universe_holding_is_not_collected_twice(holder):
    """대조군 — 유니버스 안 보유는 **한 번만** 들어간다.

    중복이 들어가면 yfinance 프레임에 같은 열이 두 번 생기고, 그 프레임을
    받는 쪽이 `int(Series)` 로 죽는다. 실제로 그래서 한국 매매신호 스캔이
    20분마다 죽었다.
    """
    c, out = _run(held=[INSIDE])

    requested = [t for batch in c.requested for t in batch]
    assert requested.count(INSIDE) == 1, (
        f"{INSIDE} appears {requested.count(INSIDE)} times in the collection "
        "targets -- duplicate columns downstream crash the scan."
    )


def test_the_scan_universe_is_not_widened(holder):
    """스캔 유니버스는 **안 넓어진다** — 의도된 비대칭이다.

    남의 소형주가 매매신호 스캔에 섞이면 그 스캔이 무엇을 훑은 것인지가
    달라진다. 여기서 필요한 것은 "그 종목의 일봉이 갱신되는가" 뿐이다.
    적어 두지 않으면 다음 사람이 "둘 다 넓히는 게 맞겠지" 로 고친다.

    **스캔 함수를 직접 돌려 무엇이 들어가는지 본다.** 처음엔 수집 쪽에서
    스캔 호출을 가로채 목록을 만들어 비교했는데, 그건 내가 만든 리스트를
    내가 검사하는 것이라 아무것도 재지 못했다.
    """
    from backend.db import market_cache as mcache
    from backend.services import trading_signals

    seen: list[list[str]] = []
    idx = pd.bdate_range("2026-01-01", periods=300)

    def _fake_scan(close_df, volume_df, top_n=10):
        seen.append(list(close_df.columns))
        return {"long_picks": [], "short_picks": []}

    def _prices(tickers, period, fill=True):
        # 보유 종목까지 DB 에 있다 — 스캔이 **고를 수 있는데도 안 고르는지**를
        # 봐야 한다. 없는 것을 안 골랐다면 아무것도 증명하지 못한다.
        cols = list(tickers) + [OUTSIDE]
        return pd.DataFrame({t: [100.0] * len(idx) for t in cols}, index=idx)

    with mock.patch.object(sch, "_universe_for", lambda market: list(_UNIVERSE)), \
         mock.patch.object(mcache, "get_prices_from_db", _prices), \
         mock.patch.object(mcache, "get_volume_from_db",
                           lambda tickers, period: None), \
         mock.patch.object(mcache, "save_common", lambda *a, **k: None), \
         mock.patch.object(trading_signals, "sma_macd_rsi_scan", _fake_scan):
        sch._update_signal_scan(market="KR")

    assert seen, "스캔이 아예 안 돌았다 — 이 검사의 전제가 없다"
    assert OUTSIDE not in seen[0], (
        f"a user's off-universe holding entered the signal scan: {seen[0]} -- "
        "the scan then means something different from what its name says."
    )
    assert set(seen[0]) == set(_UNIVERSE), f"스캔 대상이 유니버스와 다르다: {seen[0]}"


def test_nothing_is_collected_when_there_is_nothing_to_collect(holder, caplog):
    """대조군 — 유니버스도 보유도 비면 그렇다고 말하고 건너뛴다.

    없으면 위 검사들은 "언제나 전부 요청한다" 는 구현으로도 통과한다.
    """
    with caplog.at_level(logging.WARNING):
        c, out = _run(held=[], universe=[])

    assert out == {"stale": 0, "processed": 0, "remaining": 0, "scan_refreshed": False}
    assert c.requested == [], "받을 것이 없는데 요청했다"
    assert caplog.records, "아무것도 못 받는데 아무 말도 없다"


def test_holdings_alone_still_get_collected(holder, caplog):
    """유니버스만 비어도 보유 종목 수집은 이어간다.

    한국 유니버스가 0종이던 동안 실제로 그 상태였다. 유니버스가 없다고
    보유까지 멈추면 그 사용자의 일봉은 영영 안 들어온다.
    """
    with caplog.at_level(logging.WARNING):
        c, out = _run(held=[OUTSIDE], universe=[])

    requested = [t for batch in c.requested for t in batch]
    assert requested == [OUTSIDE], f"보유만 있는데 요청이 {requested}"
    assert caplog.records, "유니버스가 비었는데 아무 말도 없다"

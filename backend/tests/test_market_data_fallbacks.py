"""
`market_data` 의 실패 경로 — 15개 분기 중 14개가 미실행이었다.

이 모듈이 만드는 값은 전부 화면과 프롬프트로 간다. 섹터 등락률, 실적·배당일,
뉴스, 가격 프레임. 그리고 이 모듈에서 **`0` 과 `None` 을 혼동한 사고가 실제로
났다** — 섹터 1M/3M/6M 이 전부 `0.0` 으로 나왔는데 데이터가 없어서였고,
사용자에게는 "모든 섹터가 보합" 으로 보였다 (CLAUDE.md §1.3).

그 뒤로 `_chg` 는 계산 불가를 `None` 으로 돌려주고, 배당수익률도 '무배당' 과
'모름' 을 가른다. 그 판단들이 **실제로 도는지** 아무도 확인한 적이 없었다.

## 창별 DB 주의

이 모듈의 분기 일부는 "DB 에 데이터가 있느냐" 로 갈린다. 그 답은 **이 창의
DB(`pfp_test`)에 대한 사실**이고 다른 창에서는 다르다 — 실제로 같은 질문에
두 창이 다른 숫자를 보고했고 둘 다 자기 창에 대해서는 맞았다.

그래서 DB 상태에 의존하는 검사는 **행을 직접 만들어** 고정한다. 티커는
실재하지 않는 이름(`__TEST__`)을 쓴다 — `market_prices` 키가
`(ticker, price_date)` 라 실재 티커를 쓰면 다른 테스트와 부딪친다.
"""
from __future__ import annotations

import logging
from unittest import mock

import pandas as pd
import pytest

from backend.services import market_data as md


# ── 저장 깊이: 얕은 요청이 깊은 백필을 굶기지 않는다 ───────────────────────────

@pytest.mark.parametrize("period, expect_canonical, why", [
    ("1mo", True, "짧은 요청은 표준 기간으로 올린다"),
    ("5d", True, "더 짧아도 마찬가지"),
    ("10y", False, "더 긴 요청은 그대로 둔다"),
])
def test_storage_depth_is_not_capped_by_a_shallow_request(period, expect_canonical, why):
    """저장 깊이를 호출자의 요청 기간과 분리한다.

    `/holdings-detail` 이 `1mo` 로 먼저 도착하면 그 티커는 1개월치만 저장되고
    `updated_at` 이 지금으로 찍힌다. 그러면 `/metrics` 가 `2y` 를 요청해도
    '컬럼 존재 + 신선' 으로 판정돼 **깊은 백필이 영영 일어나지 않는다.**
    """
    out = md.canonical_period(period)
    if expect_canonical:
        assert out == md._CANONICAL_PERIOD, f"{why}: {out}"
    else:
        assert out == period, f"{why}: {out}"


def test_an_unparsable_period_falls_back_to_the_canonical_depth():
    """기간 문자열을 못 읽으면 얕게가 아니라 **표준 깊이**로 간다.

    반대로 떨어지면 위의 굶주림이 그대로 재현된다. 못 읽었을 때 안전한 쪽은
    더 깊게 받는 것이다.
    """
    with mock.patch("backend.db.market_cache.period_to_days",
                    side_effect=ValueError("unknown period")):
        assert md.canonical_period("nonsense") == md._CANONICAL_PERIOD


# ── 가격 프레임: 요청이 비면 DB 도 네트워크도 건드리지 않는다 ──────────────────

def test_close_df_is_empty_for_no_tickers():
    """빈 요청에는 빈 프레임이다."""
    with mock.patch("backend.db.market_cache.get_prices_from_db",
                    side_effect=AssertionError("빈 요청인데 DB 를 읽었다")):
        out = md.get_close_df([], include_market=False)

    assert isinstance(out, pd.DataFrame) and out.empty


# ── 섹터: 계산 불가는 0% 가 아니다 ─────────────────────────────────────────────
#
# 실제 사고: 1M/3M/6M 이 전부 0.0 으로 나왔는데 데이터가 없어서였다.
# 화면에는 "모든 섹터가 보합" 으로 보였다.

_ETF = "__TEST_ETF__"


def _sector_rows(cur: float, prev_1m, *, month_frame: bool = True) -> list[dict]:
    """`get_sector_table` 을 한 섹터만으로 돌린다.

    1M 은 6개월 프레임의 **22행 전**에서 온다. 그래서 22행을 만들고 그 첫
    행만 원하는 값으로 둔다. `month_frame=False` 면 그 프레임에서 이 ETF 열을
    빼 `prev_1m.get(etf)` 가 `None` 이 되게 한다.

    1개월 프레임의 마지막 두 행은 서로 달라야 한다 — 같으면 장 마감 후
    ffill 아티팩트로 보고 한 행을 잘라내서 행이 모자라 빈 목록이 나온다.
    """
    idx_1mo = pd.bdate_range("2026-01-05", periods=3)
    df_1mo = pd.DataFrame({_ETF: [90.0, 95.0, cur]}, index=idx_1mo)

    idx_6mo = pd.bdate_range("2025-09-01", periods=22)
    col = _ETF if month_frame else "__OTHER__"
    df_6mo = pd.DataFrame({col: [prev_1m] + [100.0] * 21}, index=idx_6mo)

    with mock.patch.object(md, "_get_sector_etf_df_1mo", lambda market="US": df_1mo), \
         mock.patch.object(md, "get_close_df", lambda *a, **kw: df_6mo), \
         mock.patch.object(md, "sector_etfs_for", lambda market: [("테스트", _ETF)]):
        return md.get_sector_table("US")


@pytest.mark.parametrize("prev, month_frame, why", [
    (float("nan"), True, "직전 값이 NaN"),
    (0.0, True, "직전 값이 0 — 나눌 수 없다"),
    (100.0, False, "6개월 프레임에 이 ETF 가 없다"),
])
def test_sector_change_is_none_when_it_cannot_be_computed(prev, month_frame, why):
    """계산 불가는 `None` 이다. `0.0` 은 '보합' 이라는 주장이다.

    실제 사고: 1M/3M/6M 이 전부 `0.0` 으로 나왔는데 데이터가 없어서였다.
    화면에는 "모든 섹터가 보합" 으로 보였다.
    """
    rows = _sector_rows(110.0, prev, month_frame=month_frame)

    assert rows, "행이 하나도 안 나왔다 — 이 검사의 전제가 없다"
    assert rows[0]["change_1m_pct"] is None, (
        f"{why}: got {rows[0]['change_1m_pct']!r} -- 0.0 renders as 'flat', "
        "which is a claim about the market. (데이터 없음이 보합으로 보인다.)"
    )


def test_sector_change_is_a_number_when_it_can_be_computed():
    """대조군 — 잴 수 있으면 숫자가 나온다.

    없으면 위 검사들은 "1M 을 아예 안 준다" 는 구현으로도 통과한다.
    """
    rows = _sector_rows(110.0, 100.0)

    assert rows and rows[0]["change_1m_pct"] == pytest.approx(10.0), rows


def test_a_sector_with_no_current_price_is_left_out_entirely():
    """현재가가 없으면 그 섹터 행을 만들지 않는다.

    가격 자리에 `0` 을 넣고 행을 만들면 그 섹터가 무가치한 것처럼 보인다.
    행을 빼는 쪽이 정직하다.
    """
    rows = _sector_rows(float("nan"), 100.0)

    assert rows == [], f"현재가가 NaN 인데 행이 생겼다: {rows}"


@pytest.mark.parametrize("fn, expected, why", [
    (lambda: md.get_sector_changes("US"), {}, "get_sector_changes"),
    (lambda: md.get_sector_table("US"), [], "get_sector_table"),
])
def test_sector_lookups_come_back_empty_when_prices_fail(fn, expected, why):
    """가격을 못 받으면 빈 결과다 — 0 으로 채운 행이 아니다.

    빈 결과는 화면이 "표시할 것이 없다" 로 그릴 수 있다. 0 으로 채운 행은
    측정값으로 그려진다.

    **두 함수의 가격 진입점이 다르다.** `get_sector_changes` 는
    `_get_sector_etf_df_1mo` 만 쓰고 `get_sector_table` 은 거기에
    `get_close_df` 를 더한다. 하나만 막으면 나머지 하나가 **실제 데이터를
    읽는다** — 처음에 `get_close_df` 만 막았다가 이 테스트가 진짜 섹터
    등락률을 받아 와서 알았다. 둘 다 막는다.
    """
    with mock.patch.object(md, "_get_sector_etf_df_1mo",
                           side_effect=RuntimeError("prices unavailable")), \
         mock.patch.object(md, "get_close_df",
                           side_effect=RuntimeError("prices unavailable")):
        assert fn() == expected, why


# ── 실적·배당: '무배당' 과 '모름' 은 다르다 ────────────────────────────────────

def _earnings_for(ticker_obj) -> dict:
    with mock.patch.object(md, "_cached", lambda key, ttl, f: f()), \
         mock.patch("yfinance.Ticker", lambda t: ticker_obj):
        rows = md.get_earnings_dividends(["__TEST__"])
    return rows[0]


def test_a_missing_dividend_yield_is_none_not_zero():
    """배당수익률을 못 읽으면 `None` 이다.

    `0%` 로 적으면 배당주를 무배당으로 오해해 후보에서 뺀다. 그 종목이 왜
    빠졌는지는 어디에도 안 남는다.
    """
    tk = mock.Mock()
    tk.calendar = {}
    tk.dividends = pd.Series(dtype=float)
    tk.info = {}                      # dividendYield 없음

    assert _earnings_for(tk)["div_yield"] is None


def test_a_present_dividend_yield_is_taken_as_a_percent():
    """대조군 — 값이 있으면 그대로 퍼센트로 적는다.

    yfinance 의 `dividendYield` 는 **이미 퍼센트**다 (AAPL 0.34 = 0.34%).
    크기를 보고 단위를 추측하면 0.05% 를 주는 종목이 5% 로 부풀려진다.
    """
    tk = mock.Mock()
    tk.calendar = {}
    tk.dividends = pd.Series(dtype=float)
    tk.info = {"dividendYield": 0.34}

    assert _earnings_for(tk)["div_yield"] == "0.34%"


def test_a_broken_ticker_still_returns_a_row():
    """실적·배당 조회가 전부 터져도 그 종목의 행은 나온다.

    행이 통째로 빠지면 호출자는 "이 종목은 조회하지 않았다" 와 "조회했는데
    없었다" 를 구별하지 못한다.
    """
    tk = mock.Mock()
    type(tk).calendar = mock.PropertyMock(side_effect=RuntimeError("calendar down"))
    type(tk).dividends = mock.PropertyMock(side_effect=RuntimeError("dividends down"))
    type(tk).info = mock.PropertyMock(side_effect=RuntimeError("info down"))

    row = _earnings_for(tk)

    assert row["ticker"] == "__TEST__"
    assert row["div_yield"] is None, "못 읽었는데 값이 생겼다"


def test_a_ticker_that_cannot_be_constructed_still_returns_a_row():
    """`yf.Ticker(t)` 자체가 터져도 행은 나온다.

    위 테스트는 속성 조회가 터지는 경우고, 이건 객체를 만들지도 못하는
    경우다 — 레이트리밋에 걸리면 이쪽이다. 바깥 `except` 가 따로 있는 이유이고,
    그 줄은 지금까지 한 번도 돌지 않았다.
    """
    with mock.patch.object(md, "_cached", lambda key, ttl, f: f()), \
         mock.patch("yfinance.Ticker", side_effect=RuntimeError("rate limited")):
        rows = md.get_earnings_dividends(["__TEST__"])

    assert rows and rows[0]["ticker"] == "__TEST__", (
        f"the row vanished when the ticker could not be constructed: {rows} -- "
        "the caller cannot tell 'not looked up' from 'looked up and empty'."
    )
    assert rows[0]["div_yield"] is None


# ── 백그라운드 갱신: 쿨다운 안에서는 다시 부르지 않는다 ────────────────────────

def test_background_refresh_skips_tickers_still_in_cooldown():
    """방금 시도한 티커는 쿨다운이 끝날 때까지 다시 받지 않는다.

    yfinance 는 IP 단위로 레이트리밋을 건다. 쿨다운이 없으면 실패한 티커를
    요청마다 다시 때리고, 그게 **다섯 창을 한꺼번에 죽인다** (§7.7).
    """
    import time as _time

    md._bg_last_attempt["__TEST__"] = _time.time()      # 방금 시도한 것으로 표시
    md._bg_in_progress.discard("__TEST__")
    try:
        with mock.patch("backend.db.market_cache._yf_download_batched",
                        side_effect=AssertionError("쿨다운 중인데 다시 받았다")):
            md._bg_refresh_tickers(["__TEST__"], "1mo")
    finally:
        md._bg_last_attempt.pop("__TEST__", None)


def test_background_refresh_skips_tickers_already_running():
    """이미 진행 중인 티커도 다시 시작하지 않는다 — 같은 수집이 두 번 돈다."""
    md._bg_in_progress.add("__TEST__")
    md._bg_last_attempt.pop("__TEST__", None)
    try:
        with mock.patch("backend.db.market_cache._yf_download_batched",
                        side_effect=AssertionError("진행 중인데 또 시작했다")):
            md._bg_refresh_tickers(["__TEST__"], "1mo")
    finally:
        md._bg_in_progress.discard("__TEST__")


# ── 뉴스: 한 종목이 죽어도 나머지는 나온다 ─────────────────────────────────────

def test_news_for_one_broken_ticker_does_not_lose_the_others(caplog):
    """한 티커의 뉴스 조회가 실패해도 나머지 결과는 유지된다."""
    good = [{"content": {"title": "정상 헤드라인", "canonicalUrl": {"url": "http://x"},
                         "pubDate": "2026-09-11T00:00:00Z"}}]

    def fake_ticker(sym):
        tk = mock.Mock()
        if sym == "__BROKEN__":
            type(tk).news = mock.PropertyMock(side_effect=RuntimeError("news down"))
        else:
            tk.news = good
        return tk

    with mock.patch.object(md, "_cached", lambda key, ttl, f: f()), \
         mock.patch("yfinance.Ticker", fake_ticker), \
         caplog.at_level(logging.WARNING):
        out = md.get_portfolio_news(["__BROKEN__", "__OK__"], max_per=1)

    tickers = {n["ticker"] for n in out}
    assert "__BROKEN__" not in tickers, "실패한 티커의 뉴스가 어디선가 생겼다"
    assert "__OK__" in tickers, (
        f"one broken ticker took the others with it: {tickers} -- each lookup "
        "is independent and a single failure must not empty the feed."
    )

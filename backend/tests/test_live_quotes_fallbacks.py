"""
`live_quotes` 의 실패 경로 — 모듈 전체에 테스트가 하나도 없었다.

폴백 감사에서 이 모듈의 분기 13개가 **전부 미실행**으로 나왔다. 다른 모듈은
일부가 덮여 있었는데 여기는 0/13 이었다.

그리고 이 모듈의 실패 경로는 **드물지 않다.** yfinance 는 IP 단위로 레이트
리밋을 걸고, 그건 실제로 걸린다 (CLAUDE.md §7.7: 병렬 창 다섯이 같이 죽는
시나리오). 즉 여기 `except` 들은 "만일을 위한" 것이 아니라 정기적으로 도는
경로이고, 그 안에서 무엇이 나오는지 아무도 확인한 적이 없었다.

## 가장 중요한 두 가지

**`_finite`** — 이 모듈에서 가격이 DB 로 들어가는 유일한 문 앞의 검문소다.
NaN 이 통과하면 화면의 현재가가 NaN 이 되고, 그 값으로 계산된 평가액·수익률이
전부 오염된다.

**티커별 세션 라벨** — `session=None` 이면 티커마다 자기 거래소 세션을 적는다.
전에는 `us_market_status()` 하나를 전 티커에 찍어서, 11:16 KST 에 `^KS11` 이
`post` 로 기록됐다. 배치에 시장이 섞이므로 전역 라벨은 **한쪽에 대해 항상
틀린다.**
"""
from __future__ import annotations

import logging
import math
from unittest import mock

import pytest

from backend.services import live_quotes as lq


# ── `_finite` — 가격이 들어가는 문 앞의 검문소 ─────────────────────────────────

@pytest.mark.parametrize("value, expected, why", [
    (123.45, 123.45, "평범한 실수"),
    ("123.45", 123.45, "문자열 숫자도 받는다"),
    (0, 0.0, "0 은 유한하다 — 여기서 거르지 않는다"),
    (float("nan"), None, "NaN"),
    (float("inf"), None, "무한대"),
    (float("-inf"), None, "음의 무한대"),
    (None, None, "값이 없다"),
    ("", None, "빈 문자열"),
    ("n/a", None, "숫자로 못 읽는 문자열"),
    ({"a": 1}, None, "숫자가 아닌 타입"),
])
def test_finite_filters_everything_that_is_not_a_real_number(value, expected, why):
    """숫자가 아니거나 유한하지 않으면 `None` 이다.

    NaN 이 통과하면 화면의 현재가가 NaN 이 되고, 그것으로 계산된 평가액·
    수익률·비중이 전부 오염된다. 한 종목의 나쁜 값 하나가 포트폴리오 전체를
    무효화한다.
    """
    out = lq._finite(value)
    if expected is None:
        assert out is None, f"{why}: {out!r} 가 통과했다"
    else:
        assert out == pytest.approx(expected), f"{why}: {out!r}"
        assert math.isfinite(out)


# ── 저장: 쓸 행이 없으면 DB 를 건드리지 않는다 ─────────────────────────────────

@pytest.mark.parametrize("quotes, why", [
    ({}, "빈 배치"),
    ({"AAPL": {"price": None}}, "가격이 없다"),
    ({"AAPL": {"price": float("nan")}}, "가격이 NaN"),
    ({"AAPL": {"price": 0}}, "가격이 0 — 실제 가격일 수 없다"),
    ({"AAPL": {"price": -5.0}}, "가격이 음수"),
])
def test_save_quotes_writes_nothing_when_there_is_no_usable_price(quotes, why):
    """쓸 가격이 없으면 0 을 돌려주고 **DB 에 손대지 않는다.**

    여기서 0 은 "저장한 행 수" 이므로 정직한 값이다 — 가격 0 을 저장하는 것과
    전혀 다르다. 가격 0 이 들어가면 화면은 그 종목을 무가치로 그린다.
    """
    with mock.patch("backend.db.is_available", return_value=True), \
         mock.patch("backend.db.get_conn") as conn:
        assert lq.save_quotes(quotes, source="test", session="closed") == 0, why
        assert not conn.called, f"{why}: 쓸 것이 없는데 DB 커넥션을 잡았다"


def test_save_quotes_does_nothing_without_a_database():
    """DB 가 없으면 조용히 0 이다 — 예외로 호출자를 죽이지 않는다."""
    with mock.patch("backend.db.is_available", return_value=False):
        assert lq.save_quotes({"AAPL": {"price": 231.0}}, source="t", session="open") == 0


# ── 세션 라벨은 티커마다 자기 거래소를 따른다 ──────────────────────────────────

def test_session_label_is_per_ticker_when_none_is_given():
    """`session=None` 이면 티커마다 `market_session(ticker)` 을 쓴다.

    전역 라벨 하나를 찍으면 배치에 시장이 섞였을 때 **한쪽은 항상 틀린다.**
    실제로 11:16 KST 에 `^KS11` 이 `post` 로 기록됐다 — 그 시각 KRX 는 장중이다.
    """
    seen: list[str] = []

    def fake_session(ticker: str) -> str:
        seen.append(ticker)
        return "open" if ticker.endswith(".KS") else "post"

    captured = {}

    def fake_execute_values(cur, sql, rows, **kw):
        captured["rows"] = list(rows)

    with mock.patch("backend.db.is_available", return_value=True), \
         mock.patch("backend.db.get_conn"), \
         mock.patch("backend.services.market_calendar.market_session", fake_session), \
         mock.patch("psycopg2.extras.execute_values", fake_execute_values):
        lq.save_quotes(
            {"005930.KS": {"price": 71900.0}, "AAPL": {"price": 231.0}},
            source="test", session=None,
        )

    assert set(seen) == {"005930.KS", "AAPL"}, (
        f"market_session was asked about {seen} -- it must be consulted once "
        "per ticker, or one market gets the other's label. "
        "(전역 라벨은 한쪽에 대해 항상 틀린다.)"
    )
    labels = {row[0]: row[-2] for row in captured["rows"]}
    assert labels == {"005930.KS": "open", "AAPL": "post"}, f"labels={labels}"


def test_an_explicit_session_label_is_used_as_given():
    """문자열을 직접 주면 그대로 쓴다 — 티커와 무관하게 참인 값들(백필의 'closed')."""
    captured = {}

    with mock.patch("backend.db.is_available", return_value=True), \
         mock.patch("backend.db.get_conn"), \
         mock.patch("backend.services.market_calendar.market_session",
                    side_effect=AssertionError("명시적 라벨인데 티커별 조회를 했다")), \
         mock.patch("psycopg2.extras.execute_values",
                    lambda cur, sql, rows, **kw: captured.update(rows=list(rows))):
        lq.save_quotes({"AAPL": {"price": 231.0}}, source="backfill", session="closed")

    assert captured["rows"][0][-2] == "closed"


# ── 스트림: 못 쓰는 메시지는 버린다 ────────────────────────────────────────────

@pytest.mark.parametrize("msg, why", [
    ({}, "심볼이 없다"),
    ({"price": 231.0}, "심볼이 없다 (가격만 있음)"),
    ({"id": "AAPL"}, "가격이 없다"),
    ({"id": "AAPL", "price": float("nan")}, "가격이 NaN"),
    ({"id": "AAPL", "price": "n/a"}, "가격을 숫자로 못 읽는다"),
])
def test_unusable_stream_messages_are_dropped(msg, why):
    """쓸 수 없는 메시지는 버퍼에 쌓지 않는다.

    쌓으면 다음 flush 가 그 값을 DB 에 넣는다. 스트림은 초당 수십 건이라
    한 번 새면 계속 샌다.
    """
    stream = lq.QuoteStream()
    stream._on_message(msg)

    assert not stream._buf, f"{why}: 못 쓰는 메시지가 버퍼에 남았다 — {stream._buf}"


def test_a_usable_stream_message_is_buffered():
    """대조군 — 쓸 수 있는 메시지는 쌓인다.

    없으면 위 검사들은 "무엇도 안 쌓는" 구현으로 전부 통과한다. 그건 메시지를
    거르는 게 아니라 스트림이 죽은 것이다.
    """
    stream = lq.QuoteStream()
    stream._on_message({"id": "AAPL", "price": 231.0, "change_percent": 1.2})

    assert "AAPL" in stream._buf, f"쓸 수 있는 메시지가 버려졌다: {stream._buf}"
    assert stream._buf["AAPL"]["price"] == pytest.approx(231.0)


def test_stopping_a_stream_survives_a_broken_socket():
    """소켓 닫기가 터져도 `stop()` 은 조용히 끝난다.

    여기서 예외가 새면 종료 경로가 중간에 끊겨 스레드가 남는다.
    """
    stream = lq.QuoteStream()
    stream._ws = mock.Mock()
    stream._ws.close.side_effect = RuntimeError("socket already gone")

    stream.stop()          # 예외가 새면 여기서 실패한다

    assert stream._stop.is_set(), "정지 신호가 세워지지 않았다"


# ── 배치 갱신: 받을 것이 없으면 0 ──────────────────────────────────────────────

def test_round_the_clock_refresh_returns_zero_when_polling_yields_nothing():
    """폴링이 빈손이면 저장을 시도하지 않는다."""
    with mock.patch.object(lq, "poll_quotes", return_value={}):
        assert lq.refresh_round_the_clock() == 0


def test_tier1_refresh_returns_zero_when_the_universe_is_empty():
    """유니버스가 비면 0 이다 — 빈 배치로 DB 를 때리지 않는다."""
    with mock.patch("backend.services.ticker_universe.get_tier", return_value=[]):
        assert lq.refresh_tier1() == 0


# ── 백필: yfinance 가 죽는 것은 드문 일이 아니다 ───────────────────────────────

def test_backfill_logs_and_returns_zero_when_the_download_fails(caplog):
    """다운로드가 실패하면 **로그를 남기고** 0 을 돌려준다.

    yfinance 는 IP 단위로 레이트리밋을 걸고 그건 실제로 걸린다. 이 경로는
    예외적인 것이 아니라 정기적으로 돈다 — 로그가 없으면 종가가 왜 안 채워지는지
    알 방법이 없고, 화면에는 그냥 낡은 값이 남는다.
    """
    with mock.patch("yfinance.download", side_effect=RuntimeError("rate limited")), \
         caplog.at_level(logging.WARNING):
        assert lq.backfill_last_close(["AAPL"]) == 0

    assert caplog.records, (
        "the backfill download failed silently -- closes simply stop arriving "
        "and the screen keeps showing stale values with no explanation. "
        "(레이트리밋은 드문 일이 아니다.)"
    )


def test_backfill_returns_zero_for_an_empty_ticker_list():
    """받을 종목이 없으면 yfinance 를 부르지 않는다."""
    with mock.patch("yfinance.download",
                    side_effect=AssertionError("빈 목록인데 다운로드를 시도했다")):
        assert lq.backfill_last_close([]) == 0


def test_get_quotes_returns_an_empty_mapping_for_no_tickers():
    """빈 요청에는 빈 결과다 — DB 도 네트워크도 건드리지 않는다."""
    with mock.patch("backend.db.get_conn",
                    side_effect=AssertionError("빈 요청인데 DB 를 잡았다")):
        assert lq.get_quotes([]) == {}


def test_starting_a_stream_with_no_tickers_opens_no_socket():
    """구독할 것이 없으면 웹소켓을 열지 않는다.

    열어 두면 아무것도 받지 않는 연결과 스레드 두 개가 프로세스에 남는다.
    """
    stream = lq.QuoteStream()

    with mock.patch("yfinance.WebSocket",
                    side_effect=AssertionError("구독할 티커가 없는데 소켓을 열었다")):
        stream.start([])
        # `if t` 로 falsy 만 거른다. 공백 문자열(`"  "`)은 살아남는다 —
        # `get_quotes` 는 `.strip()` 을 하는데 여기는 안 한다. 지금 호출자가
        # 공백을 넘기지 않아 문제가 없으므로 실제 동작 그대로 적어 둔다.
        stream.start([None, ""])

    assert stream._ws is None
    assert not stream._threads, "빈 구독인데 스레드가 떴다"


def test_a_failed_flush_is_logged_and_the_loop_keeps_going(caplog):
    """flush 가 실패해도 루프는 살아 있고, **로그가 남는다.**

    여기서 예외가 새면 스트림 스레드가 죽고 실시간이 조용히 멈춘다. 화면은
    마지막으로 받은 값을 계속 보여 주므로 **멈춘 것이 보이지 않는다.**
    """
    stream = lq.QuoteStream()
    stream.flush_interval = 0.0          # 기다리지 않고 바로 한 바퀴
    stream._buf = {"AAPL": {"price": 231.0}}

    calls = {"n": 0}

    def failing_save(*a, **kw):
        calls["n"] += 1
        stream._stop.set()               # 한 바퀴만 돌고 나온다
        raise RuntimeError("db gone")

    with mock.patch.object(lq, "save_quotes", failing_save), \
         caplog.at_level(logging.WARNING):
        stream._flush_loop()

    assert calls["n"] == 1, "flush 가 시도되지 않았다 — 이 검사의 전제가 없다"
    assert caplog.records, (
        "a failed flush left no log -- the stream stops delivering and the "
        "screen keeps showing the last value it got. (멈춘 것이 안 보인다.)"
    )


def test_seeding_closing_prices_without_a_database_returns_zero():
    """DB 가 없으면 조용히 0 이다 — 예외로 스케줄러를 죽이지 않는다."""
    with mock.patch("backend.db.is_available", return_value=False), \
         mock.patch("backend.db.get_conn",
                    side_effect=AssertionError("DB 가 없는데 커넥션을 잡았다")):
        assert lq.seed_closing_prices() == 0

"""
`save_prices_to_db` 의 캘린더 가드 — **양방향으로** 잰다.

이 함수는 `market_prices` 의 **유일한 SQL 기록 지점**이다(§1.6). 그래서 어떤
호출자가 ffill 된 프레임을 넘겨도 위조 종가가 영구 저장되지 않는다. §1.6 은
"저장 금지" 만 말하고 있어서 한 방향으로만 읽히기 쉬운데, **오늘 운영 사고는
반대 방향이었다.**

    09-10(목) 한국 시세가 운영에서 하루치 통째로 빠졌다.
    캘린더에 구멍이 나 그날이 휴장으로 읽혔고, 가드가 날짜 선후가 아니라
    **개장 여부**를 보므로 다시 받아도 계속 거부됐다.

막아야 할 것을 막는 것만큼 **통과시켜야 할 것을 통과시키는 것**이 중요하다.
한쪽만 재면 "아무것도 저장하지 않는" 구현이 가드로 보인다.

## 캘린더를 고정한다

`is_kr_trading_day` 는 관측 기반이라 DB 캐시와 바깥 소스를 본다. 그대로 두면
이 테스트의 결과가 **이 창의 DB 상태와 실행 시각**에 달린다 — 다른 창에서는
다른 분기가 돌고, 내일 돌리면 또 달라진다. 그래서 캘린더 함수를 명시적으로
갈아끼우고 날짜를 직접 정한다.
"""
from __future__ import annotations

import logging
from datetime import date
from unittest import mock

import pandas as pd
import pytest

from backend.db import market_cache as mc

_US = "__TEST_US__"
_KR = "__TEST_KR__.KS"
_ALWAYS = "__TEST_BTC__-USD"

# 고정한 캘린더. 미국은 09-10 까지, 한국도 09-10 까지 확정됐다고 둔다.
_US_LAST = date(2026, 9, 10)
_KR_LAST = date(2026, 9, 10)
_SESSIONS = {date(2026, 9, 8), date(2026, 9, 9), date(2026, 9, 10),
             date(2026, 9, 11)}


@pytest.fixture
def fixed_calendar():
    """거래일 판단을 고정한다 — 실행 시각과 이 창의 DB 상태에 안 흔들리게."""
    from backend.services import market_calendar as cal

    with mock.patch.object(cal, "is_us_trading_day", lambda d: d in _SESSIONS), \
         mock.patch.object(cal, "is_kr_trading_day", lambda d: d in _SESSIONS), \
         mock.patch.object(cal, "last_completed_session", lambda *a, **kw: _US_LAST), \
         mock.patch.object(cal, "last_completed_kr_session", lambda *a, **kw: _KR_LAST), \
         mock.patch.object(cal, "uses_us_session_calendar",
                           lambda t: str(t) == _US), \
         mock.patch.object(cal, "uses_kr_session_calendar",
                           lambda t: str(t) == _KR):
        yield


@pytest.fixture
def written(live_db):
    """이 테스트가 쓴 행만 읽고, 앞뒤로 지운다."""
    from backend.db import get_conn

    def purge():
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM market_prices WHERE ticker LIKE %s", ("__TEST%",))

    def read(ticker):
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT price_date FROM market_prices WHERE ticker=%s ORDER BY 1",
                    (ticker,),
                )
                return [r[0] for r in cur.fetchall()]

    purge()
    yield read
    purge()


def _save(ticker: str, days: list[date]):
    idx = pd.to_datetime([d.isoformat() for d in days])
    mc.save_prices_to_db(pd.DataFrame({ticker: [100.0 + i for i in range(len(days))]},
                                      index=idx))


# ── 통과시켜야 할 것 — 오늘 사고의 방향 ────────────────────────────────────────

def test_a_confirmed_korean_session_is_written(fixed_calendar, written):
    """확정된 한국 거래일 종가는 저장된다.

    09-10 이 정확히 이 경우였다. 캘린더에 구멍이 나 휴장으로 읽히자 가드가
    거부했고, **다시 받아도 계속 거부돼** 그 하루가 영영 안 채워졌다.
    """
    _save(_KR, [_KR_LAST])

    assert written(_KR) == [_KR_LAST], (
        f"a confirmed KRX close was rejected: {written(_KR)} -- the guard asks "
        "whether the market was open, not whether the date is newer, so a "
        "rejected day never gets filled in on a later attempt. "
        "(막지 말아야 할 것을 막으면 그 하루는 영영 안 들어온다.)"
    )


def test_a_confirmed_us_session_is_written(fixed_calendar, written):
    """미국 쪽 대조군 — 확정 거래일은 저장된다."""
    _save(_US, [_US_LAST])

    assert written(_US) == [_US_LAST]


def test_a_round_the_clock_asset_is_written_on_any_day(fixed_calendar, written):
    """24시간 자산은 주말이든 오늘이든 저장된다.

    암호화폐·환율·선물에는 '마감' 이 없다. 증시 캘린더로 거르면 주말 이틀이
    통째로 비고, 그 구간이 차트에서 사라진다.
    """
    weekend_and_today = [date(2026, 9, 12), date(2026, 9, 13), date(2026, 9, 14)]
    _save(_ALWAYS, weekend_and_today)

    assert written(_ALWAYS) == weekend_and_today, (
        f"a 24/7 asset was filtered by an exchange calendar: {written(_ALWAYS)}"
    )


# ── 막아야 할 것 ──────────────────────────────────────────────────────────────

def test_a_non_session_day_is_rejected(fixed_calendar, written):
    """캘린더에 없는 날은 저장하지 않는다 — 주말·공휴일."""
    _save(_US, [date(2026, 9, 12)])          # 토요일, _SESSIONS 에 없다

    assert written(_US) == [], f"비거래일 행이 저장됐다: {written(_US)}"


def test_a_close_that_is_not_final_yet_is_rejected(fixed_calendar, written):
    """종가가 확정되지 않은 날은 저장하지 않는다.

    장중 부분 봉이 그날의 확정 종가로 박히면, 이후 수집이 같은 날짜를 다시
    받아도 `ON CONFLICT` 로 덮이기 전까지 그 값이 사실로 통한다.
    """
    _save(_US, [date(2026, 9, 11)])          # 개장일이지만 last_completed 보다 뒤

    assert written(_US) == [], f"미확정 당일 행이 저장됐다: {written(_US)}"


def test_a_korean_intraday_bar_is_rejected(fixed_calendar, written):
    """한국 종목도 같은 가드를 받는다.

    `.KS`/`.KQ` 는 미국 캘린더를 안 따른다는 이유로 가드를 통째로 건너뛰고
    있었다. 그러면 09:00~15:30 에 수집한 부분 봉이 확정 종가로 박힌다.
    """
    _save(_KR, [date(2026, 9, 11)])          # 개장일이지만 아직 확정 전

    assert written(_KR) == [], f"한국 장중 부분 봉이 저장됐다: {written(_KR)}"


def test_a_mixed_frame_keeps_the_good_rows(fixed_calendar, written):
    """한 프레임에 통과할 행과 막힐 행이 섞이면 통과할 것만 남는다.

    전부 버리면 하루 때문에 2년치가 날아가고, 전부 저장하면 가드가 없는
    것과 같다.
    """
    _save(_US, [date(2026, 9, 9), date(2026, 9, 10), date(2026, 9, 11)])

    assert written(_US) == [date(2026, 9, 9), date(2026, 9, 10)], written(_US)


# ── 쓰기가 실패해도 호출자를 죽이지 않는다 ─────────────────────────────────────

def test_a_failed_write_is_logged_and_swallowed(fixed_calendar, caplog):
    """DB 쓰기가 터져도 예외가 새지 않고 **로그가 남는다.**

    이 함수는 수집 루프 한가운데서 불린다. 예외가 새면 그 배치의 나머지
    종목이 통째로 날아간다. 다만 조용히 넘기면 "수집은 돌았는데 아무것도
    안 들어온" 상태를 설명할 단서가 없다.
    """
    with mock.patch.object(mc, "get_conn", side_effect=RuntimeError("db gone")), \
         caplog.at_level(logging.WARNING):
        _save(_US, [_US_LAST])               # 예외가 새면 여기서 실패한다

    assert caplog.records, (
        "the write failed and nothing was logged -- collection appears to run "
        "while nothing arrives, with no trace connecting the two. "
        "(수집은 돌았는데 아무것도 안 들어온 상태가 설명되지 않는다.)"
    )


def test_nothing_to_write_touches_no_connection(fixed_calendar):
    """저장할 행이 없으면 커넥션을 잡지 않는다.

    전 행이 가드에 걸린 프레임이 그 경우다. 커넥션을 잡으면 수집 주기마다
    빈 트랜잭션이 하나씩 늘어난다.
    """
    with mock.patch.object(mc, "get_conn",
                           side_effect=AssertionError("쓸 행이 없는데 커넥션을 잡았다")):
        _save(_US, [date(2026, 9, 12)])      # 주말뿐
        mc.save_prices_to_db(pd.DataFrame())

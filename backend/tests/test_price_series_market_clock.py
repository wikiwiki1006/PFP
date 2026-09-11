"""
'오늘' 은 그 종목이 상장된 거래소 기준이다 — 서버 기준도, 뉴욕 기준도 아니다.

`price_series` 는 '오늘' 을 `now_et()` 로만 정의했다. 한국 종목에는 틀린다:
KRX 정규장 09:00~15:30 KST 는 ET 로 **전날** 20:00~02:30 이라, **한국장이
열려 있는 내내 ET 날짜가 하루 뒤처져 있다.**

그래서 장중 실시간 가격이 들어오면 `daily_change` 가 '오늘 이전의 마지막
종가' 를 ET 날짜로 잘랐고, 어제(KST) 종가가 '오늘' 로 분류돼 전일 종가에서
빠졌다. 실측 (2026-09-11 10:35 KST, 005930.KS):

    프레임   09-08 269,500 · 09-09 269,500 · 09-10 269,000
    실시간   258,500
    결과     prev_close=269,500 (09-09)  chg=-4.08%  as_of=09-10   ← 이틀치
    정답     prev_close=269,000 (09-10)  chg=-3.90%  as_of=09-11

## 이 테스트는 시각을 고정해야만 의미가 있다

이 결함은 **ET 와 KST 의 날짜가 서로 다른 시간대에만** 나타난다. 하루의 절반은
두 날짜가 같아서 어느 쪽으로 구현하든 통과한다 — 시각을 고정하지 않으면 이
테스트는 **절반의 확률로 아무것도 지키지 않는다.** 그것도 조용히.

그래서 모든 케이스가 `now` 를 명시적으로 넘기고, 맨 아래
`test_the_chosen_instant_actually_splits_the_two_dates` 가 **고른 시각이 정말
두 날짜를 가르는지**를 따로 단언한다. 누군가 시각을 옮기면 그 테스트가 먼저
깨진다 — 나머지가 조용히 무력해지는 대신.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from backend.services.market_calendar import KST
from backend.services.price_series import _market_now, daily_change

# KRX 장중. KST 로 09-11 10:35, 같은 순간이 ET 로는 09-10 21:35 이다.
_KST_MORNING = datetime(2026, 9, 11, 10, 35, tzinfo=timezone(timedelta(hours=9)))

# 두 날짜가 **같은** 순간. 대조군으로 쓴다.
_KST_EVENING = datetime(2026, 9, 11, 23, 30, tzinfo=timezone(timedelta(hours=9)))

# 9월은 서머타임(EDT). 이 스위트가 고른 날짜에만 유효하다.
_ET = timezone(timedelta(hours=-4))

_KR = "005930.KS"
_US = "AAPL"

_FRAME = pd.DataFrame(
    {
        _KR: [269_500.0, 269_500.0, 269_000.0],
        _US: [230.0, 231.0, 232.0],
    },
    index=pd.to_datetime(["2026-09-08", "2026-09-09", "2026-09-10"]),
)

_LIVE_KR = 258_500.0


# ── 전제: 고른 시각이 정말 두 날짜를 가르는가 ──────────────────────────────────

def test_the_chosen_instant_actually_splits_the_two_dates():
    """이 스위트의 전제를 따로 단언한다.

    두 날짜가 같은 순간을 골라 두면 아래 검사들은 **틀린 구현으로도 전부
    통과한다.** 그 상태는 출력에서 통과와 구별되지 않으므로, 전제 자체를
    검사로 만든다.

    전제는 **시간대 산술로만** 확인한다 — 검사 대상인 `_market_now` 로
    확인하면, 그 함수가 틀렸을 때 전제까지 같이 틀려서 아무것도 못 가른다.
    """
    assert KST is not None, "KST 타임존을 못 읽었다 — 이 스위트가 성립하지 않는다"

    seoul_date = _KST_MORNING.astimezone(timezone(timedelta(hours=9))).date()
    newyork_date = _KST_MORNING.astimezone(_ET).date()

    assert seoul_date != newyork_date, (
        f"the chosen instant falls on the same calendar day in both cities "
        f"({seoul_date}) -- every test below would pass with either "
        "implementation. Pick a moment inside the KRX session. "
        "(시각을 잘못 고르면 이 스위트가 조용히 무력해진다.)"
    )
    assert seoul_date == datetime(2026, 9, 11).date()
    assert newyork_date == datetime(2026, 9, 10).date()


# ── 본론 ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("zone", ["KST", "UTC", "ET"], ids=lambda z: f"now={z}")
def test_kr_intraday_uses_the_seoul_date_for_today(zone):
    """한국장 중 한국 종목의 '오늘' 은 KST 날짜다.

    ET 날짜로 자르면 09-10 종가가 '오늘' 로 분류돼 전일 종가에서 빠지고,
    **이틀치 변동이 오늘 등락으로** 나간다.

    **같은 순간을 세 시간대로 넘긴다.** KST 로만 넘기면 수정 전 코드도
    통과한다 — 그쪽은 `now` 를 그대로 돌려주므로 이미 서울 날짜이기 때문이다.
    처음에 KST 만 썼다가 수정 전 코드에 대고 돌려 보고서야 알았다. 변환을
    하는지 보려면 **변환이 필요한 형태로** 줘야 한다.
    """
    moment = {
        "KST": _KST_MORNING,
        "UTC": _KST_MORNING.astimezone(timezone.utc),
        "ET": _KST_MORNING.astimezone(_ET),
    }[zone]

    dc = daily_change(_FRAME, _KR, live_price=_LIVE_KR, now=moment)

    assert dc is not None, "장중 실시간이 있는데 변동을 못 구했다"
    assert dc.prev_close == pytest.approx(269_000.0), (
        f"prev_close={dc.prev_close} -- with the Seoul date as today, the last "
        "close before it is 09-10 (269,000). Using the New York date makes it "
        "09-09 (269,500) and reports two days of movement as one. "
        "(ET 날짜로 자르면 어제 종가가 '오늘' 로 분류된다.)"
    )
    assert dc.chg_pct == pytest.approx(-3.9033, abs=1e-3), f"chg_pct={dc.chg_pct}"
    assert dc.as_of.date() == datetime(2026, 9, 11).date(), (
        f"as_of={dc.as_of} -- the live price was taken during the Seoul "
        "session, so it belongs to that day, not the previous New York one."
    )


def test_kr_ticker_converts_an_aware_timestamp_whatever_zone_it_arrives_in():
    """한국 종목은 어떤 시간대로 받아도 서울 날짜로 변환한다.

    서버가 어디에 있든 결과가 같아야 한다 — Cloud Run 은 UTC, 개발 기기는
    KST 다. 같은 순간을 UTC 로 줘도 09-11 이 나와야 한다.
    """
    for label, moment in (
        ("KST", _KST_MORNING),
        ("UTC", _KST_MORNING.astimezone(timezone.utc)),
        ("ET", _KST_MORNING.astimezone(_ET)),
    ):
        assert _market_now(_KR, moment).date() == datetime(2026, 9, 11).date(), (
            f"{label} 로 같은 순간을 줬을 때 서울 날짜가 아니다"
        )


# ── 반대 방향의 같은 결함 — 아직 있다 ──────────────────────────────────────────

_US_ASYMMETRY = (
    "_market_now converts an explicit `now` into Seoul time for a KR ticker "
    "but returns it untouched for a US one (`return now or now_et()`). Hand it "
    "a KST-aware timestamp and a US ticker takes the Seoul date -- the mirror "
    "image of the bug this module just fixed. No caller passes an explicit "
    "`now` today, so nothing is broken right now; the moment one does (a mixed "
    "US+KR portfolio sharing a single timestamp is the obvious case, and "
    "portfolio_daily_change already threads one `now` through every holding), "
    "US tickers silently inherit whatever zone the caller used. Convert on "
    "both branches. (owner: unassigned -- price_series.py is not in the layout "
    "table; integration to assign, per the ledger procedure.)"
)


@pytest.mark.xfail(strict=True, reason=_US_ASYMMETRY)
def test_us_ticker_converts_an_aware_timestamp_too():
    """미국 종목도 받은 순간을 뉴욕 시간대로 변환해야 한다.

    지금은 KR 분기만 변환하고 US 분기는 `now` 를 그대로 돌려준다. 대조군이
    없으면 "무조건 서울 기준" 이라는 구현으로도 위 테스트들이 전부 통과한다 —
    반대 방향으로 똑같이 틀린 것이다.
    """
    assert _market_now(_US, _KST_MORNING).date() == datetime(2026, 9, 10).date(), (
        "a US ticker took the Seoul date from an explicitly supplied timestamp"
    )


@pytest.mark.xfail(strict=True, reason=_US_ASYMMETRY)
def test_us_intraday_still_uses_the_new_york_date():
    """같은 순간의 미국 종목 일변동은 뉴욕 날짜를 기준으로 잘라야 한다."""
    dc = daily_change(_FRAME, _US, live_price=240.0, now=_KST_MORNING)

    assert dc is not None
    assert dc.as_of.date() == datetime(2026, 9, 10).date(), (
        f"as_of={dc.as_of} -- a US ticker must not inherit the Seoul date."
    )


def test_outside_the_session_both_exchanges_agree(monkeypatch):
    """두 날짜가 같은 순간에는 결과도 같다 — 이 검사는 결함을 못 잡는다.

    그 사실을 명시해 둔다. 이런 시각만 골라 테스트를 짜면 통과가 아무것도
    증명하지 못하는데, 출력에서는 통과와 같아 보인다.
    """
    kr_date = _market_now(_KR, _KST_EVENING).date()
    us_date = _market_now(_US, _KST_EVENING).date()

    assert kr_date == us_date == datetime(2026, 9, 11).date(), (
        f"KR={kr_date} US={us_date} -- 이 시각은 두 날짜가 같아야 한다 "
        "(KST 23:30 = ET 10:30 같은 날). 아니면 위 전제 설명이 낡았다."
    )

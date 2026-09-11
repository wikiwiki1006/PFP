"""
현금 입출금을 세 곳이 따로 구현하고 있다 — **그리고 일치하는 이유는
라우터가 음수를 거부하기 때문이다.**

    backend/services/cash_ledger.py:16        DEPOSIT → +abs(q) · WITHDRAW → -abs(q)
    portfolio_calculator.build_equity_curve    running_cash += q / -= q
    portfolio_calculator.build_return_pct_curve  running_cash += q / -= q

앞의 하나만 `abs` 를 쓴다. 양수만 들어오는 동안에는 세 답이 같지만, 음수가
한 번 통과하면 갈린다. 실측 — `DEPOSIT 1000` 뒤 `WITHDRAW -500`:

    cash_ledger          1000 - 500 =  500
    build_equity_curve   1000 + 500 = 1500     ← 출금이 잔고를 늘린다
    build_return_pct     1000 + 500 = 1500

**코드를 통일하지 않는다.** `abs` 를 떼면 음수가 들어올 때 셋 다 틀리고,
셋 다 `abs` 를 붙이면 잘못된 입력을 조용히 받아들인다. 지금 맞는 답은
"음수는 애초에 안 들어온다" 이고, 그걸 보장하는 것은 라우터의 한 줄이다.

그래서 이 파일은 **그 의존을 말한다.** 나중에 누가 음수를 허용하면(현금
조정 기능 같은 것) 아래 검사가 먼저 빨개진다. 목록에만 적어 두면 그때
아무도 안 본다.
"""
from __future__ import annotations

from unittest import mock

import pandas as pd
import pytest
from fastapi import HTTPException

from backend.models.portfolio import AddTradeRequest
from backend.routers import portfolio as prt
from backend.services import portfolio_calculator as pc
from backend.services.cash_ledger import _cash_event_delta

UID = "__TEST_U_cash"
MARKET = "US"

_IDX = pd.bdate_range("2026-09-01", periods=5)
# 보유 종목이 없으므로 자산 곡선은 곧 현금 잔고다 — 세 경로를 같은 축에서 읽는다.
_PRICES = pd.DataFrame({"AAPL": [100.0] * 5}, index=_IDX)


def _log(*events):
    return [{"date": d, "ticker": "CASH", "type": t, "q": q, "price": 1}
            for d, t, q in events]


def _by_ledger(events):
    return sum(_cash_event_delta(t, q) for _, t, q in events)


def _by_equity_curve(events):
    curve = pc.build_equity_curve({}, _log(*events), _PRICES)
    return None if curve.empty else round(float(curve.iloc[-1]), 6)


def _by_return_curve(events):
    _, _, _, cash_events, _ = pc.build_return_pct_curve({}, _log(*events), _PRICES)
    return round(sum(cash_events.values()), 6)


# ── 라우터가 음수를 막는다 — 이 한 줄에 셋이 기대고 있다 ──────────────────────

@pytest.fixture
def account(live_db):
    """거래를 넣을 수 있는 계정 하나."""
    from backend.db import get_conn
    from backend.db import users_repo as ur

    def purge():
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM trade_log WHERE user_id=%s", (UID,))
            cur.execute("DELETE FROM holdings  WHERE user_id=%s", (UID,))
            cur.execute("DELETE FROM users     WHERE id=%s", (UID,))

    purge()
    assert ur.upsert_user(UID, email=f"{UID}@example.com") is True
    with mock.patch.object(prt, "_fetch_sector", lambda t: "Technology"):
        yield
    purge()


@pytest.mark.parametrize("bad_q, why", [
    (-500, "음수"),
    (0, "0"),
    (float("nan"), "NaN"),
    (float("inf"), "무한대"),
])
def test_the_router_refuses_a_quantity_that_is_not_a_positive_number(account, bad_q, why):
    """수량이 양수가 아니면 거래가 기록되지 않는다.

    **세 구현이 일치하는 것은 이 한 줄 덕분이다.** 여기를 넓히면 아래
    `test_the_three_paths_disagree_...` 가 말하는 상태가 실제로 생긴다.
    """
    with pytest.raises(HTTPException) as raised:
        prt._add_trade_locked(
            AddTradeRequest(ticker="CASH", type="DEPOSIT", q=bad_q, price=1,
                            date="2026-09-02"),
            UID, MARKET,
        )
    assert raised.value.status_code == 400, f"{why} 가 400 이 아니다"


def test_the_router_accepts_a_normal_deposit(account):
    """대조군 — 정상 입금은 들어간다.

    없으면 위 검사는 "모든 입금을 거부한다" 는 구현으로도 통과하고, 그러면
    입금 기능이 통째로 죽는다.
    """
    from backend.db.portfolio_repo import get_trade_log

    prt._add_trade_locked(
        AddTradeRequest(ticker="CASH", type="DEPOSIT", q=500, price=1,
                        date="2026-09-02"),
        UID, MARKET,
    )
    assert [r["type"] for r in get_trade_log(UID, market=MARKET)] == ["DEPOSIT"]


# ── 양수만 들어오는 동안에는 세 답이 같다 ──────────────────────────────────────

_VALID = [
    pytest.param([("2026-09-02", "DEPOSIT", 500)], id="입금"),
    pytest.param([("2026-09-02", "DEPOSIT", 500),
                  ("2026-09-03", "WITHDRAW", 200)], id="입금-뒤-출금"),
    pytest.param([("2026-09-02", "DEPOSIT", 1000),
                  ("2026-09-03", "DEPOSIT", 250),
                  ("2026-09-04", "WITHDRAW", 400)], id="여러-번"),
]


@pytest.mark.parametrize("events", _VALID)
def test_the_three_cash_paths_agree_on_valid_quantities(events):
    """세 구현이 같은 잔고를 낸다.

    기대값을 상수로 적지 않고 **서로 비교한다** — 한쪽의 상수가 틀리면
    검사가 조용히 반대로 답한다.
    """
    ledger = _by_ledger(events)
    equity = _by_equity_curve(events)
    pct = _by_return_curve(events)

    assert ledger == equity == pct, (
        f"the three cash implementations disagree on a normal history: "
        f"cash_ledger={ledger} equity_curve={equity} return_curve={pct} -- "
        "the same deposit means different things in different screens."
    )


def test_the_comparison_would_notice_a_difference():
    """대조군 — 세 값이 실제로 움직인다.

    위 비교는 셋 다 0 을 돌려줘도 통과한다. 그러면 "일치한다" 가 "아무것도
    안 센다" 와 같아진다.
    """
    small = [("2026-09-02", "DEPOSIT", 500)]
    large = [("2026-09-02", "DEPOSIT", 900)]

    assert _by_ledger(small) != _by_ledger(large)
    assert _by_equity_curve(small) != _by_equity_curve(large)
    assert _by_return_curve(small) != _by_return_curve(large)


# ── 음수가 통과하면 갈린다 — 그래서 위 가드가 중요하다 ────────────────────────

def test_the_three_paths_disagree_the_moment_a_negative_quantity_gets_through():
    """음수 수량이 들어오면 세 답이 갈린다 — **출금이 잔고를 늘린다.**

    지금은 라우터가 막아 도달하지 않는다. 이 검사는 "고쳐야 할 결함" 이
    아니라 **위 가드가 무엇을 떠받치고 있는지**를 적어 둔 것이다.

    음수를 허용하는 변경(현금 조정 기능 등)을 넣으면 이 검사가 먼저
    빨개진다. 그때 할 일은 이 검사를 지우는 것이 아니라 **세 구현을 먼저
    맞추는 것**이다 — `abs` 를 떼면 셋 다 틀리고, 셋 다 붙이면 잘못된
    입력을 조용히 받아들인다.
    """
    events = [("2026-09-02", "DEPOSIT", 1000), ("2026-09-03", "WITHDRAW", -500)]

    ledger = _by_ledger(events)
    equity = _by_equity_curve(events)

    assert ledger != equity, (
        f"the three paths now agree on a negative quantity ({ledger}) -- if "
        "that is deliberate, this test and the note above are stale. If it is "
        "accidental, check that all three use the same convention."
    )
    assert equity > ledger, (
        f"expected the curve to read a negative withdrawal as an inflow "
        f"(equity={equity} vs ledger={ledger}); the direction changed."
    )


def test_a_negative_deposit_is_dropped_by_one_path_and_kept_by_another():
    """같은 음수라도 어느 필드가 사라지는지가 경로마다 다르다.

    `DEPOSIT -500` 은 원장에서 **+500**(abs), 자산 곡선에서 0, TWRR 의
    현금흐름 기록에서는 **아예 사라진다**. 셋 중 둘이 아니라 셋이 다르다.
    그 사실을 적어 두지 않으면 "부호만 맞추면 된다" 로 읽힌다.
    """
    events = [("2026-09-02", "DEPOSIT", -500)]

    assert _by_ledger(events) == 500, "원장은 abs 로 부호를 뒤집는다"
    assert _by_return_curve(events) == 0, (
        "TWRR 의 현금흐름 기록에서 이 사건이 사라지지 않았다 — 설명이 낡았다"
    )

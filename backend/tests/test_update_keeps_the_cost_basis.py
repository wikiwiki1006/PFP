"""
수량 정정(UPDATE)이 **취득원가를 바꾸지 않는가**.

같은 사건을 다루는 자리가 넷인데 표현이 둘로 갈려 있었다.

    routers/portfolio.py           보유의 `avg` — **주당**
    portfolio_calculator (재생 둘)  running_avg / running_cost — **주당**
    cash_ledger (재생)             total_cost — **합계**

앞의 셋은 "손대지 않는다" 가 곧 보존이었는데, 합계를 들고 있는 한 곳에서는
같은 한 줄(`qty = q`)이 정반대를 뜻했다 — `avg = total_cost / qty` 가 수량에
반비례해 움직인다. 10주를 100에 사고 수량을 20으로 정정하면 평단이 50 이
되고, 5로 줄이면 200 이 된다. **사용자는 주식 수만 고쳤는데 취득원가가
바뀐다.**

## 무엇을 재는가 — 기대값을 안 적는다

두 가지를 잰다. 첫째만 있으면 **양쪽이 똑같이 틀려도 통과**하므로 둘 다 둔다.

  ① 즉시 반영 경로와 재생 경로가 **같은 답**을 낸다.
     같은 이력을 두 방법으로 처리한 결과를 서로 비교한다. 어느 쪽의
     기대값도 상수로 적지 않는다 — 상수가 틀리면 검사가 조용히 반대로
     답한다.

  ② 수량만 바꾸는 UPDATE 는 **평단을 안 바꾼다.**
     UPDATE 직전의 평단과 직후의 평단을 비교한다. 이것도 상수가 없고,
     ①이 못 잡는 "둘 다 같은 방향으로 틀림" 을 잡는다.

## 즉시 반영 경로는 라우터 안에 인라인이다

그래서 `_add_trade_locked` 를 직접 부른다. 그 함수가 거래를 기록하면서
보유를 갱신하는 **그 코드**다. 재생은 `_recalculate_holding_from_trades` 가
같은 거래 기록을 다시 읽어 돌린다 — 사용자가 옛 거래를 하나 고치거나
지우면 실제로 이 경로가 돈다.

실DB 를 쓴다. 두 경로 다 `holdings`·`trade_log` 를 실제로 읽고 쓰는 것이
재려는 대상이고, 저장소를 모킹하면 내가 SQL 을 어떻게 읽었는지만 검증된다.
"""
from __future__ import annotations

from unittest import mock

import pytest

from backend.db.portfolio_repo import get_holdings
from backend.models.portfolio import AddTradeRequest
from backend.routers import portfolio as prt
from backend.services import cash_ledger

UID = "__TEST_U_trades"
TICKER = "ZZTEST"
MARKET = "US"


@pytest.fixture
def account(live_db):
    """거래 이력이 비어 있는 계정 하나. 앞뒤로 지운다."""
    from backend.db import get_conn
    from backend.db import users_repo as ur

    def purge():
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM trade_log WHERE user_id=%s", (UID,))
            cur.execute("DELETE FROM holdings  WHERE user_id=%s", (UID,))
            cur.execute("DELETE FROM users     WHERE id=%s", (UID,))

    purge()
    assert ur.upsert_user(UID, email=f"{UID}@example.com") is True
    # 새 종목의 섹터 자동 조회는 yfinance 를 부른다. 이 검사가 재려는 것과
    # 무관하고, 백그라운드 스레드라 실패해도 조용하다 — 아예 안 나가게 한다.
    with mock.patch.object(prt, "_fetch_sector", lambda t: "Technology"):
        # 현금을 먼저 넣는다. 매수는 잔고를 확인하고 모자라면 400 이다 —
        # 그 검사를 우회하면 이 파일이 실제 매수 경로를 안 지나게 된다.
        prt._add_trade_locked(
            AddTradeRequest(ticker="CASH", type="DEPOSIT", q=1_000_000, price=1,
                            date="2026-08-01"),
            UID, MARKET,
        )
        yield
    purge()


def _apply(trade_type: str, q: float, price: float | None = None, day: str = "2026-09-01"):
    """거래 하나를 **실제 경로로** 넣는다 — 기록 + 즉시 반영."""
    prt._add_trade_locked(
        AddTradeRequest(ticker=TICKER, type=trade_type, q=q, price=price, date=day),
        UID, MARKET,
    )


def _held():
    row = get_holdings(UID, market=MARKET).get(TICKER)
    return None if row is None else (round(float(row["q"]), 6),
                                     round(float(row["avg"]), 4))


def _replayed():
    """거래 기록을 다시 읽어 재계산한 결과."""
    cash_ledger._recalculate_holding_from_trades(TICKER, UID, market=MARKET)
    return _held()


# 날짜를 하루씩 밀어 재생 순서가 입력 순서와 같게 한다. 같은 날이면
# `sorted(key=(date, id))` 의 두 번째 키에 기대게 되는데, 그건 이 검사가
# 재려는 것이 아니다.
_HISTORIES = [
    pytest.param([("ADD", 10, 100.0), ("UPDATE", 20, None)], id="수량을-늘리는-정정"),
    pytest.param([("ADD", 10, 100.0), ("UPDATE", 5, None)], id="수량을-줄이는-정정"),
    pytest.param([("ADD", 10, 100.0), ("ADD", 10, 200.0), ("UPDATE", 30, None)],
                 id="두-번-사고-정정"),
    pytest.param([("ADD", 10, 100.0), ("UPDATE", 20, None), ("ADD", 10, 300.0)],
                 id="정정-뒤-매수"),
    pytest.param([("ADD", 10, 100.0), ("UPDATE", 20, None), ("SOLD", 10, 150.0)],
                 id="정정-뒤-매도"),
    pytest.param([("ADD", 10, 100.0), ("ADD", 10, 200.0)], id="정정-없음-대조군"),
]


@pytest.mark.parametrize("history", _HISTORIES)
def test_the_replay_agrees_with_what_the_user_already_saw(account, history):
    """재생이 화면에 떠 있던 값과 같은 답을 낸다.

    사용자가 옛 거래를 하나 고치거나 지우면 재생이 돈다. 두 경로가 다르면
    **손대지 않은 종목의 평단이 그때 바뀐다** — 사용자는 방금 고친 거래
    때문이라고 읽지 않는다. 어느 한쪽의 기대값을 적어 두면 그 상수가 틀렸을 때
    검사가 조용히 반대로 답하므로, 두 결과를 서로 비교한다.
    """
    for i, (ttype, q, price) in enumerate(history):
        _apply(ttype, q, price, day=f"2026-09-{i + 1:02d}")

    immediate = _held()
    assert immediate is not None, "즉시 반영 경로가 보유를 안 남겼다 — 전제가 없다"

    assert _replayed() == immediate, (
        f"the replay disagrees with what the user was already seeing: "
        f"replay={_replayed()} vs immediate={immediate} -- editing an unrelated "
        f"old trade silently rewrites this holding's cost basis."
    )


@pytest.mark.parametrize("new_qty", [20, 5])
@pytest.mark.parametrize("replay", [False, True], ids=["즉시반영", "재생"])
def test_correcting_only_the_quantity_leaves_the_cost_basis_alone(account, new_qty, replay):
    """수량만 고치면 **주당** 취득원가는 그대로다 — 양쪽 경로 각각.

    위 검사만 있으면 두 경로가 **같은 방향으로 틀려도** 통과한다. 여기서는
    UPDATE 직전과 직후를 비교하므로 그 경우가 잡힌다. 이것도 상수가 없다 —
    "무엇이어야 하는가" 가 아니라 "바뀌지 않아야 한다" 를 잰다.

    수량을 늘리는 경우와 줄이는 경우를 **둘 다** 잰다. 합계를 들고 있으면
    평단이 수량에 반비례하므로 한 방향만 재면 부호를 놓친다.
    """
    _apply("ADD", 10, 100.0, day="2026-09-01")
    before = _held()
    assert before and before[1] > 0, "전제: 평단이 잡혀 있다"

    _apply("UPDATE", new_qty, None, day="2026-09-02")
    after = _replayed() if replay else _held()

    assert after is not None, "정정 후 보유가 사라졌다"
    assert after[0] == new_qty, f"수량이 정정대로 안 들어갔다: {after[0]}"
    assert after[1] == before[1], (
        f"correcting the share count moved the cost basis from {before[1]} to "
        f"{after[1]} -- the user changed how many shares they hold, not what "
        f"they paid. (주식 수만 고쳤는데 취득원가가 바뀐다.)"
    )


def test_the_two_paths_are_not_trivially_equal(account):
    """대조군 — 두 경로가 **실제로 값을 만들고**, 이력이 다르면 답도 다르다.

    위 비교는 둘 다 `None` 을 돌려줘도, 둘 다 같은 상수를 돌려줘도 통과한다.
    그러면 "두 경로가 일치한다" 가 "두 경로가 아무것도 안 한다" 와 같아진다.
    """
    _apply("ADD", 10, 100.0, day="2026-09-01")
    cheap = _held()

    _apply("ADD", 10, 300.0, day="2026-09-02")
    mixed = _held()

    assert cheap and mixed, "즉시 반영이 값을 안 만든다"
    assert cheap != mixed, (
        f"two different histories produced the same holding ({cheap}) -- the "
        "comparison above would pass even if neither path computed anything."
    )
    assert _replayed() == mixed


# ── 실현손익이 기대는 전제 ─────────────────────────────────────────────────────
#
# `realized_pnl_from_log` 는 `if not ticker: continue` 로 종목 없는 행을
# 건너뛴다. **모든 행이 건너뛰어지면 결과가 "실현한 게 없다"(pnl 0.0,
# reason None)와 똑같아진다** — 이력이 있는데 한 건도 못 셌다는 사실이
# 어디에도 안 남는다.
#
# 지금은 결함이 아니다. `trade_log.ticker` 가 `TEXT NOT NULL` 이고
# `get_trade_log` 가 그 값을 그대로 싣는다. 그래서 그 분기에 **도달할 수
# 없다** — 도달할 수 없는 상태를 단언하면 코드가 하지 않는 약속을 적는 꼴이다.
#
# 대신 **그 안전이 기대고 있는 전제**를 못 박는다. 전제가 깨지면 위 침묵이
# 곧바로 결함이 되므로, 그때 이 검사가 먼저 빨개진다.
#
# (`calculate_metrics(trade_log=...)` 는 공개 인자라 호출자가 다른 모양을
# 넘길 수는 있다. 지금 호출자는 둘 다 `get_trade_log` 결과를 넘긴다.)

def test_every_recorded_trade_carries_a_ticker(account):
    """기록된 거래는 전부 종목을 들고 온다.

    이게 깨지면 실현손익이 그 행들을 조용히 건너뛰고, 결과가 "실현한 게
    없다" 와 구별되지 않는다.
    """
    from backend.db.portfolio_repo import get_trade_log

    _apply("ADD", 10, 100.0, day="2026-09-01")
    _apply("SOLD", 4, 150.0, day="2026-09-02")

    rows = get_trade_log(UID, market=MARKET)
    assert rows, "거래를 넣었는데 이력이 비었다 — 이 검사의 전제가 없다"

    missing = [r for r in rows if not str(r.get("ticker", "")).strip()]
    assert not missing, (
        f"{len(missing)} recorded trades carry no ticker -- realized P&L skips "
        "those rows silently, and skipping every row looks exactly like "
        "'nothing was realized'. (한 건도 못 셌다는 사실이 안 남는다.)"
    )

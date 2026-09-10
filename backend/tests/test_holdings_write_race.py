"""
동시 쓰기 경쟁 회귀 방지 — PUT/POST /holdings/{ticker}.

`routers/portfolio.py` 의 쓰기 엔드포인트 중 이 둘만 `user_write_lock` 이
빠져 있었다. 둘 다 read-modify-write 다:

- PUT  : `get_holdings` 로 old_q 를 읽고 delta 를 계산해 DEPOSIT 을 기록한 뒤
         `save_holding` 으로 **절대값**을 쓴다. 동시 2건이 같은 old_q 를 읽으면
         DEPOSIT 이 두 번 남는데 수량은 한 번만 오른다 → 원장과 잔액이 갈라진다.
- POST : `if ticker in holdings: 409` 가 TOCTOU 다. 동시 2건이 모두 통과하면
         `save_holding` 이 `ON CONFLICT DO UPDATE` 라 **에러조차 안 난다.**
         CASH 면 DEPOSIT 만 두 번 남는다.

`portfolio_repo.user_write_lock` 의 docstring 이 이 사고를 이미 적어놨다 —
"제출 버튼 더블클릭만으로 재현". 두 테스트 모두 락이 들어오기 전에 먼저
실패하는 것을 확인했고, 락이 들어온 뒤 통과하는 것으로 수정을 증명했다.

실DB 배관(풀 생명주기·DB 가드·사용자 행)은 `conftest.py`, 동시 실행 하네스는
`helpers/concurrency.py` 에 있다. 왜 모킹으로 대체할 수 없는지도 거기 적혀 있다.
"""
from __future__ import annotations

import pytest

import backend.db as db
from backend.db.portfolio_repo import save_holding
from backend.models.portfolio import HoldingItem, UpdateHoldingRequest
from backend.routers import portfolio as portfolio_router
from backend.tests.helpers.concurrency import delay_first_call, run_concurrently

MARKET = "US"


def _cash_rows(uid: str):
    """(최종 수량, DEPOSIT 건수, 원장 순변화) — 원장은 부호를 붙여 합산한다."""
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT qty FROM holdings WHERE user_id=%s AND market=%s AND ticker='CASH'",
                (uid, MARKET),
            )
            row = cur.fetchone()
            qty = float(row[0]) if row else 0.0

            cur.execute(
                "SELECT trade_type, qty FROM trade_log "
                "WHERE user_id=%s AND market=%s AND ticker='CASH'",
                (uid, MARKET),
            )
            trades = cur.fetchall()

    deposits = sum(1 for t, _ in trades if t == "DEPOSIT")
    ledger = sum(float(q) if t == "DEPOSIT" else -float(q) for t, q in trades)
    return qty, deposits, ledger


# 메시지 앞머리는 ASCII 로 둔다 — Windows 콘솔이 cp949 라 한글이 깨지는데,
# 조치에 필요한 숫자와 파일·함수 이름만은 깨진 출력에서도 읽혀야 한다.
# 환경변수(PYTHONIOENCODING)에 기대지 않으므로 CI·파일·파이프 어디로 흘러가도
# 같게 읽힌다.


def test_lock_is_actually_exercised(live_db):
    """DB 가 붙어 있어야 락이 실제로 걸린다 — 아니면 아래 두 테스트가 무의미하다.

    `user_write_lock` 은 DB 미연결이면 아무 일도 하지 않고 통과한다. 그래서
    "통과했지만 아무것도 증명 못 한" 상태와 진짜 통과를 구분해 둔다.
    """
    assert db.is_available(), (
        "no DB pool -- user_write_lock yields straight through, so nothing in "
        "this file proves anything. (DB 가 없으면 락을 재보지도 못한다.)"
    )


def test_concurrent_put_records_one_deposit(db_uid, monkeypatch):
    """PUT 동시 2건: 같은 목표 수량이면 DEPOSIT 은 1건이어야 한다.

    현금 100 에서 두 요청이 모두 150 으로 맞춘다.
    - 직렬화되면 A 가 +50 을 기록하고, B 는 이미 150 이라 delta 가 0 → 기록 없음.
    - 직렬화되지 않으면 둘 다 old=100 을 읽어 +50 을 두 번 기록한다.
      수량은 150 인데 원장은 +100 이라, 거래 이력과 잔액이 갈라진다.
    """
    save_holding("CASH", 100.0, 1.0, "Cash", db_uid, market=MARKET)

    real, wrapper = delay_first_call(portfolio_router, "get_holdings")
    monkeypatch.setattr(portfolio_router, "get_holdings", wrapper)

    def put():
        return portfolio_router.update_holding(
            "CASH",
            UpdateHoldingRequest(q=150.0, avg=1.0, date="2026-01-02"),
            _auth={"uid": db_uid},
            market=MARKET,
        )

    results = run_concurrently([put, put])
    monkeypatch.setattr(portfolio_router, "get_holdings", real)

    errors = [r for kind, r in results if kind == "err"]
    assert not errors, f"PUT raised: {errors}"

    qty, deposits, ledger = _cash_rows(db_uid)

    assert qty == pytest.approx(150.0), f"qty={qty}, expected 150.0"
    assert deposits == 1, (
        f"DEPOSIT={deposits}, expected 1 -- update_holding in "
        "routers/portfolio.py lacks user_write_lock. "
        "(두 요청이 같은 old_q 를 읽어 각자 +50 을 기록했다.)"
    )
    assert ledger == pytest.approx(qty - 100.0), (
        f"ledger={ledger}, actual change={qty - 100.0} -- trade_log and holdings diverged. "
        "(거래 이력과 잔액이 갈라졌다.)"
    )


def test_concurrent_post_creates_holding_once(db_uid, monkeypatch):
    """POST 동시 2건: 하나만 성공하고 나머지는 409 여야 한다.

    `if ticker in holdings: 409` 검사와 `save_holding` 사이가 벌어져 있고,
    `save_holding` 은 `ON CONFLICT DO UPDATE` 라 중복 생성이 에러도 내지 않는다.
    현금이면 DEPOSIT 만 두 번 남아 없던 돈이 원장에 생긴다.
    """
    real, wrapper = delay_first_call(portfolio_router, "get_holdings")
    monkeypatch.setattr(portfolio_router, "get_holdings", wrapper)

    def post():
        return portfolio_router.add_holding(
            "CASH",
            HoldingItem(q=100.0, avg=1.0, sector="Cash", date="2026-01-02"),
            _auth={"uid": db_uid},
            market=MARKET,
        )

    results = run_concurrently([post, post])
    monkeypatch.setattr(portfolio_router, "get_holdings", real)

    ok = [r for kind, r in results if kind == "ok"]
    conflicts = [r for kind, r in results if kind == "err" and getattr(r, "status_code", None) == 409]
    other = [r for kind, r in results if kind == "err" and getattr(r, "status_code", None) != 409]

    assert not other, f"non-409 exception raised: {other}"

    qty, deposits, ledger = _cash_rows(db_uid)

    assert len(ok) == 1 and len(conflicts) == 1, (
        f"ok={len(ok)}, 409={len(conflicts)}, expected 1/1 -- add_holding in "
        "routers/portfolio.py lacks user_write_lock, so the existence check "
        "and save_holding are TOCTOU. "
        "(동시 생성 2건이 모두 통과했다.)"
    )
    assert deposits == 1, (
        f"DEPOSIT={deposits}, expected 1 -- cash appeared in the ledger from nowhere. "
        "(없던 현금이 원장에 생겼다.)"
    )
    assert ledger == pytest.approx(qty), f"ledger={ledger}, qty={qty} -- they must match"

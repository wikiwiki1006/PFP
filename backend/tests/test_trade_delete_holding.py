"""매수 이력을 지우면 보유 종목도 사라져야 한다.

거래를 지웠는데 보유가 남으면 근거 없는 자산이 화면에 계속 뜬다. 반대로
거래 이력 없이 직접 등록한 보유는 지우면 안 된다 — 사용자가 입력한 값이다.
이 둘을 구분하지 못해 전자를 놓치고 있었다.
"""
import pytest

from backend.services import cash_ledger


class _Store:
    """보유·거래를 메모리에 담는 최소 대역."""

    def __init__(self):
        self.holdings: dict[str, dict] = {}
        self.trades: list[dict] = []

    def get_trade_log(self, uid, market="US"):
        return list(self.trades)

    def get_holdings(self, uid, market="US"):
        return dict(self.holdings)

    def save_holding(self, ticker, q, avg, sector, uid, market="US"):
        self.holdings[ticker] = {"q": q, "avg": avg, "sector": sector}

    def delete_holding(self, ticker, uid, market="US"):
        self.holdings.pop(ticker, None)


@pytest.fixture
def store(monkeypatch):
    s = _Store()
    monkeypatch.setattr(cash_ledger, "get_trade_log", s.get_trade_log)
    monkeypatch.setattr(cash_ledger, "get_holdings", s.get_holdings)
    monkeypatch.setattr(cash_ledger, "save_holding", s.save_holding)
    monkeypatch.setattr(cash_ledger, "delete_holding", s.delete_holding)
    return s


def test_last_buy_deleted_removes_holding(store):
    """마지막 매수를 지우면 보유도 없어진다."""
    store.holdings["AAPL"] = {"q": 10, "avg": 100.0, "sector": "Tech"}
    store.trades = []          # 방금 그 매수를 지운 상태

    cash_ledger._recalculate_holding_from_trades(
        "AAPL", "u1", market="US", drop_when_no_trades=True)

    assert "AAPL" not in store.holdings


def test_directly_entered_holding_survives(store):
    """거래 이력 없이 등록한 보유는 재계산이 지우지 않는다."""
    store.holdings["MSFT"] = {"q": 5, "avg": 200.0, "sector": "Tech"}
    store.trades = []

    cash_ledger._recalculate_holding_from_trades("MSFT", "u1", market="US")

    assert store.holdings["MSFT"]["q"] == 5


def test_partial_delete_keeps_remaining_quantity(store):
    """매수가 여러 건이면 하나를 지워도 나머지 수량이 남는다."""
    store.holdings["005930.KS"] = {"q": 30, "avg": 70000.0, "sector": "Tech"}
    store.trades = [
        {"id": 1, "ticker": "005930.KS", "type": "BUY", "q": 20,
         "price": 70000, "date": "2026-01-02"},
    ]  # 10주짜리 매수 한 건을 지운 뒤

    cash_ledger._recalculate_holding_from_trades(
        "005930.KS", "u1", market="KR", drop_when_no_trades=True)

    assert store.holdings["005930.KS"]["q"] == 20

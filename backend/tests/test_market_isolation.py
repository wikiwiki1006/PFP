"""
미국·한국 포트폴리오가 서로를 건드리지 않는지 확인한다.

DB 는 처음부터 market 컬럼으로 둘을 나눠 두었는데, 정작 저장 함수를 부르는
쪽에서 market 을 넘기지 않는 곳이 많았다. 기본값이 "US" 라 한국 화면에서 한
모든 편집이 조용히 미국 포트폴리오에 기록됐다. 실제로 겪은 것들:

  · 한국에서 현금을 입금하면 미국 현금이 늘었다
  · 한국에서 "포트폴리오 새로 등록" 을 누르면 미국 보유 종목이 통째로 지워졌다
  · 한국 화면의 리포트·최적화가 미국 보유 종목을 읽었다

이 파일은 그 반대를 못박는다. 한쪽을 건드려도 다른 쪽 숫자는 그대로여야 한다.
기본값에 기대는 코드를 다시 넣으면 여기서 걸린다.
"""
import inspect

import pytest

from backend.db import portfolio_repo
from backend.routers import portfolio as portfolio_router
from backend.services import cash_ledger


# ── 시그니처 ─────────────────────────────────────────────────────────────────
# 호출부를 일일이 훑는 대신, 시장을 가르는 함수가 market 을 받는지부터 확인한다.

@pytest.mark.parametrize("fn", [
    portfolio_repo.get_holdings,
    portfolio_repo.save_holding,
    portfolio_repo.delete_holding,
    portfolio_repo.get_trade_log,
    portfolio_repo.add_trade,
    portfolio_repo.wipe_portfolio,
    cash_ledger._adjust_cash,
    cash_ledger._revert_cash_event,
    cash_ledger._recalculate_holding_from_trades,
])
def test_market_scoped_functions_accept_market(fn):
    assert "market" in inspect.signature(fn).parameters, (
        f"{fn.__module__}.{fn.__qualname__} 가 market 을 받지 않는다 — "
        "이 함수를 쓰는 모든 곳이 미국에만 쓰게 된다"
    )


def test_wipe_portfolio_is_scoped_to_one_market():
    """새로 등록이 다른 시장까지 지우면 사용자 데이터가 날아간다."""
    src = inspect.getsource(portfolio_repo.wipe_portfolio)
    assert src.count("AND market=%s") == 2, (
        "wipe_portfolio 의 DELETE 두 문장이 모두 market 으로 좁혀져 있어야 한다"
    )


# ── 호출부 ───────────────────────────────────────────────────────────────────

def _module_sources():
    from backend.routers import macro, optimizer, reports, signals, ticker
    return {
        m.__name__: inspect.getsource(m)
        for m in (portfolio_router, macro, optimizer, reports, signals, ticker,
                  cash_ledger)
    }


@pytest.mark.parametrize("call", [
    "save_holding(", "get_holdings(", "add_trade(", "get_trade_log(",
    "delete_holding(", "wipe_portfolio(",
])
def test_no_call_site_relies_on_the_default_market(call):
    """market 을 생략한 호출은 전부 미국으로 간다 — 한 건도 남기지 않는다."""
    offenders = []
    for name, src in _module_sources().items():
        lines = src.splitlines()
        for i, line in enumerate(lines):
            col = line.find(call)
            if col < 0 or line.lstrip().startswith(("#", "def ", "from ", "import ")):
                continue
            # 호출은 여러 줄에 걸치므로 괄호가 닫힐 때까지 이어 붙여서 본다.
            # 3 줄만 보면 인자가 긴 add_trade({...}, uid, market=market) 를 놓친다.
            depth, chunk = 0, []
            for text in lines[i:]:
                seg = text[col:] if text is line else text
                chunk.append(seg)
                depth += seg.count("(") - seg.count(")")
                if depth <= 0:
                    break
            if "market=" not in "".join(chunk):
                offenders.append(f"{name}:{i + 1}: {line.strip()}")
    assert not offenders, (
        f"{call} 호출에 market 이 빠졌다:\n  " + "\n  ".join(offenders)
    )


# ── 현금 원장 ────────────────────────────────────────────────────────────────

def test_cash_deposit_moves_the_balance():
    """입금이 거래 이력에만 남고 잔고를 건드리지 않던 적이 있다."""
    assert cash_ledger._cash_event_delta("DEPOSIT", 1000) == 1000
    assert cash_ledger._cash_event_delta("WITHDRAW", 1000) == -1000


def test_trade_handler_applies_cash_events():
    """CASH 입출금 분기가 _add_trade_locked 안에 남아 있어야 한다."""
    src = inspect.getsource(portfolio_router._add_trade_locked)
    assert "_cash_event_delta(trade_type" in src, (
        "CASH 입출금이 잔고에 반영되지 않으면 입금해도 0 원인 채 성공만 돌아온다"
    )

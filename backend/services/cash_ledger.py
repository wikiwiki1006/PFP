"""
services/cash_ledger.py
───────────────────────
현금 잔고 조정 + 거래 이력 재생.

매수·매도·입출금이 현금과 보유 수량에 미치는 영향을 한 곳에 모았다.
규칙이 바뀌면 이 파일만 보면 된다.
"""
from __future__ import annotations

from backend.db.portfolio_repo import (
    get_holdings, save_holding, delete_holding, get_trade_log,
)


def _cash_event_delta(trade_type: str, q: float) -> float:
    """CASH 입출금 이벤트의 잔고 변화량. DEPOSIT → +q, WITHDRAW → -q."""
    t = str(trade_type).upper()
    if t == "DEPOSIT":
        return +abs(q)
    if t == "WITHDRAW":
        return -abs(q)
    return 0.0



def _revert_cash_event(uid: str, trade_type: str, q: float,
                       market: str = "US") -> None:
    """삭제된 CASH 입출금 이벤트를 잔고에서 되돌린다."""
    d = _cash_event_delta(trade_type, q)
    if d:
        _adjust_cash(uid, -d, market=market)



def _cash_delta(trade_type: str, q: float, price: float) -> float:
    """거래 유형에 따른 현금 변화량 반환. BUY → 음수(차감), SELL → 양수(증가)."""
    t = trade_type.upper()
    if t in ("ADD", "BUY"):
        return -(q * price)
    if t in ("SOLD", "SELL"):
        return +(q * price)
    return 0.0



def _adjust_cash(uid: str, delta: float, market: str = "US") -> None:
    """CASH 잔고에 delta를 가감. 잔고가 없으면 delta > 0일 때만 생성.

    market 은 반드시 넘겨야 한다. 미국·한국 포트폴리오는 CASH 행까지 따로
    관리되므로, 이 값을 빠뜨리면 한국 거래가 미국 현금을 건드린다.

    max(0, ...) 로 클램프하지 않는다 — 잔고보다 큰 매수를 0으로 잘라내면
    차감되지 못한 금액이 조용히 사라지고, 나중에 그 종목을 매도할 때는
    전액이 다시 입금돼 없던 현금이 생긴다. 음수 잔고는 그대로 보여주는 편이
    입력 오류를 드러내므로 안전하다.
    """
    if abs(delta) < 0.001:
        return
    holdings_ = get_holdings(uid, market=market)
    cash_h = holdings_.get("CASH")
    if cash_h is None:
        if delta > 0:
            save_holding("CASH", round(delta, 2), 1.0, "Cash", uid, market=market)
        return
    cur = float(cash_h.get("q", 0)) if isinstance(cash_h, dict) else float(cash_h or 0)
    save_holding("CASH", round(cur + delta, 2), 1.0, "Cash", uid, market=market)



def _recalculate_holding_from_trades(ticker: str, uid: str,
                                     market: str = "US",
                                     drop_when_no_trades: bool = False) -> None:
    """거래 기록 전체를 재생해 보유 수량·단가를 재계산. 수량 0이면 삭제.
    CASH는 _adjust_cash로 별도 처리.

    drop_when_no_trades 는 "이 종목의 거래가 방금 지워졌다"는 뜻이다.
    거래가 하나도 남지 않았을 때 보유를 지울지 말지가 호출자마다 다르기 때문에
    받는다. 아래 두 경우를 함수 안에서는 구분할 수 없다:

      ① 거래 이력 없이 직접 등록한 보유 (POST /holdings/{ticker})
         → 지우면 사용자가 입력한 보유가 통째로 사라진다.
      ② 매수 이력을 지워서 거래가 0건이 된 경우
         → 남겨 두면 근거 없는 보유가 화면에 계속 떠 있는다.

    거래 삭제 경로만 True 를 준다."""
    if ticker.upper() == "CASH":
        return
    ticker = ticker.upper()
    # 티커 비교는 대소문자 무시 — 다른 경로가 소문자로 저장했더라도 재생에서 누락되면
    # 수량이 0으로 계산돼 멀쩡한 보유 종목이 삭제된다.
    trades = sorted(
        [t for t in get_trade_log(uid, market=market)
         if str(t.get("ticker", "")).upper() == ticker],
        key=lambda t: (t.get("date", ""), t.get("id", 0)),
    )
    # 거래 이력이 아예 없는 종목은 직접 등록된 보유분이다 (POST /holdings/{ticker}).
    # 재생 결과 0 이라고 삭제해버리면 사용자가 입력한 보유가 사라진다.
    if not trades:
        if drop_when_no_trades:
            delete_holding(ticker, uid, market=market)
        return

    qty        = 0.0
    total_cost = 0.0

    for tr in trades:
        q     = float(tr.get("q") or 0)
        price = float(tr.get("price") or 0)
        ttype = tr.get("type", "")
        if ttype in ("ADD", "BUY"):
            total_cost += price * q
            qty        += q
        elif ttype in ("SOLD", "SELL"):
            if qty > 0:
                ratio      = min(q, qty) / qty
                total_cost = total_cost * (1.0 - ratio)
            qty = max(0.0, qty - q)
        elif ttype == "UPDATE":
            # 수량만 정정한다 — 주당 평단은 보존한다.
            #
            # 여기 total_cost 는 **합계**라서, qty 만 바꾸고 두면
            # avg = total_cost / qty 가 수량에 반비례해 움직인다. 10주를 100에
            # 사고 수량을 20으로 정정하면 평단이 100 → 50 이 되고, 반대로 5로
            # 줄이면 200 이 된다. 사용자는 주식 수만 고쳤는데 취득원가가 바뀐다.
            #
            # 같은 사건을 다루는 다른 세 곳은 전부 **주당** 값을 들고 있어서
            # 손대지 않는 것이 곧 보존이었다 (routers/portfolio.py 의 UPDATE 는
            # cur["avg"] 를 그대로 넘기고, portfolio_calculator 의 두 재생은
            # running_cost·running_avg 를 건드리지 않는다). 표현이 달라서 같은
            # 한 줄이 정반대 결과를 냈다 — 여기서 비율로 옮겨 맞춘다.
            #
            # qty 가 0 이면 옮길 원가가 없다. 그 경우 total_cost 도 0 이라
            # 평단이 0 으로 남는데, 이건 고치기 전과 같은 동작이다.
            if qty > 0:
                total_cost = total_cost / qty * q
            qty = q

    holdings = get_holdings(uid, market=market)
    existing = holdings.get(ticker, {})
    sector   = existing.get("sector", "Other")

    qty = round(qty, 6)
    if qty <= 0:
        delete_holding(ticker, uid, market=market)
    else:
        avg = round(total_cost / qty, 4) if qty > 0 else 0.0
        save_holding(ticker, qty, avg, sector, uid, market=market)



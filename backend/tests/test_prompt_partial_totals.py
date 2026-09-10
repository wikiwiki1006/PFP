"""
B3 — 일부만 있는 데이터로 만든 합계를 전체 합처럼 적지 않는다.

모델은 프롬프트의 `총자산 $3,310.00` 을 포트폴리오 전체 값으로 인용한다.
빠진 종목이 있다는 것은 프롬프트 어디에도 없으니 알 방법이 없다. 그렇게
과소 집계된 숫자가 리포트에 사실로 실린다.

`generate_daily_brief` 는 **오늘 손익 합계**에 대해 이미 이걸 지킨다 —
일부 종목의 `day_pnl` 이 없으면 합계 대신 "일부 종목 데이터 없음 — 합산 불가"
라고 적는다. 그 동작을 여기서 고정한다. 다음 사람이 "합계가 왜 안 나오지" 하고
부분 합으로 되돌리기 쉬운 자리다.

## 같은 함수 안에서 관례가 갈려 있다

바로 그 옆의 **주식 평가액·총자산**에는 같은 보호가 없다. 한 줄 아래에서
`pos_val` 을 `is not None` 으로 걸러 더한 뒤 `총자산` 이라고 적는다.

두 경우에 깨진다:

  - 보유 종목 하나가 `price_data` 에 통째로 없을 때 (가격을 못 받으면
    `macro.py` 가 그 종목을 아예 넣지 않는다)
  - `pos_val` 만 `None` 일 때 — 종목 줄에는 `평가액 —` 로 정직하게 나가는데,
    합계 줄은 그 종목을 빼고 더한 값을 총자산이라고 적는다

**손익 합계는 알고 평가액 합계는 모른다.** 규칙을 아는 코드와 모르는 코드가
같은 함수 안에 세 줄 간격으로 있다.
"""
from __future__ import annotations

from unittest import mock

import pytest

from backend.services import ai_analysis

# 보유는 둘. price_data 에 무엇이 들어오는지가 케이스마다 다르다.
_HOLDINGS = {
    "AAPL": {"q": 10, "avg": 200.0, "sector": "Tech"},
    "MSFT": {"q": 5,  "avg": 400.0, "sector": "Tech"},
    "CASH": {"q": 1000.0},
}
_AAPL = {"price": 231.0, "chg_pct": 1.0, "pnl_pct": 15.5, "pos_val": 2310.0, "day_pnl": 20.0}
_MSFT = {"price": 420.0, "chg_pct": 0.5, "pnl_pct": 5.0,  "pos_val": 2100.0, "day_pnl": 10.0}

_CANNOT_SUM = "합산 불가"


def _brief(price_data: dict) -> str:
    """실제 경로가 만드는 프롬프트. `call_claude` 를 가로채 문자열을 받는다."""
    with mock.patch.object(ai_analysis, "call_claude", lambda prompt, *a, **kw: prompt), \
         mock.patch.object(ai_analysis, "build_macro_block", lambda m: "  (없음)"):
        return ai_analysis.generate_daily_brief(_HOLDINGS, price_data, [], "US")


def _total_line(text: str) -> str:
    for line in text.splitlines():
        if "총자산" in line:
            return line.strip()
    raise AssertionError(f"총자산 줄이 없다:\n{text[:400]}")


# ── 이미 지키고 있는 것 — 되돌아가지 않게 고정한다 ──────────────────────────────

def test_full_data_does_produce_a_total():
    """대조군 — 데이터가 다 있으면 합계를 적는다.

    없으면 아래 검사들은 "아무 합계도 안 적는" 구현으로 전부 통과한다.
    합계를 안 내는 것이 목적이 아니라, **부분 합을 전체로 적지 않는 것**이
    목적이다.
    """
    line = _total_line(_brief({"AAPL": _AAPL, "MSFT": _MSFT}))

    assert "$5,410.00" in line, f"주식 평가액 합계가 없다: {line}"
    assert "$30.00" in line, f"오늘 손익 합계가 없다: {line}"
    assert _CANNOT_SUM not in line, f"데이터가 다 있는데 합산 불가로 적었다: {line}"


def test_partial_day_pnl_refuses_to_sum():
    """일부 종목의 `day_pnl` 이 없으면 손익 합계를 내지 않는다.

    지금 동작하는 보호다. 되돌리기 쉬운 자리라 고정해 둔다 — 부분 합을 적으면
    모델은 그걸 포트폴리오 전체 손익으로 인용한다.
    """
    line = _total_line(_brief({"AAPL": _AAPL, "MSFT": {**_MSFT, "day_pnl": None}}))

    assert _CANNOT_SUM in line, (
        f"a partial day-P&L was summed and labelled as the total: {line} -- "
        "the model quotes it as the whole portfolio's P&L. "
        "(부분 합을 전체 손익으로 적었다.)"
    )
    assert "$20.00" not in line, f"부분 합이 그대로 적혔다: {line}"


# ── 평가액·총자산 — 손익 합계와 같은 보호를 받는다 ──────────────────────────────
#
# 합계의 기준을 `price_data` 에서 **보유 종목**으로 바꿔 해결했다. 가격을 못 받은
# 종목은 조립부에서 통째로 빠지므로, price_data 를 세면 빠진 종목은 애초에
# 세어지지 않는다 — "없는 것을 못 세는" 형태라 검사가 없으면 안 보인다.


def test_holding_missing_from_price_data_is_not_silently_dropped():
    """보유 종목이 `price_data` 에 없으면 총자산을 단정하지 않는다.

    가격을 못 받은 종목은 `macro.py` 의 조립부에서 **통째로 빠진다**. 그러면
    총자산이 그 종목만큼 작아지는데, 프롬프트에는 빠졌다는 사실이 없다.
    보유는 `holdings` 에 그대로 있으므로 이 함수는 불일치를 알 수 있다.
    """
    line = _total_line(_brief({"AAPL": _AAPL}))          # MSFT 가 빠졌다

    assert _CANNOT_SUM in line or "MSFT" in line, (
        f"a holding absent from price_data vanished from the total: {line} -- "
        "MSFT is held but the portfolio is reported as if it were not. "
        "(보유 종목이 총자산에서 조용히 사라졌다.)"
    )


def test_partial_pos_val_refuses_to_sum():
    """`pos_val` 이 일부 없으면 평가액 합계를 내지 않는다.

    종목 줄은 `평가액 —` 로 정직하게 나간다. 합계 줄만 그 종목을 빼고 더한 뒤
    총자산이라고 적는다 — 같은 프롬프트 안에서 두 줄이 서로 다른 말을 한다.
    """
    line = _total_line(_brief({"AAPL": {**_AAPL, "pos_val": None}, "MSFT": _MSFT}))

    assert _CANNOT_SUM in line, (
        f"a partial equity sum was labelled as the total: {line} -- the same "
        "prompt shows 평가액 — for that holding, so the two lines disagree. "
        "(종목 줄은 모른다고 하고 합계 줄은 단정한다.)"
    )

"""
`/holdings-detail` 과 `/equity-curve` 의 숫자도 유한해야 한다.

왜 이 가드가 필요한지는 `test_metrics_json_safe.py` 상단에 적어두었다. 요약하면
**`SafeJSONResponse` 가 NaN/Inf 를 `null` 로 바꿔주기 때문에** 개별 계산 지점의
`_safe()` 누락이 화면에서 영원히 보이지 않는다는 것이다. 요청은 200 으로 나가고
사용자는 `—` 를 볼 뿐이라, 값이 잘못 계산된 것과 값이 원래 없는 것이 구별되지
않는다 (§1.3).

`/metrics` 는 평평한 dict 하나지만 이 둘은 **리스트 안에 dict 가 들어간다.**
필드가 많고 종목마다 반복되므로 누락이 숨기 쉽다. 그래서 검사도 중첩을 따라간다
(`helpers/json_safety.py`).

지금은 두 경로 모두 깨끗하다. 이 파일은 버그를 고치는 것이 아니라 **그 상태를
고정한다** — 새 필드가 추가될 때 여기서 걸린다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.services.portfolio_calculator import (
    build_return_pct_curve,
    get_holdings_detail,
    return_pct_to_records,
)
from backend.tests.helpers.json_safety import non_finite

_DATES = pd.bdate_range("2026-01-05", periods=6)
_HOLDINGS = {"AAPL": {"q": 10, "avg": 100.0, "sector": "Tech"}}
_TRADES = [{"date": "2026-01-05", "ticker": "AAPL", "type": "ADD", "q": 10, "price": 100.0}]


def _prices(**cols) -> pd.DataFrame:
    return pd.DataFrame(dict(cols), index=_DATES)


def _holding(q=10, avg=100.0) -> dict:
    return {"AAPL": {"q": q, "avg": avg, "sector": "Tech"}}


# ── /holdings-detail ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("holdings, close_df", [
    pytest.param(_HOLDINGS, _prices(AAPL=[100.0] * 6), id="정상-대조군"),
    pytest.param(_HOLDINGS, _prices(AAPL=[np.nan] * 6), id="가격-전량-NaN"),
    pytest.param(_HOLDINGS, _prices(AAPL=[0.0] * 6), id="가격-0"),
    pytest.param(_holding(q=float("nan")), _prices(AAPL=[100.0] * 6), id="수량-NaN"),
    pytest.param(_holding(q=float("inf")), _prices(AAPL=[100.0] * 6), id="수량-inf"),
    # avg 가 0 이면 손익률이 0 으로 나누기가 된다.
    pytest.param(_holding(avg=0.0), _prices(AAPL=[100.0] * 6), id="평단가-0"),
    pytest.param(_holding(avg=float("nan")), _prices(AAPL=[100.0] * 6), id="평단가-NaN"),
])
def test_holdings_detail_rows_are_finite(holdings, close_df):
    """종목 행의 어떤 숫자도 NaN/Inf 가 아니다."""
    rows = get_holdings_detail(holdings, close_df)

    bad = non_finite(rows)
    assert not bad, (
        f"non-finite value(s) in /holdings-detail: {bad} -- SafeJSONResponse "
        "will render these as null, so a miscalculated row is indistinguishable "
        "from a row with no data. Wrap the value in _safe() where it is produced. "
        "(계산 실패와 값 없음이 화면에서 같아진다.)"
    )


# ── /equity-curve ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("holdings, trades, close_df", [
    pytest.param(_HOLDINGS, _TRADES,
                 _prices(AAPL=[100.0, 101.0, 102.0, 103.0, 104.0, 105.0]),
                 id="정상-대조군"),
    pytest.param(_HOLDINGS, _TRADES, _prices(AAPL=[np.nan] * 6), id="가격-전량-NaN"),
    pytest.param(_HOLDINGS, _TRADES, _prices(AAPL=[0.0] * 6), id="가격-0"),
    pytest.param(_HOLDINGS, [], _prices(AAPL=[100.0] * 6), id="거래-없음"),
    # 벤치마크가 비면 상대 수익률 계산의 분모가 사라진다.
    pytest.param(_HOLDINGS, _TRADES,
                 _prices(AAPL=[100.0] * 6, **{"^GSPC": [np.nan] * 6}),
                 id="벤치마크-전량-NaN"),
    pytest.param(_HOLDINGS, _TRADES,
                 _prices(AAPL=[100.0] * 6, **{"^GSPC": [0.0] * 6}),
                 id="벤치마크-0"),
    pytest.param({"CASH": {"q": 100.0, "avg": 1.0, "sector": "Cash"}},
                 [{"date": "2026-01-05", "ticker": "CASH", "type": "DEPOSIT",
                   "q": 100.0, "price": 1.0}],
                 _prices(AAPL=[100.0] * 6),
                 id="현금만-입금"),
])
def test_equity_curve_records_are_finite(holdings, trades, close_df):
    """수익률 곡선 레코드의 어떤 숫자도 NaN/Inf 가 아니다."""
    return_pct, by_date, initial, cash_events, equity = build_return_pct_curve(
        holdings, trades, close_df)
    records = return_pct_to_records(
        return_pct, by_date, close_df, None, initial, cash_events, equity)

    bad = non_finite(records)
    assert not bad, (
        f"non-finite value(s) in /equity-curve: {bad} -- these render as null, "
        "so a broken point on the curve looks the same as a gap in the data. "
        "(끊긴 계산과 빈 구간이 그래프에서 같아진다.)"
    )

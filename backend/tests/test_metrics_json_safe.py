"""
metrics 응답에 NaN/Inf 가 들어가면 안 된다 — 한 필드가 아니라 요청 전체가 죽는다.

`float('nan')` 은 JSON 이 아니다. FastAPI 는 그걸 내보내지 못하고 예외를 던진다:

    JSONResponse({"x": float("nan")})
    ValueError: Out of range float values are not JSON compliant

즉 어떤 지표 하나가 NaN 이 되면 그 필드만 '—' 로 뜨는 게 아니라 **`/metrics`
요청이 통째로 500** 이 되고 화면 전체가 안 뜬다. `total_return_pct` 하나의
문제가 아니라 **응답 경계 전체의 성질**이라, 나중에 누가 새 지표를 추가할 때도
여기서 잡혀야 한다.

`calculate_metrics` 는 대부분의 값을 `_safe()`(NaN/Inf → 기본값)로 감싸 두었다.
이 파일은 그 보호가 **구조적인지 우연인지**를 퇴화 입력들로 확인한다. 지금은
우연히 안전한 경로가 하나 있다 — 아래 `^VIX` 케이스.

실DB 는 쓰지 않는다. 계산 함수에 값을 넣고 나온 값을 보는 것이라 모킹으로 충분하다.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from fastapi.responses import JSONResponse

from backend.services.portfolio_calculator import calculate_metrics

_DATES = pd.bdate_range("2026-01-05", periods=6)
_HOLDINGS = {"AAPL": {"q": 10, "avg": 100.0, "sector": "Tech"}}


def _prices(**cols) -> pd.DataFrame:
    return pd.DataFrame({k: v for k, v in cols.items()}, index=_DATES)


def _flat(n: float) -> pd.Series:
    return pd.Series([n] * len(_DATES), index=_DATES)


def _non_finite(metrics: dict) -> dict:
    """응답에 남은 NaN/Inf 필드. 중첩 없이 평평한 dict 라 한 겹만 본다."""
    return {
        k: v for k, v in metrics.items()
        if isinstance(v, float) and not math.isfinite(v)
    }


# (id, holdings, close_df, equity_curve)
_DEGENERATE = [
    pytest.param(
        _HOLDINGS, _prices(AAPL=[100.0] * 6), _flat(1000.0),
        id="정상-대조군",
    ),
    pytest.param(
        {}, _prices(AAPL=[100.0] * 6), _flat(0.0),
        id="보유-없음",
    ),
    pytest.param(
        _HOLDINGS, _prices(AAPL=[np.nan] * 6), _flat(0.0),
        id="가격-전량-NaN",
    ),
    pytest.param(
        _HOLDINGS, _prices(AAPL=[100.0] * 6),
        pd.Series([0.0, 0.0, 0.0, 0.0, 0.0, 1000.0], index=_DATES),
        id="곡선이-0에서-시작",
    ),
    pytest.param(
        _HOLDINGS, _prices(AAPL=[100.0] * 6), _flat(0.0),
        id="곡선-전량-0",
    ),
    pytest.param(
        _HOLDINGS, _prices(AAPL=[100.0] * 6), _flat(float("nan")),
        id="곡선-전량-NaN",
    ),
    pytest.param(
        {"AAPL": {"q": float("nan"), "avg": float("nan"), "sector": "Tech"}},
        _prices(AAPL=[100.0] * 6), _flat(1000.0),
        id="보유-수량이-NaN",
    ),
    pytest.param(
        {"CASH": {"q": float("inf"), "avg": 1.0, "sector": "Cash"}},
        _prices(AAPL=[100.0] * 6), _flat(1000.0),
        id="현금이-inf",
    ),
    pytest.param(
        _HOLDINGS, _prices(AAPL=[100.0] * 6, **{"^VIX": [18.0] * 5 + [np.nan]}),
        _flat(1000.0),
        id="VIX-마지막만-NaN",
    ),
]


@pytest.mark.parametrize("holdings, close_df, equity", _DEGENERATE)
def test_no_non_finite_float_in_metrics(holdings, close_df, equity):
    """퇴화 입력에서도 응답의 모든 float 이 유한해야 한다."""
    metrics = calculate_metrics(holdings, close_df, equity)

    bad = _non_finite(metrics)
    assert not bad, (
        f"non-finite value(s) in the metrics response: {bad} -- NaN/Inf is not "
        "JSON, so FastAPI fails the whole /metrics request rather than "
        "reporting one bad field. Wrap the value in _safe(). "
        "(한 필드가 아니라 요청 전체가 500 이 된다.)"
    )


@pytest.mark.parametrize("holdings, close_df, equity", _DEGENERATE)
def test_metrics_response_is_serializable(holdings, close_df, equity):
    """실제 응답 경로로 직렬화된다 — 이게 사용자가 겪는 실패 지점이다.

    위 테스트가 값을 보는 것이라면 이건 결과를 본다. 새 지표가 dict·list 처럼
    중첩된 형태로 들어와 NaN 을 숨겨도 여기서 걸린다.
    """
    metrics = calculate_metrics(holdings, close_df, equity)
    try:
        JSONResponse(metrics)
    except ValueError as e:
        pytest.fail(
            f"/metrics response is not JSON-serializable: {e} -- the request "
            f"would return 500. metrics={metrics}"
        )


def test_all_nan_vix_column_does_not_poison_the_response():
    """`^VIX` 열이 전량 NaN 이어도 `vix` 가 NaN 으로 새어 나가지 않아야 한다.

    `vix = float(curr.get("^VIX", 18.0))` 이었을 때, 기본값 18.0 은 **열이
    없을 때만** 쓰였다. 열은 있는데 값이 전부 NaN 이면 `.get()` 이 NaN 을
    돌려주고, 그 분기에만 다른 값들과 달리 `_safe()` 가 없었다. `ffill()` 도
    전량 NaN 열은 채우지 못한다.

    yfinance 가 ^VIX 를 빈 열로 주는 것은 실제로 일어난다. 그때 사용자는
    폴백값 18.0 이 아니라 **빈 값('—')** 을 봤다 — 앱 전역 `SafeJSONResponse`
    가 NaN 을 null 로 바꿔 주기 때문에 요청이 깨지지는 않고, 그 안전망이
    이 누락을 화면에서 가려 왔다.
    """
    metrics = calculate_metrics(
        _HOLDINGS,
        _prices(AAPL=[100.0] * 6, **{"^VIX": [np.nan] * 6}),
        _flat(1000.0),
    )

    bad = _non_finite(metrics)
    assert not bad, (
        f"non-finite value(s): {bad} -- the vix read in calculate_metrics needs _safe()"
    )

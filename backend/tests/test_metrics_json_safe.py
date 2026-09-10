"""
metrics 응답에 NaN/Inf 가 들어가면 안 된다 — **안전망이 그걸 가려주기 때문에.**

`main.py` 의 `SafeJSONResponse` 가 앱 기본 응답 클래스이고, 직렬화 직전에
NaN/Inf 를 `null` 로 바꾼다. 그래서 지표 하나가 NaN 이 돼도 요청은 200 으로
나가고 화면에는 `—` 가 뜬다. **요청이 죽지 않는다.**

그게 바로 이 파일이 필요한 이유다. 안전망이 증상을 지워버리므로, 개별 계산
지점에서 `_safe()` 를 빠뜨려도 **화면에서는 영원히 보이지 않는다.** 아래
`^VIX` 케이스가 정확히 그렇게 살아남았다. 이 파일은 안전망 **뒤쪽**, 즉 값이
만들어지는 자리를 본다.

가려지는 것이 증상만은 아니다. `vix` 의 기본값 `18.0` 처럼 **"값이 없으면 이걸
쓴다" 는 의도 자체가 사라진다.** NaN 이 `null` 로 바뀌고 나면 "기본값을
적용하려 했는데 실패했다" 와 "값이 원래 없다" 가 화면에서 똑같아진다 — §1.3 의
형태 그대로다.

그리고 안전망은 **보장이 아니라 마지막 방어선**이다. 누가 특정 라우트에
`response_class=JSONResponse` 를 명시하거나 `SafeJSONResponse` 를 걷어내면,
그때는 진짜로 `ValueError: Out of range float values are not JSON compliant` 가
나고 요청 전체가 500 이 된다. 그래서 안전망이 제자리에 있는지도 함께 고정한다.

`calculate_metrics` 는 대부분의 값을 `_safe()`(NaN/Inf → 기본값)로 감싸 두었다.
이 파일은 그 보호가 **구조적인지 우연인지**를 퇴화 입력들로 확인한다.

실DB 는 쓰지 않는다. 계산 함수에 값을 넣고 나온 값을 보는 것이라 모킹으로 충분하다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from fastapi.responses import JSONResponse

from backend.services.portfolio_calculator import calculate_metrics
from backend.tests.helpers.json_safety import non_finite

_DATES = pd.bdate_range("2026-01-05", periods=6)
_HOLDINGS = {"AAPL": {"q": 10, "avg": 100.0, "sector": "Tech"}}


def _prices(**cols) -> pd.DataFrame:
    return pd.DataFrame({k: v for k, v in cols.items()}, index=_DATES)


def _flat(n: float) -> pd.Series:
    return pd.Series([n] * len(_DATES), index=_DATES)


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

    bad = non_finite(metrics)
    assert not bad, (
        f"non-finite value(s) in the metrics response: {bad} -- SafeJSONResponse "
        "will turn these into null, so the screen shows an em dash and nobody "
        "ever learns the value was miscalculated. Wrap it in _safe() at the "
        "point it is produced. (안전망이 증상을 지워 영영 안 보이게 된다.)"
    )


@pytest.mark.parametrize("holdings, close_df, equity", _DEGENERATE)
def test_metrics_is_clean_before_the_safety_net(holdings, close_df, equity):
    """안전망 없이도 응답이 엄격한 JSON 이어야 한다.

    Starlette 의 기본 `JSONResponse` 는 `allow_nan=False` 라 NaN 이 있으면
    거부한다. 여기서 그걸 일부러 쓴다 — `SafeJSONResponse` 로 검사하면 NaN 을
    `null` 로 바꿔주므로 **무엇을 넣어도 통과해서 아무것도 못 잰다.**

    위 테스트가 값을 하나씩 훑는 것이라면 이건 직렬화기 자체를 통과시킨다.
    훑기가 못 보는 형태 — 예를 들어 `float` 이 아닌데 직렬화 때 NaN 이 되는
    numpy 스칼라 — 가 들어와도 여기서 걸린다.
    """
    metrics = calculate_metrics(holdings, close_df, equity)
    try:
        JSONResponse(metrics)
    except ValueError as e:
        pytest.fail(
            f"metrics is not strict JSON before SafeJSONResponse cleans it: {e} "
            f"-- some value skipped _safe(). metrics={metrics}"
        )


def test_safety_net_is_still_wired_up():
    """`SafeJSONResponse` 가 앱 기본 응답 클래스로 붙어 있는지.

    위 검사들은 "NaN 이 새어도 요청은 죽지 않는다" 를 전제로 심각도를 낮춰
    잡는다. 그 전제가 사라지면 — 누가 이 클래스를 걷어내거나 특정 라우트에
    `response_class=JSONResponse` 를 명시하면 — 같은 NaN 이 그때는 500 이 된다.
    전제가 조용히 바뀌지 않도록 여기서 고정한다.
    """
    from backend.main import SafeJSONResponse, app

    assert app.router.default_response_class is SafeJSONResponse, (
        f"the app's default response class is {app.router.default_response_class!r}, "
        "not SafeJSONResponse -- a non-finite metric now fails the whole request "
        "with 500 instead of rendering as null. "
        "(안전망이 사라지면 심각도가 올라간다.)"
    )
    assert SafeJSONResponse({"x": float("nan")}).body == b'{"x":null}', (
        "SafeJSONResponse no longer converts non-finite values to null"
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

    bad = non_finite(metrics)
    assert not bad, (
        f"non-finite value(s): {bad} -- the vix read in calculate_metrics needs _safe()"
    )

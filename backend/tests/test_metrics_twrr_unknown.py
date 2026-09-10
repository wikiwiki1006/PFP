"""
TWRR 보정에 실패하면 `total_return_pct` 는 0.0 이 아니라 None 이어야 한다.

`calculate_metrics` 가 내놓는 `total_return_pct` 는 날짜 보정이 들어간
equity_curve 로 계산돼서 **추가 입금이 있으면 이미 왜곡된 것으로 알려져 있다.**
`/metrics` 는 그 위에 TWRR 마지막 값을 덮어써 바로잡는다.

그 덮어쓰기가 실패했을 때 보정 전 값을 그대로 내보내면, 사용자는 틀린 수익률을
정상처럼 본다 — 화면에 '—' 가 아니라 그럴듯한 숫자가 뜨므로 아무도 눈치채지
못한다. CLAUDE.md §1.3: 0% 는 "보합" 이라는 뜻이지 "모름" 이 아니다. 계산
불가는 None 으로 나가고 프론트 `formatPct` 가 '—' 로 받는다.

실패 경로는 두 가지고 둘 다 조용하다:

  1. `build_return_pct_curve` 가 예외를 던진다
  2. 곡선에 **행은 있는데 값이 전량 NaN** 이다 — `twrr.empty` 는 False 라
     빈 곡선 검사를 통과해 버리고, `dropna()` 후 `iloc[-1]` 에서 IndexError 가 난다

`/metrics` 는 라우터 함수를 직접 부른다. 실DB 는 쓰지 않는다 — 여기서 재는 것은
계산 경로의 분기이고, 모킹으로 충분히 재현된다. (P0 의 advisory lock 은 세션
단위라 모킹으로 검증 자체가 불가능해서 실DB 를 썼다. 그 조건이 아니면 모킹이다.)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.routers import portfolio as portfolio_router

MARKET = "US"
UID = "u1"

# calculate_metrics 가 내놓는 값들. total_return_pct 만 덮어써지고 나머지는
# 그대로 남아야 한다 — 보정 실패가 다른 지표까지 무효화하면 안 된다.
_BASE_METRICS = {
    "total_equity": 12_345.67,
    "total_cost": 10_000.0,
    "total_return_pct": 23.4567,      # 보정 전 값 (왜곡된 것으로 알려져 있음)
    "daily_change": 12.5,
    "daily_change_pct": 0.1,
    "beta": 1.02,
}

_DATES = pd.date_range("2026-01-01", periods=3, freq="D")


@pytest.fixture
def metrics_call(monkeypatch):
    """`/metrics` 를 부를 수 있는 최소 환경. TWRR 만 테스트가 갈아끼운다."""
    monkeypatch.setattr(portfolio_router, "get_holdings",
                        lambda uid, market="US": {"AAPL": {"q": 10, "avg": 100.0, "sector": "Tech"}})
    monkeypatch.setattr(portfolio_router, "get_trade_log",
                        lambda uid, market="US": [
                            {"date": "2026-01-01", "ticker": "AAPL", "type": "ADD", "q": 10, "price": 100.0},
                        ])
    monkeypatch.setattr(portfolio_router, "_portfolio_close_df",
                        lambda *a, **kw: pd.DataFrame({"AAPL": [100.0, 110.0, 120.0]}, index=_DATES))
    monkeypatch.setattr(portfolio_router, "get_close_df", lambda *a, **kw: pd.DataFrame())
    monkeypatch.setattr(portfolio_router, "build_equity_curve",
                        lambda *a, **kw: pd.Series([1000.0, 1100.0, 1200.0], index=_DATES))
    monkeypatch.setattr(portfolio_router, "calculate_metrics",
                        lambda *a, **kw: dict(_BASE_METRICS))
    monkeypatch.setattr(portfolio_router, "_is_market_open", lambda *a, **kw: False)

    def call():
        return portfolio_router.get_metrics(_auth={"uid": UID}, market=MARKET)

    return call


def _set_twrr(monkeypatch, curve):
    """`build_return_pct_curve` 를 갈아끼운다. 예외 클래스를 주면 그것을 던진다."""
    def fake(*a, **kw):
        if isinstance(curve, BaseException):
            raise curve
        return curve, None, None, None, None

    monkeypatch.setattr(portfolio_router, "build_return_pct_curve", fake)


# ── 실패 경로 ─────────────────────────────────────────────────────────────────

def test_twrr_exception_blanks_total_return(metrics_call, monkeypatch):
    """TWRR 계산이 터지면 total_return_pct 는 None 이다 — 보정 전 값을 남기지 않는다."""
    _set_twrr(monkeypatch, RuntimeError("boom"))

    metrics = metrics_call()

    assert metrics["total_return_pct"] is None, (
        f"total_return_pct={metrics['total_return_pct']!r}, expected None -- "
        "the pre-correction value is known to be distorted by deposits, so "
        "shipping it shows the user a wrong number that looks right. "
        "(계산 불가는 '—' 로 보여야 한다.)"
    )


def test_twrr_all_nan_blanks_total_return(metrics_call, monkeypatch):
    """행은 있는데 전량 NaN 인 곡선도 None 이다.

    `twrr.empty` 만 보면 이 곡선은 '비어 있지 않다'. dropna() 후에야 빈 것이
    드러나고, 거기서 `iloc[-1]` 을 부르면 IndexError 가 난다. 그 예외가 위쪽
    except 에 잡혀 None 이 되든 미리 걸러 None 이 되든 결과는 같아야 한다 —
    이 테스트는 결과를 고정한다.
    """
    all_nan = pd.Series([np.nan, np.nan, np.nan], index=_DATES)
    assert not all_nan.empty, "이 테스트의 전제: 행은 있고 값만 없다"

    _set_twrr(monkeypatch, all_nan)

    metrics = metrics_call()

    assert metrics["total_return_pct"] is None, (
        f"total_return_pct={metrics['total_return_pct']!r}, expected None -- "
        "an all-NaN curve is not empty, so an emptiness check alone lets it "
        "through. (전량 NaN 곡선을 값으로 읽으면 안 된다.)"
    )


def test_twrr_empty_curve_blanks_total_return(metrics_call, monkeypatch):
    """아예 빈 곡선도 None 이다."""
    _set_twrr(monkeypatch, pd.Series(dtype=float))

    metrics = metrics_call()

    assert metrics["total_return_pct"] is None, (
        f"total_return_pct={metrics['total_return_pct']!r}, expected None"
    )


# ── 실패가 번지지 않는다 ───────────────────────────────────────────────────────

@pytest.mark.parametrize("broken", [
    pytest.param(RuntimeError("boom"), id="exception"),
    pytest.param("all_nan", id="all-nan"),
])
def test_other_metrics_survive_twrr_failure(metrics_call, monkeypatch, broken):
    """TWRR 이 실패해도 나머지 지표는 그대로다.

    보정 하나가 실패했다고 총자산·베타·일간변동까지 사라지면, 화면 전체가
    '—' 가 되어 사용자는 서비스가 고장난 것으로 읽는다. 지금은 metrics 위에
    한 키만 덮어쓰는 구조라 그럴 일이 없는데, 나중에 누가 except 범위를 넓히면
    조용히 깨진다. 그래서 고정해 둔다.
    """
    if broken == "all_nan":
        broken = pd.Series([np.nan, np.nan, np.nan], index=_DATES)
    _set_twrr(monkeypatch, broken)

    metrics = metrics_call()

    for key, expected in _BASE_METRICS.items():
        if key == "total_return_pct":
            continue
        assert metrics[key] == expected, (
            f"{key}={metrics[key]!r}, expected {expected!r} -- a failed TWRR "
            "correction must not invalidate the other metrics. "
            "(보정 실패가 다른 지표까지 무효화하면 안 된다.)"
        )


# ── 대조군 ────────────────────────────────────────────────────────────────────

def test_valid_twrr_overwrites_total_return(metrics_call, monkeypatch):
    """정상 곡선이면 마지막 값으로 덮어쓴다.

    대조군이 없으면 위 세 개는 "언제나 None 을 준다" 는 구현으로도 전부
    통과한다. 그건 계산 불가를 숨기는 것과 똑같이 나쁘다 — 이번엔 반대 방향으로.
    """
    _set_twrr(monkeypatch, pd.Series([0.0, 5.0, 17.8912], index=_DATES))

    metrics = metrics_call()

    assert metrics["total_return_pct"] == pytest.approx(17.8912), (
        f"total_return_pct={metrics['total_return_pct']!r}, expected 17.8912"
    )


def test_trailing_nan_uses_last_real_value(metrics_call, monkeypatch):
    """마지막 몇 칸이 NaN 이면 그 앞의 실제 관측치를 쓴다 — NaN 이나 None 이 아니다.

    곡선 끝에 아직 값이 안 찬 행이 붙는 것은 흔하다. 그때마다 수익률을 비우면
    멀쩡한 값이 있는데도 '—' 가 뜬다.
    """
    _set_twrr(monkeypatch, pd.Series([1.0, 9.5, np.nan], index=_DATES))

    metrics = metrics_call()

    assert metrics["total_return_pct"] == pytest.approx(9.5), (
        f"total_return_pct={metrics['total_return_pct']!r}, expected 9.5 -- "
        "a trailing NaN must not discard the last real observation."
    )

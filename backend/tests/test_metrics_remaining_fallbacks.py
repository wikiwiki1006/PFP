"""
`portfolio_calculator` 의 남은 폴백 분기들.

폴백 감사에서 이 모듈의 분기 14개 중 12개가 미실행이었다. 앞선 두 파일이
8개를 덮었고, 여기서 나머지를 채운다.

전부 **한 번도 실행된 적이 없다**는 공통점이 있다. 그 상태의 위험은 두 가지다:
그 안의 오타가 안 보이는 것(이미 세 번 났다), 그리고 **폴백이 돌려주는 값이
실제로 무엇인지 아무도 확인한 적이 없다**는 것. 후자가 이 파일의 초점이다 —
계산 불가를 `0` 으로 돌려주면 화면은 그것을 측정값으로 그린다 (§1.3).
"""
from __future__ import annotations

import logging
from unittest import mock

import numpy as np
import pandas as pd
import pytest

from backend.services import portfolio_calculator as pc

_IDX = pd.bdate_range("2026-01-05", periods=180)
_HOLDINGS = {"AAPL": {"q": 10, "avg": 165.4, "sector": "Tech"}}
_PRICES = pd.DataFrame({"AAPL": [165.4] * 100 + [155.28] * 80}, index=_IDX)


def _metrics(close_df: pd.DataFrame, **kw) -> dict:
    curve = pc.build_equity_curve(_HOLDINGS, [], close_df)
    return pc.calculate_metrics(_HOLDINGS, close_df, curve, **kw)


# ── 알파: 재지 못하면 None 이다 ────────────────────────────────────────────────
#
# 0.0 은 "벤치마크와 정확히 같은 성과" 라는 **주장**이다. 재지 못한 것과
# 같을 수 없다. 베타는 이미 None 을 돌려주는데, 같은 함수 안에서 알파만
# 다르게 굴면 화면의 두 칸이 서로 다른 규칙으로 그려진다.

def test_alpha_is_none_without_a_benchmark_column():
    """벤치마크 열이 없으면 알파는 None 이다."""
    assert _metrics(_PRICES)["alpha_vs_benchmark"] is None, (
        "alpha came back as a number with no benchmark to compare against -- "
        "0.0 claims the portfolio exactly matched the market. "
        "(잴 대상이 없는 것과 같은 것은 다르다.)"
    )


def test_alpha_is_none_when_the_computation_fails():
    """벤치마크는 있는데 계산이 터져도 None 이다. `except: pass` 가 0 을 남기면 안 된다."""
    junk = _PRICES.copy()
    junk["^GSPC"] = ["not a number"] * len(_IDX)

    assert _metrics(junk)["alpha_vs_benchmark"] is None


def test_alpha_is_a_number_when_it_can_be_measured():
    """대조군 — 잴 수 있으면 숫자가 나온다.

    없으면 위 둘은 "알파를 아예 안 준다" 는 구현으로도 통과한다.
    """
    with_bm = _PRICES.copy()
    with_bm["^GSPC"] = np.linspace(4000.0, 4400.0, len(_IDX))

    alpha = _metrics(with_bm)["alpha_vs_benchmark"]
    assert isinstance(alpha, float), f"alpha={alpha!r} — 잴 수 있는데 비었다"


# ── 일변동 primitive 가 실패하면 곡선 기반 폴백으로 내려간다 ───────────────────

def test_daily_change_primitive_failure_logs_and_falls_back(caplog):
    """primitive 가 터지면 **로그를 남기고** 에쿼티 곡선으로 계산한다.

    폴백 경로는 ffill 로 복제된 유령 행을 구분하지 못해 0% 를 낼 수 있다.
    어느 쪽 값이 화면에 떴는지는 그 로그로만 구별되므로, 로그가 없으면
    '진짜 보합' 과 '폴백이 낸 0' 이 영영 같아진다.
    """
    raw = pd.DataFrame({"AAPL": [165.4] * 30}, index=_IDX[-30:])

    with mock.patch("backend.services.price_series.portfolio_daily_change",
                    side_effect=RuntimeError("primitive down")), \
         caplog.at_level(logging.WARNING):
        out = _metrics(_PRICES, raw_df=raw)

    assert out, "폴백이 지표를 통째로 잃었다"
    assert caplog.records, (
        "the daily-change primitive failed and nothing was logged -- the "
        "screen cannot tell a real flat day from a fallback that produced 0%. "
        "(어느 경로로 나온 값인지 구별할 단서가 없다.)"
    )


def test_metrics_survive_a_broken_trading_day_calendar():
    """거래일 캘린더를 못 읽어도 지표는 나온다.

    `is_us_trading_day` 가 터지면 곡선 인덱스를 그대로 쓴다. 이 폴백이 없으면
    캘린더 하나 때문에 화면 전체가 빈다.
    """
    with mock.patch("backend.services.market_calendar.is_us_trading_day",
                    side_effect=RuntimeError("calendar down")):
        out = _metrics(_PRICES)

    assert out.get("total_equity") is not None, "캘린더 실패가 지표를 통째로 없앴다"


def test_trim_survives_a_broken_session_calendar():
    """세션 컷오프를 못 구하면 프레임을 자르지 않고 그대로 쓴다.

    여기서 빈 프레임을 돌려주면 곡선도 지표도 통째로 사라진다. 자르지 못한
    것과 볼 것이 없는 것은 다르다.
    """
    with mock.patch("backend.services.market_calendar.is_us_market_open",
                    side_effect=RuntimeError("calendar down")):
        out = pc._trim_to_session(_PRICES, "US")

    assert out.equals(_PRICES), (
        f"the frame was altered when the session calendar failed: {len(out)} "
        f"rows vs {len(_PRICES)} -- failing to trim is not the same as having "
        "nothing to show. (자르지 못한 것과 비어 있는 것은 다르다.)"
    )


# ── 팩터 회귀: 데이터가 모자라면 빈 dict ───────────────────────────────────────

@pytest.mark.parametrize("close_df, returns, why", [
    (pd.DataFrame({"AAPL": [1.0] * 180}, index=_IDX),
     pd.Series(np.random.default_rng(0).normal(0, 0.01, 180), index=_IDX),
     "벤치마크 열이 없다"),
    (pd.DataFrame({"^GSPC": np.linspace(4000.0, 4400.0, 180)}, index=_IDX),
     pd.Series(np.random.default_rng(0).normal(0, 0.01, 30), index=_IDX[:30]),
     "겹치는 구간이 60일 미만이다"),
    (pd.DataFrame({"^GSPC": ["x"] * 180}, index=_IDX),
     pd.Series(np.random.default_rng(0).normal(0, 0.01, 180), index=_IDX),
     "벤치마크를 숫자로 읽을 수 없다"),
])
def test_factor_analysis_returns_empty_not_zeros(close_df, returns, why):
    """재지 못하면 빈 dict 다. 0 으로 채운 dict 가 아니다.

    `{"market_beta": 0.0, "r_squared": 0.0}` 은 "시장과 무관하고 설명력이
    없다" 는 측정 결과처럼 읽힌다. 빈 dict 만이 "재지 못했다" 를 말한다.
    """
    out = pc.factor_analysis(returns, close_df)

    assert out == {}, (
        f"{why}: got {out!r} -- zeros read as a measurement of no exposure. "
        "(계산 불가를 0 으로 채우면 측정값처럼 보인다.)"
    )


def test_factor_analysis_measures_when_it_can():
    """대조군 — 데이터가 충분하면 실제 값이 나와야 한다.

    지금은 **어떤 입력으로도** 빈 dict 가 나온다. 그래서 위의 '빈 dict 여야
    한다' 검사 세 개는 전부 통과하지만 아무것도 증명하지 못한다 — 함수가
    올바르게 판단해서 비운 것인지, 애초에 아무것도 못 하는 것인지 구별되지
    않는다. 대조군이 그 둘을 가른다.
    """
    rng = np.random.default_rng(1)
    mkt = pd.Series(rng.normal(0, 0.01, 180), index=_IDX)
    close_df = pd.DataFrame({"^GSPC": 4000.0 * (1 + mkt).cumprod()}, index=_IDX)
    port = mkt * 1.2 + rng.normal(0, 0.001, 180)

    out = pc.factor_analysis(port, close_df)

    assert out, "충분한 데이터인데 빈 dict 가 나왔다 — 위 검사들이 무의미해진다"
    assert set(out) >= {"alpha_annualized", "market_beta", "r_squared"}
    assert 0.9 < out["market_beta"] < 1.5, f"market_beta={out['market_beta']}"

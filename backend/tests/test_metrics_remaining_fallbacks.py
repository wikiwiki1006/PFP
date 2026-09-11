"""
`portfolio_calculator` 의 남은 폴백 분기들.

폴백 감사에서 이 모듈의 분기 14개 중 12개가 미실행이었다. 앞선 두 파일이
8개를 덮었고, 여기서 나머지를 채운다.

전부 **한 번도 실행된 적이 없다**는 공통점이 있다. 그 상태의 위험은 두 가지다:
그 안의 오타가 안 보이는 것(이미 세 번 났다), 그리고 **폴백이 돌려주는 값이
실제로 무엇인지 아무도 확인한 적이 없다**는 것. 후자가 이 파일의 초점이다 —
계산 불가를 `0` 으로 돌려주면 화면은 그것을 측정값으로 그린다 (§1.3).

위 개수는 **처음 감사할 때의 것**이고 지금 이 파일의 내용과 다르다.
`factor_analysis` 를 덮던 네 검사를 지웠다 — 그 함수가 삭제됐기 때문이다
(호출자 0곳. 라우터가 부르던 `factor_analysis` 는 `services/optimizer.py`
쪽의 **이름만 같은 다른 함수**다).
"""
from __future__ import annotations

import contextlib
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


# ── 한 지표가 실패해도 나머지는 살아남는다 ─────────────────────────────────────
#
# 이 성질은 원래 `test_metrics_twrr_unknown.py` 가 지키고 있었다. 그 파일은
# `/metrics` 의 TWRR 덮어쓰기를 검증했는데, 그 보정이 고치려던 왜곡이
# `calculate_metrics` 쪽 정의가 바뀌면서 사라졌다 — 보정만 남아 맞는 값을
# 다른 정의의 값으로 되돌리고 있었다. 보정이 삭제되면 그 파일은 검증 대상을
# 잃지만, **이 성질 자체는 계속 값어치가 있다.**
#
# 보정이 아니라 지표 산출 자체에 붙인다. 여기가 더 나은 자리인 이유는 실패할
# 수 있는 지점이 하나가 아니기 때문이다 — 베타·알파·일변동이
# 각각 독립적으로 죽을 수 있고, **어느 하나가 죽어도 화면 전체가 '—' 가 되면
# 안 된다.** 사용자는 그걸 "서비스 고장" 으로 읽는다.
#
# 고정 기대값과 비교하지 않고 **정상 실행 결과와 비교**한다. 기대값을 적어
# 두면 지표가 하나 늘거나 이름이 바뀔 때 이 검사가 그 변화를 막는 장애물이
# 된다 — 재려는 것은 값이 무엇인지가 아니라 **실패가 번지지 않는지**다.

def _healthy_prices() -> pd.DataFrame:
    """지표가 **퇴화하지 않는** 프레임.

    `_PRICES` 는 뒤쪽 80일이 평평해서 `perf_1w`·`perf_1m`·일변동이 전부
    0.0 이 된다. 그 위에서 "실패가 값을 바꿨나" 를 물으면 바뀐 것과 원래
    0 인 것이 같아 보인다. 그래서 여기서만 움직이는 계열을 쓴다.
    """
    rng = np.random.default_rng(7)
    px = 165.4 * np.cumprod(1 + rng.normal(0.0003, 0.011, len(_IDX)))
    return pd.DataFrame({"AAPL": px,
                         "^GSPC": np.linspace(4000.0, 4400.0, len(_IDX))},
                        index=_IDX)


def _recent(prices: pd.DataFrame) -> pd.DataFrame:
    """`/metrics` 가 넘기는 희소 실시간 프레임 자리."""
    return prices[["AAPL"]].tail(30)


@contextlib.contextmanager
def _no_patch():
    yield


def _break_the_daily_change_primitive(prices):
    """호출부가 `try` 로 감싼 자리 — 예외 주입이 **실제로 일어날 수 있는** 상태다."""
    return prices, mock.patch(
        "backend.services.price_series.portfolio_daily_change",
        side_effect=RuntimeError("injected failure"))


def _make_the_benchmark_unreadable(prices):
    """베타·알파는 스스로 삼킨다 — 그래서 예외가 아니라 **데이터**로 깨뜨린다.

    호출부(`beta = calculate_portfolio_beta(...)`)는 맨몸이고 `try` 는 함수
    **안**에 있다. 그 함수를 던지게 바꾸면 일어날 수 없는 상태를 재게 된다.
    지수 열이 숫자가 아닌 것은 실제로 나오는 형태다 (yfinance 가 빈/문자 열).
    """
    junk = prices.copy()
    junk["^GSPC"] = ["not a number"] * len(junk)
    return junk, _no_patch()


# `may_change` 는 **그 실패가 소유한 필드**다. 관측된 차이가 아니라 선언이다 —
# 관측만 적으면 이 검사는 오늘 동작의 사본이 되고, 실패가 번져도 그 사본을
# 같이 고치면 통과한다.
#
# `is_us_trading_day` 를 여기 넣으려다 뺐다. `calculate_metrics` 안의 그
# 호출부는 `try` 로 감싸여 있지만, **더 이른 맨몸 경로**가 같은 함수를 먼저
# 부른다: `_priced_holdings → price_series.daily_change → us_price_cutoff`.
# 그래서 그 함수를 전역으로 던지게 하면 지표가 통째로 죽는다 — 즉 "한 지표만
# 실패" 가 아니다. 미국 캘린더는 규칙 계산이라 던질 일도 없다. 두 이유 모두
# "일어날 수 없는 상태" 라서 뺐다.
_FAILURES = [
    pytest.param(_break_the_daily_change_primitive,
                 {"today_change_pct", "as_of", "change_counted",
                  "change_holdings", "change_stale"},
                 id="일변동-primitive-실패"),
    pytest.param(_make_the_benchmark_unreadable,
                 {"alpha_vs_benchmark", "portfolio_beta",
                  "benchmark", "benchmark_label"},
                 id="벤치마크-판독-실패"),
]


@pytest.mark.parametrize("break_it, may_change", _FAILURES)
def test_one_failing_metric_does_not_invalidate_the_others(break_it, may_change):
    """한 계산이 실패해도 나머지 지표는 정상 실행과 **같은 값**이어야 한다.

    지금은 각 실패가 자기 자리에 갇혀 있는데, **나중에 누가 `except` 범위를
    넓히면 조용히 깨진다.** 그때 화면은 한 칸이 아니라 전체가 '—' 가 되고,
    사용자는 그것을 "서비스 고장" 으로 읽는다. 원인은 넓힌 `except` 한 줄이다.

    고정 기대값이 아니라 **정상 실행 결과**와 비교한다. 기대값을 적어 두면
    지표가 하나 늘거나 이름이 바뀔 때 이 검사가 그 변화를 막는 장애물이 된다
    — 재려는 것은 값이 무엇인지가 아니라 **실패가 번지지 않는지**다.
    """
    healthy = _healthy_prices()
    baseline = _metrics(healthy, raw_df=_recent(healthy))
    assert baseline, "정상 실행이 빈 결과다 — 이 검사의 전제가 없다"

    damaged_prices, patch = break_it(healthy)
    with patch:
        damaged = _metrics(damaged_prices, raw_df=_recent(healthy))

    assert damaged, (
        "one broken computation emptied the whole metric set -- it must not "
        "take the screen down with it. (한 계산 실패가 화면 전체를 비웠다.)"
    )

    # 대조군을 검사 안에 둔다. 밖에 두면 표에 항목을 추가한 사람이 대조군을
    # 같이 늘리지 않고, 그 항목은 **아무 실패도 일어나지 않은 상태**를 정상과
    # 비교하며 조용히 통과한다.
    moved = {k for k in may_change if damaged.get(k) != baseline.get(k)}
    assert moved, (
        f"nothing in {sorted(may_change)} changed -- the failure never "
        "happened, so this compared a healthy run with another healthy run."
    )

    unaffected = set(baseline) - may_change
    differing = {k: (baseline[k], damaged.get(k))
                 for k in unaffected if damaged.get(k) != baseline[k]}
    assert not differing, (
        f"the failure changed metrics it does not own: {differing} -- "
        "it spread beyond the values that depend on it."
    )


def test_the_money_totals_survive_every_one_of_those_failures():
    """평가액·원가·수익률은 위 실패 **어느 것에도** 흔들리지 않는다.

    위 검사는 "소유하지 않은 필드" 를 표로 선언해서 잰다. 그 표를 잘못 넓히면
    검사가 조용히 약해지므로, 넓힐 수 없는 몇 개를 따로 못 박는다. 이 셋은
    보유 수량과 가격만으로 나오는 값이라 캘린더·벤치마크·일변동과 무관하다.
    """
    healthy = _healthy_prices()
    baseline = _metrics(healthy, raw_df=_recent(healthy))

    for break_it, _owned in [(p.values[0], p.values[1]) for p in _FAILURES]:
        damaged_prices, patch = break_it(healthy)
        with patch:
            damaged = _metrics(damaged_prices, raw_df=_recent(healthy))
        for key in ("total_equity", "total_cost", "total_return_pct",
                    "stock_value", "cash_value"):
            assert damaged.get(key) == baseline[key], (
                f"{key} moved when {break_it.__name__} was applied: "
                f"{damaged.get(key)!r} vs {baseline[key]!r} -- the money "
                "totals come from quantities and prices alone."
            )

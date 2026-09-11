"""
에쿼티 곡선의 폴백 분기들 — 지금까지 한 번도 실행된 적이 없다.

폴백 감사에서 `portfolio_calculator` 의 분기 10개가 **테스트에서 한 번도
실행되지 않는 것**으로 나왔다. 그중 두 개가 `_fallback()` 을 돌려주는
경로이고, 하나는 매매 이력이 없는 사용자가 늘 타는 길이다.

성공 경로는 요청마다 돌아 오타가 즉시 드러나지만, 이런 분기는 그 상황을
만들어 본 테스트가 없으면 영원히 안 돈다. `market_calendar` 의 평일 폴백이
작성 이후 한 번도 실행되지 않은 채 `NameError` 를 품고 있었던 것과 같은 자리다.

## 그리고 그 감사 중에 부호 뒤집힘이 나왔다

`total_return_pct` 는 **에쿼티 곡선의 첫 양수 지점 대비 현재**로 계산된다.
곡선은 넘겨받은 `close_df` 범위만 덮으므로, 그 범위가 매수 시점을 포함하지
않으면 이 값은 "총 수익률" 이 아니라 **그 창의 수익률**이다.

    보유: 10주 @ 평단 165.4   현재가 155.28   → 실제 -6.12%
    5일 창(100 → 155.28)을 주면  total_return_pct = +55.28%

같은 응답의 `total_cost`(1654) 와 `total_equity`(1552.8) 는 -6.12% 를 가리킨다.
**응답이 자기 자신과 모순된다.** 그리고 이 값은 화면과 AI 프롬프트 양쪽으로
간다 — 6% 손실 중인 포트폴리오를 모델이 55% 수익으로 읽고 조언을 쓴다.

`/analyst-feedback` 이 `period="5d"` 로 받은 프레임을 그대로 넘긴다. 창이
짧은 것 자체는 그쪽의 선택이지만, **그 창으로 정당화할 수 없는 숫자를
"총 수익률" 이라는 이름으로 내보내는 것**은 이 함수의 몫이다.
"""
from __future__ import annotations

import pandas as pd
import pytest

from backend.services.portfolio_calculator import (
    _trim_to_session,
    build_equity_curve,
    calculate_metrics,
)

_HOLDINGS = {"AAPL": {"q": 10, "avg": 165.4, "sector": "Tech"}}
_BUY = [{"date": "2026-01-05", "ticker": "AAPL", "type": "ADD", "q": 10, "price": 165.4}]

# 매수 시점을 덮는 창. 평단 165.4 에서 155.28 로 내려온 상태.
_LONG_IDX = pd.bdate_range("2026-01-05", periods=180)
_LONG_DOWN = pd.DataFrame({"AAPL": [165.4] * 100 + [155.28] * 80}, index=_LONG_IDX)
_LONG_UP = pd.DataFrame({"AAPL": [165.4] * 100 + [200.0] * 80}, index=_LONG_IDX)

# 매수보다 **나중에** 시작하는 짧은 창. /analyst-feedback 이 쓰는 형태.
_SHORT_IDX = pd.bdate_range("2026-09-07", periods=5)
_SHORT_UP = pd.DataFrame({"AAPL": [100.0, 110.0, 130.0, 145.0, 155.28]}, index=_SHORT_IDX)


def _implied_pct(metrics: dict) -> float | None:
    """응답 자신의 원가·평가액이 함의하는 수익률."""
    cost, equity = metrics.get("total_cost"), metrics.get("total_equity")
    if not cost or equity is None:
        return None
    return (equity / cost - 1) * 100


# ── 한 번도 실행된 적 없던 폴백들 ──────────────────────────────────────────────

def test_no_trade_log_still_produces_a_curve():
    """매매 이력이 없으면 현재 수량을 전 구간에 적용한 곡선을 만든다.

    이력을 한 번도 입력하지 않은 사용자가 늘 타는 길인데, 감사에서 이 분기를
    실행하는 테스트가 하나도 없었다.
    """
    curve = build_equity_curve(_HOLDINGS, [], _LONG_DOWN)

    assert not curve.empty, "폴백이 빈 곡선을 돌려줬다 — 화면에 그릴 것이 없다"
    assert curve.iloc[-1] == pytest.approx(1552.8), (
        f"fallback curve ends at {curve.iloc[-1]} -- with 10 shares at 155.28 "
        "it should be 1552.8. (현재 수량 × 가격이 아니다.)"
    )


def test_a_malformed_trade_log_falls_back_instead_of_raising():
    """이력이 망가져 있어도 예외 대신 폴백으로 내려간다.

    `except: return _fallback()` 경로다. 여기서 예외가 새면 포트폴리오 화면
    전체가 뜨지 않는다 — 곡선 하나 때문에 나머지 지표까지 잃는다.
    """
    broken = [{"date": "not-a-date", "ticker": "AAPL", "type": "ADD", "q": "x"}]

    curve = build_equity_curve(_HOLDINGS, broken, _LONG_DOWN)

    assert not curve.empty, (
        "a malformed trade log produced no curve at all -- the fallback exists "
        "so one bad row cannot take the whole screen down."
    )


def test_trim_leaves_a_non_datetime_index_alone():
    """인덱스가 날짜가 아니면 손대지 않고 돌려준다. 예외를 내지 않는다."""
    odd = pd.DataFrame({"AAPL": [1.0, 2.0]}, index=["a", "b"])

    assert _trim_to_session(odd, "US").equals(odd)
    assert _trim_to_session(pd.DataFrame(), "US").empty


@pytest.mark.parametrize("df, why", [
    (pd.DataFrame(), "빈 프레임"),
    (pd.DataFrame({"AAPL": [100.0]}, index=pd.bdate_range("2026-09-07", periods=1)),
     "행이 하나뿐 — 변동을 계산할 수 없다"),
])
def test_metrics_returns_nothing_rather_than_zeros(df, why):
    """계산할 수 없으면 빈 dict 다. 0 으로 채운 dict 가 아니다.

    `{"total_return_pct": 0.0, ...}` 를 돌려주면 화면은 '보합' 을 그린다.
    빈 dict 는 호출자가 "값이 없다" 를 알아볼 수 있는 유일한 형태다 (§1.3).
    """
    out = calculate_metrics(_HOLDINGS, df, pd.Series(dtype=float))

    assert out == {}, (
        f"{why}: got {out!r} -- an uncomputable metric set must come back "
        "empty, not filled with zeros that render as 'flat'. "
        "(계산 불가를 0 으로 채우면 보합으로 보인다.)"
    )


# ── 대조군: 창이 매수를 덮으면 부호가 맞는다 ────────────────────────────────────

@pytest.mark.parametrize("prices, expect_negative, why", [
    (_LONG_DOWN, True, "평단 아래로 내려온 보유"),
    (_LONG_UP, False, "평단 위로 올라간 보유"),
])
def test_return_sign_agrees_when_the_window_covers_the_purchase(prices, expect_negative, why):
    """정상 창에서는 수익률 부호가 원가·평가액과 일치한다.

    대조군이 없으면 아래 검사는 "언제나 부호가 틀린다" 는 구현으로도, 또
    "총 수익률을 아예 안 준다" 는 구현으로도 통과한다.
    """
    metrics = calculate_metrics(_HOLDINGS, prices,
                                build_equity_curve(_HOLDINGS, _BUY, prices))
    rtn = metrics["total_return_pct"]
    implied = _implied_pct(metrics)

    assert rtn is not None and implied is not None, f"{why}: 값이 비었다 — 전제가 없다"
    assert (rtn < 0) is expect_negative, f"{why}: total_return_pct={rtn}"
    assert (rtn < 0) == (implied < 0), (
        f"{why}: total_return_pct={rtn} but cost/equity imply {implied:+.2f}%"
    )


# ── 베타와 LIVE 배지의 폴백 — 역시 한 번도 실행된 적이 없었다 ──────────────────
#
# 베타는 `None` 과 `0.0` 이 전혀 다른 말이다. 0 은 "시장과 무관하게 움직인다"
# 는 **측정 결과**이고, None 은 "재지 못했다" 이다. 화면은 전자를 숫자로,
# 후자를 '—' 로 그린다.

def test_beta_is_none_when_there_is_nothing_to_measure():
    """벤치마크와 맞댈 종목이 없으면 베타는 None 이다. 0.0 이 아니다."""
    from backend.services.portfolio_calculator import calculate_portfolio_beta

    only_cash = {"CASH": {"q": 1000.0, "avg": 1.0, "sector": "Cash"}}
    with_benchmark = pd.DataFrame(
        {"^GSPC": [4000.0 + i for i in range(len(_LONG_IDX))]}, index=_LONG_IDX)

    assert calculate_portfolio_beta(only_cash, with_benchmark, "^GSPC") is None, (
        "beta came back as a number for a portfolio with no stocks -- 0.0 "
        "reads as 'moves independently of the market', which is a measurement, "
        "not an admission that nothing could be measured. (0 과 모름은 다르다.)"
    )


def test_beta_is_none_when_the_computation_raises():
    """계산이 터져도 예외가 아니라 None 이다 — 다른 지표까지 잃지 않는다."""
    from backend.services.portfolio_calculator import calculate_portfolio_beta

    junk = pd.DataFrame({"^GSPC": ["a"] * len(_LONG_IDX),
                         "AAPL": ["b"] * len(_LONG_IDX)}, index=_LONG_IDX)

    assert calculate_portfolio_beta(_HOLDINGS, junk, "^GSPC") is None


def test_live_badge_is_off_when_the_calendar_cannot_answer(monkeypatch):
    """캘린더를 못 읽으면 'LIVE' 배지는 꺼진다.

    켜진 채로 두면 사용자는 멈춘 숫자를 실시간으로 읽는다. 모르면 끄는 쪽이
    맞고, 그 판단이 실제로 도는지 여기서 확인한다.
    """
    from backend.services import market_calendar
    from backend.services.portfolio_calculator import _market_open_flag

    def boom(*a, **kw):
        raise RuntimeError("calendar unavailable")

    monkeypatch.setattr(market_calendar, "is_us_market_open", boom)
    monkeypatch.setattr(market_calendar, "is_kr_market_open", boom)

    assert _market_open_flag("US") is False
    assert _market_open_flag("KR") is False


# ── 부호 뒤집힘 ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("trades, why", [
    (_BUY, "매매 이력 있음"),
    ([], "매매 이력 없음 — _fallback 경로"),
])
def test_return_does_not_contradict_cost_and_equity(trades, why):
    """수익률이 같은 응답의 원가·평가액과 반대 방향을 가리키면 안 된다.

    크기가 다른 것은 정상이다 — TWRR 과 단순 수익률은 다른 값이다. 하지만
    **6% 손실 중인 포트폴리오가 상승으로 보고되는 것**은 어떤 정의로도 맞지
    않는다. 값을 못 구하겠으면 `None` 이 답이다.
    """
    metrics = calculate_metrics(_HOLDINGS, _SHORT_UP,
                                build_equity_curve(_HOLDINGS, trades, _SHORT_UP))
    rtn = metrics["total_return_pct"]
    implied = _implied_pct(metrics)

    assert implied is not None and implied < 0, "전제: 원가 대비 손실 상태다"
    assert rtn is None or (rtn < 0) == (implied < 0), (
        f"{why}: total_return_pct={rtn:+.2f}% while this same response's "
        f"total_cost/total_equity imply {implied:+.2f}% -- the number "
        "contradicts the two fields beside it. (응답이 자기 자신과 모순된다.)"
    )

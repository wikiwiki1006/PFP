"""
backend/tests/test_pairs_spread.py
────────────────────────────────────
페어 트레이딩 자동탐색(pairs_auto_detail) 스프레드 계산 회귀 테스트.

버그: 스프레드를 표시 구간 '첫날'에 고정 인덱싱해 계산했다. 그러면 과거 어느
시점에 딱 한 번 발생한 괴리가 그 이후 모든 날짜에 영구히 더해져, 실제로는 두
종목이 다시 같은 방향으로 동행하고 있어도 스프레드가 그 괴리 폭 근처에 계속
머물러(사실상 '누적'돼) 보였다. 롤링 평균 기준으로 바꿔 오래된 괴리가 시간이
지나면 창 밖으로 밀려나도록 고쳤다.

네트워크·DB 불필요 (합성 프레임만 사용).
"""
import numpy as np
import pandas as pd
import pytest

from backend.services.trading_signals import pairs_auto_detail, pairs_trading_signal

N = 400
IDX = pd.bdate_range("2023-01-01", periods=N)
JUMP_DAY = 150
JUMP_FACTOR = 0.80  # B 가 이 날 딱 한 번 20% 재평가된다


def _jump_frame() -> pd.DataFrame:
    rng = np.random.default_rng(7)
    daily_ret = rng.normal(0.0003, 0.01, N)
    a = 100.0 * np.cumprod(1.0 + daily_ret)
    b = a.copy()
    b[JUMP_DAY:] *= JUMP_FACTOR   # 그 날 이후로는 다시 A 와 완전히 동행(비율 고정)
    return pd.DataFrame({"A": a, "B": b}, index=IDX)


def test_one_time_jump_does_not_permanently_offset_spread():
    close = _jump_frame()
    result = pairs_auto_detail("A", close, ["B"], threshold_pct=5.0, top_n=5)

    assert result["best"] is not None
    assert result["best"]["ticker"] == "B"

    chart = result["charts"]["B"]
    spread_by_date = {row["date"]: row["spread"] for row in chart}

    jump_date = IDX[JUMP_DAY].strftime("%Y-%m-%d")
    long_after_date = IDX[JUMP_DAY + 120].strftime("%Y-%m-%d")
    assert jump_date in spread_by_date
    assert long_after_date in spread_by_date

    # 점프 직후엔 괴리가 크게 잡혀야 한다 — 이상 신호 자체는 여전히 감지돼야 한다.
    assert abs(spread_by_date[jump_date]) > 10

    # 롤링 창(60일)이 점프를 완전히 지나간 뒤에는 스프레드가 0 근방으로 돌아온다.
    # A, B 는 점프 이후 다시 완전히 동행하므로(비율이 상수) 실질적으로 0이어야 한다.
    assert abs(spread_by_date[long_after_date]) < 1.0

    # 참고용 대조: 예전 방식(표시 구간 첫날에 고정 인덱싱)이었다면 이 시점에도
    # 여전히 ~20%p 근처에 머물렀을 것 — 그 영구 잔존이 없어졌음을 명시적으로 확인한다.
    old_idx_a = close["A"] / float(close["A"].iloc[0]) * 100
    old_idx_b = close["B"] / float(close["B"].iloc[0]) * 100
    old_spread_long_after = float((old_idx_a - old_idx_b).loc[IDX[JUMP_DAY + 120]])
    assert abs(old_spread_long_after) > 8


def test_breach_is_recorded_at_the_transient_spike():
    close = _jump_frame()
    result = pairs_auto_detail("A", close, ["B"], threshold_pct=5.0, top_n=5)

    breaches = result["all_breaches"]["B"]
    assert len(breaches) >= 1
    jump_date = IDX[JUMP_DAY].strftime("%Y-%m-%d")
    # 점프 부근에서 임계값 초과가 최소 한 번은 기록돼야 한다
    assert any(abs(pd.Timestamp(b["date"]) - pd.Timestamp(jump_date)) <= pd.Timedelta(days=5)
               for b in breaches)


def test_no_common_history_returns_empty():
    close = pd.DataFrame({"A": [1.0, 2.0]}, index=IDX[:2])
    result = pairs_auto_detail("A", close, ["B"], threshold_pct=5.0, top_n=5)
    assert result == {"matches": [], "best": None}


def test_minimum_history_length_does_not_crash():
    """common 이 가드 최소치(60일)에 딱 걸치는 경우 — window 가 축소돼도 예외가 나면 안 된다."""
    n = 60
    idx = pd.bdate_range("2024-01-01", periods=n)
    rng = np.random.default_rng(3)
    a = 100.0 * np.cumprod(1.0 + rng.normal(0, 0.01, n))
    b = a * 1.05
    close = pd.DataFrame({"A": a, "B": b}, index=idx)

    result = pairs_auto_detail("A", close, ["B"], threshold_pct=5.0, top_n=5)
    assert result["best"] is not None
    # window 가 축소된 채로도 스프레드가 계산돼야 한다(빈 리스트가 아니어야 함)
    assert len(result["charts"]["B"]) > 0


# ══════════════════════════════════════════════════════════════════════════════
# 자동 탐색이 고른 페어를 게이트가 거부하지 않는다
#
# `pairs_auto_detail` 이 고른 1위는 곧바로 `pairs_trading_signal` 로 들어간다.
# 그런데 탐색은 **변동성 유사도**로, 그것도 `abs()` 로 정렬하고 게이트는
# **수익률 상관**을 부호 그대로 본다(`>= 0.70`). 두 값이 다르니 시스템이
# 자기가 고른 1위 페어에 자기가 `낮은 상관계수 경고` 를 띄웠다.
#
#   실측 (프로덕션 60종목 풀)
#     1위가 게이트 통과      15/60 → 25/60
#     상위 5 에 음수 섞임     4/60 → 0/60
#     AAPL 기존 1위 AMAT     변동성 +0.638 / 수익률 -0.125
#
# 두 값이 **다시 갈라지는 것**이 원래 결함이므로, 아래 첫 테스트가 그 동등성
# 자체를 고정한다. 지금 두 곳이 각자 계산하고 공유 함수가 없어서, "같은 함수를
# 쓴다" 를 단언하면 코드가 하지 않는 약속을 적는 꼴이 된다 — 대신 **같은
# 입력에 같은 값이 나오는지**를 잰다.
# ══════════════════════════════════════════════════════════════════════════════

def _pair_frame(rho: float, n: int = 300, seed: int = 11) -> pd.DataFrame:
    """A 와 지정한 수익률 상관을 갖는 B 를 만든다.

    `rho` 가 음수면 역상관 페어다 — 게이트가 반드시 거부하는 종류.
    """
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2023-01-01", periods=n)
    ra = rng.normal(0.0003, 0.012, n)
    noise = rng.normal(0.0003, 0.012, n)
    rb = rho * ra + np.sqrt(max(0.0, 1.0 - rho * rho)) * noise
    return pd.DataFrame(
        {"A": 100.0 * np.cumprod(1.0 + ra), "B": 100.0 * np.cumprod(1.0 + rb)},
        index=idx,
    )


def test_the_reported_correlation_is_the_one_the_gate_will_use():
    """탐색이 보고하는 상관과 게이트가 재는 상관이 같아야 한다.

    이 둘이 갈라지는 것이 원래 결함이다. 값이 조금이라도 다르면 경계선
    (0.70) 근처에서 탐색은 통과라 보고 게이트는 거부하는 페어가 생긴다.
    """
    close = _pair_frame(0.85)
    auto = pairs_auto_detail("A", close, ["B"], threshold_pct=5.0, top_n=5)
    gate = pairs_trading_signal(close["A"], close["B"])

    assert auto["best"]["correlation"] == pytest.approx(gate["correlation"], abs=1e-4), (
        f"auto detection reports {auto['best']['correlation']} while the gate "
        f"measures {gate['correlation']} -- the search must rank by the value "
        "the gate will judge, or it hands over pairs the gate then rejects. "
        "(두 값이 갈라지면 시스템이 자기가 고른 페어를 자기가 거부한다.)"
    )


def test_an_inversely_correlated_pair_is_not_ranked_first():
    """역상관 페어가 1위로 올라오면 안 된다.

    `abs()` 로 정렬하면 -0.40 이 +0.30 을 이긴다. 그런데 게이트는
    `-0.40 >= 0.70` 이 거짓이라 **그 페어를 반드시 거부한다** — 탐색이 고를 수
    있는 것 중 최악을 고르는 셈이다.
    """
    rng = np.random.default_rng(5)
    idx = pd.bdate_range("2023-01-01", periods=300)
    ra = rng.normal(0.0003, 0.012, 300)
    noise = rng.normal(0.0003, 0.012, 300)

    def series(rho):
        rb = rho * ra + np.sqrt(max(0.0, 1.0 - rho * rho)) * noise
        return 100.0 * np.cumprod(1.0 + rb)

    close = pd.DataFrame({
        "A": 100.0 * np.cumprod(1.0 + ra),
        "INVERSE": series(-0.85),      # 절대값은 크지만 게이트가 거부한다
        "MILD":    series(0.40),       # 절대값은 작지만 방향이 맞다
    }, index=idx)

    result = pairs_auto_detail("A", close, ["INVERSE", "MILD"], top_n=5)

    assert result["best"]["ticker"] == "MILD", (
        f"ranked {result['best']['ticker']} first -- a strongly inverse pair "
        "wins on |correlation| and is then rejected by the gate every time. "
        "(부호를 버리면 반드시 거부될 페어가 1위가 된다.)"
    )
    # 음수를 **걸러내지는 않는다** — 부호 그대로 내림차순 정렬할 뿐이다.
    # 후보가 둘뿐이면 역상관 페어도 2위로 목록에 남는다. 그러니 "음수가 없다"
    # 가 아니라 **"음수가 양수보다 위로 못 온다"** 가 코드가 실제로 보장하는
    # 것이고, 그게 게이트와 어긋나지 않게 하는 성질이다.
    corrs = [m["correlation"] for m in result["matches"]]
    assert corrs == sorted(corrs, reverse=True), (
        f"the match list is not ordered by signed correlation: {corrs} -- "
        "ordering is the only thing keeping a rejected pair below an "
        "acceptable one. (부호 그대로의 내림차순이 유일한 방어선이다.)"
    )


def test_a_well_correlated_pair_is_ranked_first():
    """대조군 — 상관이 높은 페어는 1위로 올라온다.

    없으면 위 검사는 "아무것도 1위로 안 올린다" 는 구현으로도 통과한다.
    역상관을 거르는 것이 목적이지 고르지 않는 것이 목적이 아니다.
    """
    rng = np.random.default_rng(9)
    idx = pd.bdate_range("2023-01-01", periods=300)
    ra = rng.normal(0.0003, 0.012, 300)
    noise = rng.normal(0.0003, 0.012, 300)

    def series(rho):
        rb = rho * ra + np.sqrt(max(0.0, 1.0 - rho * rho)) * noise
        return 100.0 * np.cumprod(1.0 + rb)

    close = pd.DataFrame({
        "A": 100.0 * np.cumprod(1.0 + ra),
        "TIGHT": series(0.90),
        "LOOSE": series(0.20),
    }, index=idx)

    result = pairs_auto_detail("A", close, ["LOOSE", "TIGHT"], top_n=5)

    assert result["best"]["ticker"] == "TIGHT", result["matches"]


def test_the_top_pick_passes_the_gate_when_such_a_pair_exists():
    """게이트를 통과할 페어가 후보에 있으면 1위가 그것이어야 한다.

    이게 사용자가 겪는 성질이다 — 자동 탐색이 골라 준 페어를 열었더니
    "낮은 상관계수 경고" 가 떠 있는 상태를 막는다.
    """
    rng = np.random.default_rng(13)
    idx = pd.bdate_range("2023-01-01", periods=300)
    ra = rng.normal(0.0003, 0.012, 300)
    noise = rng.normal(0.0003, 0.012, 300)

    def series(rho):
        rb = rho * ra + np.sqrt(max(0.0, 1.0 - rho * rho)) * noise
        return 100.0 * np.cumprod(1.0 + rb)

    # 거부될 후보의 **절대값을 더 크게** 둔다. 그래야 `abs()` 정렬로
    # 되돌아갔을 때 이 테스트가 실제로 갈린다 — 처음에는 통과 후보의 절대값이
    # 더 커서, abs 정렬에서도 같은 답이 나와 아무것도 못 잡았다.
    close = pd.DataFrame({
        "A": 100.0 * np.cumprod(1.0 + ra),
        "PASSES": series(0.80),
        "FAILS":  series(-0.95),
    }, index=idx)

    best = pairs_auto_detail("A", close, ["FAILS", "PASSES"], top_n=5)["best"]
    signal = pairs_trading_signal(close["A"], close[best["ticker"]])

    assert signal["lock_message"] is None, (
        f"auto detection picked {best['ticker']} and the gate immediately "
        f"warned about it: {signal['lock_message']} -- a gate-passing "
        "candidate was available. (시스템이 자기가 고른 페어를 자기가 경고한다.)"
    )

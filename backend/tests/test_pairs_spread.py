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

from backend.services.trading_signals import pairs_auto_detail

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

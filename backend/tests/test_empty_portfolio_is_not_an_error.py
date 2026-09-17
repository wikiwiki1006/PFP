"""
보유가 없는 것은 **새 사용자의 정상 상태**다 (§1.3 마지막 항목).

CLAUDE.md 가 이름까지 들어 경고하는 자리인데 검사가 없었다 — *"빈 값을
실패로 취급하지 마라. `if holdings:` 는 '보유 없음' 인 사용자를 실패 경로로
보낸다."* 실제로 `/metrics` 가 400 을 던지고 있었고, 그걸 200 으로 바꾼 뒤에도
**게이트 숫자가 안 움직였다.** 되돌려도 아무도 모른다는 뜻이다.

## 두 시장을 다 본다

벤치마크는 보유와 무관하게 시장이 정한다. 빈 응답이라고 미국 지수를 그대로
내보내면 한국 사용자의 빈 화면에 S&P 500 이 범례로 뜬다 (§1.1).

## 값의 종류를 가른다

금액은 **0 이 참이다** — 아무것도 없는 사용자의 자산은 실제로 0 이지 "모름"
이 아니다. 반대로 수익률·베타는 분모가 없어 계산 불가이므로 `None` 이다.
0 으로 채우면 '본전'·'시장과 같은 변동성' 이라는 단정이 된다 (§1.3 (a)).

## 모양이 갈리는 것도 잰다

빈 응답과 실제 응답이 **다른 키 집합**을 내보내면, 프론트는 빈 계정에서만
`undefined` 를 만난다. 그건 "값이 없다" 가 아니라 "필드가 없다" 라서 옵셔널
체이닝 하나가 빠지면 화면이 통째로 죽는다. 키 이름을 손으로 나열하지 않고
**두 응답을 비교**한다 — 나열하면 필드가 늘 때마다 이 검사가 낡는다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.routers import portfolio as prt
from backend.services import portfolio_calculator as pc

_IDX = pd.bdate_range("2026-01-05", periods=180)
_rng = np.random.default_rng(3)
_PX = 165.4 * np.cumprod(1 + _rng.normal(0.0003, 0.011, len(_IDX)))
_PRICES = pd.DataFrame({"AAPL": _PX, "^GSPC": np.linspace(4000.0, 4400.0, len(_IDX))},
                       index=_IDX)
_HOLD = {"AAPL": {"q": 10, "avg": 165.4, "sector": "Tech"}}
_LOG = [{"date": "2026-01-05", "ticker": "AAPL", "type": "ADD", "q": 10, "price": 165.4}]


@pytest.fixture
def empty_account(monkeypatch):
    """보유도 거래도 없는 사용자. 저장소만 비우고 라우터는 실물이 돈다."""
    monkeypatch.setattr(prt, "get_holdings", lambda uid, market="US": {})
    monkeypatch.setattr(prt, "get_trade_log", lambda uid, market="US": [])
    # 빈 계정도 calculate_metrics 를 거치고, 한국 계산기는 변동성 지수(VKOSPI)를
    # market_data.volatility_index 로 따로 읽는다 (ba60ceb) — 네트워크에 닿는다.
    # 그 함수가 못 읽었을 때 돌려주는 모양(value None)을 대신 준다.
    from backend.services import market_data
    monkeypatch.setattr(market_data, "volatility_index", lambda market: {
        "value": None, "prev_close": None, "change_pct": None, "as_of": None,
        "label": None, "source": None,
    })


def _metrics(market="US"):
    return prt.get_metrics(_auth={"uid": "empty-user"}, market=market)


def _a_real_response():
    """같은 계산기가 실제 보유로 내놓는 응답 — 모양을 비교할 상대."""
    curve = pc.build_equity_curve(_HOLD, _LOG, _PRICES)
    return pc.calculate_metrics(_HOLD, _PRICES, curve, trade_log=_LOG)


# ── 정상 상태를 오류라고 부르지 않는다 ─────────────────────────────────────────

@pytest.mark.parametrize("market", ["US", "KR"])
def test_an_empty_portfolio_is_not_an_error(empty_account, market):
    """빈 계정도 응답을 받는다 — 예외가 아니다.

    400 이면 서버가 **정상 상태를 클라이언트 오류라고 부르는** 것이고, 그
    사용자는 화면을 열 때마다 콘솔에 400 을 쌓는다. 같은 파일의
    `/holdings-detail`·`/sector-weights` 는 이미 200 + 빈 값을 준다.
    """
    out = _metrics(market)

    assert isinstance(out, dict) and out.get("is_empty") is True, (
        f"an empty portfolio did not come back as a normal empty result: {out}"
    )


def test_a_real_portfolio_is_not_marked_empty():
    """대조군 — 보유가 있으면 `is_empty` 가 붙지 않는다.

    없으면 위 검사는 "언제나 빈 응답" 이라는 구현으로도 통과하고, 그러면
    모든 사용자의 화면이 빈다.
    """
    assert _a_real_response().get("is_empty") is not True


# ── 0 과 None 을 가른다 (§1.3 a) ───────────────────────────────────────────────

@pytest.mark.parametrize("market", ["US", "KR"])
def test_amounts_are_zero_and_unknowables_are_null(empty_account, market):
    """금액은 0, 계산 불가는 null.

    아무것도 없는 사용자의 자산은 **실제로 0** 이다. 반대로 수익률·베타는
    분모가 없어 잴 수 없다 — 0 으로 채우면 '본전' 과 '시장과 같은 변동성'
    이라는 단정이 된다.
    """
    out = _metrics(market)

    for key in ("total_equity", "stock_value", "cash_value", "total_cost"):
        assert out[key] == 0.0, f"{key}={out[key]!r} — 금액은 0 이 참이다"

    for key in ("total_return_pct", "today_change_pct", "perf_1w", "perf_1m",
                "portfolio_beta", "alpha_vs_benchmark"):
        assert out[key] is None, (
            f"{key}={out[key]!r} -- there is nothing to measure against, and a "
            "number here reads as a measurement. (§1.3 a)"
        )


@pytest.mark.parametrize("market", ["US", "KR"])
def test_nothing_counted_is_reported_as_nothing_counted(empty_account, market):
    """센 것이 없다는 사실을 그대로 싣는다 — 0건·빈 목록.

    이건 `None` 이 아니라 0 이 맞다. "몇 종목을 셌는가" 의 답은 실제로 0 이고,
    `None` 으로 두면 "세지 못했다" 와 섞인다.
    """
    out = _metrics(market)

    assert out["change_counted"] == 0
    assert out["change_holdings"] == 0
    assert out["change_stale"] == []
    assert out["as_of"] is None, "기준일이 없는데 날짜를 지어냈다"


# ── 시장이 다르면 벤치마크도 다르다 (§1.1) ────────────────────────────────────

def test_the_benchmark_follows_the_market_even_when_empty(empty_account):
    """빈 화면에도 그 시장의 지수가 실린다.

    보유와 무관하게 시장이 정하는 값이다. 빈 응답이라고 라벨까지 지우면
    차트 범례가 '벤치마크' 로 떨어지고, 미국 것을 그대로 내보내면 한국
    사용자의 빈 화면에 S&P 500 이 뜬다.
    """
    us, kr = _metrics("US"), _metrics("KR")

    assert us["benchmark"] != kr["benchmark"], (
        f"both markets report the same benchmark ({us['benchmark']}) -- a "
        "won-denominated account compared against a dollar index. (§1.1)"
    )
    for out in (us, kr):
        assert out["benchmark"] and out["benchmark_label"], (
            f"benchmark label missing: {out['benchmark']!r} / "
            f"{out['benchmark_label']!r} — 차트 범례가 '벤치마크' 로 떨어진다"
        )


def test_the_empty_benchmark_matches_what_the_calculator_would_say(empty_account):
    """라벨을 **계산기와 같은 자리**에서 낸다.

    빈 분기가 이름을 따로 지어내면 두 경로가 서로 다른 이름을 말하고,
    사용자는 계정이 차는 순간 범례가 바뀌는 것을 본다.
    """
    real = _a_real_response()      # 계산기를 직접 부른다 — 라우터 패치와 무관
    empty = _metrics("US")

    assert (empty["benchmark"], empty["benchmark_label"]) == \
           (real["benchmark"], real["benchmark_label"]), (
        f"the empty branch names the benchmark differently: "
        f"{empty['benchmark_label']!r} vs {real['benchmark_label']!r}"
    )


# ── 모양 — 빈 응답에만 없는 필드 ───────────────────────────────────────────────
#
# 빈 응답과 실제 응답이 다른 키 집합을 내보내면 프론트는 **빈 계정에서만**
# `undefined` 를 만난다. "값이 없다" 가 아니라 "필드가 없다" 라서, 옵셔널
# 체이닝 하나가 빠지면 그 화면이 통째로 죽는다 — 그리고 개발 중에는 계정이
# 비어 있지 않아서 안 보인다.
#
# 지금 빠진 것을 적어 둔다. 채워지면 이 목록을 지우라고 요구한다.
# 비어 있는 것이 정상이다. 네 항목(market_open · beta_counted ·
# beta_holdings · beta_value_share)이 여기 있었고 routers/portfolio.py 가
# 채웠다 — 아래 test_the_missing_list_does_not_outlive_the_gap 이 지우라고
# 요구해서 지웠다. 원장이 제 역할을 한 자리다.
_KNOWN_MISSING: dict[str, str] = {}


def test_no_new_field_goes_missing_from_the_empty_response(empty_account):
    """실제 응답에 있는 키는 빈 응답에도 있어야 한다.

    키 이름을 손으로 나열하지 않고 **두 응답을 비교**한다 — 나열하면 필드가
    늘 때마다 이 검사가 낡고, 낡은 목록은 아무것도 안 지킨다.
    """
    missing = set(_a_real_response()) - set(_metrics("US"))
    new = sorted(missing - set(_KNOWN_MISSING))

    assert not new, (
        f"these fields exist for a real portfolio but not for an empty one: "
        f"{new} -- the front end meets `undefined` only on empty accounts, "
        "which is exactly where nobody develops. (빈 계정에서만 필드가 없다.)"
    )


def test_the_missing_list_does_not_outlive_the_gap(empty_account):
    """채워진 필드가 목록에 남아 있으면 실패한다.

    한 방향만 검사하면 목록이 조용히 낡는다. 채운 사람은 초록을 보고
    지나가고, 다음 사람은 이 줄을 읽고 "아직 없구나" 로 믿는다.
    """
    missing = set(_a_real_response()) - set(_metrics("US"))
    filled = sorted(k for k in _KNOWN_MISSING if k not in missing)

    assert not filled, (
        "these are present now but still listed as missing -- delete the "
        "lines:\n" + "\n".join(f"  {k}  ({_KNOWN_MISSING[k]})" for k in filled)
    )


def test_the_comparison_has_something_to_compare():
    """전제 — 실제 응답이 실제로 필드를 여럿 낸다.

    비어 있으면 위 두 검사가 공집합끼리 비교하며 통과한다.
    """
    real = _a_real_response()
    assert len(real) > 15, f"실제 응답 키가 {len(real)}개뿐이다 — 비교 상대가 없다"

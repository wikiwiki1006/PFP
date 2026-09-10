"""
B4·C2 — 거시지표를 못 받았으면 **못 받았다고 프롬프트에 적는다.**

블록을 조용히 빼면 모델에게는 "없음" 이 아니라 **아무 정보도 아니다.** 그러면
모델은 빈 자리를 자기 사전지식으로 채운다 — "최근 보도에 따르면" 으로 시작하는
문장이 그렇게 나온다. **생략이 오히려 환각을 유발한다.** 반대로 "수집 실패,
인용하지 마세요" 라고 적으면 모델은 그 항목을 근거로 쓰지 않는다.

그리고 폴백 상수를 실측값처럼 적어서도 안 된다(B4). FRED 를 못 읽었을 때 쓰는
하드코딩 값이 프롬프트에 숫자로 들어가면, 모델은 그걸 오늘의 관측치로 인용한다.

## 지금 상태 — 같은 함수가 경우에 따라 다르게 군다

`build_macro_block` 은 **FRED 폴백**은 명시한다:

    [US macro indicators]
      (unavailable — FRED lookup failed)

그런데 **수집이 예외로 죽으면 빈 문자열**을 돌려준다. 그리고 호출자 둘이
그걸 다르게 다룬다:

    generate_daily_brief      `or "(거시지표 수집 실패 — 인용하지 마세요)"`  → 명시
    gather_yfinance_market_data  `if macro_block:` 로 그냥 건너뜀           → 침묵

한 호출자는 알고 다른 호출자는 모른다. 호출자마다 보완하게 두는 구조 자체가
문제라, **함수가 빈 문자열을 돌려주지 않는 것**이 해법이다. 그러면 `if
macro_block:` 은 무해해지고 새 호출자도 자동으로 안전하다.
"""
from __future__ import annotations

import re
from unittest import mock

import pytest

from backend.services import ai_analysis

# 실패를 알리는 말. 어떤 문구를 쓰든 이 중 하나는 있어야 한다.
_SAYS_FAILED = ("unavailable", "실패", "없음", "수집 못", "not available")


def _mentions_failure(text: str) -> bool:
    return any(tok in text for tok in _SAYS_FAILED)


def _has_numbers(text: str) -> bool:
    """지표 수치처럼 보이는 것이 있는가. 대괄호 제목 안의 글자는 세지 않는다."""
    return bool(re.search(r"\d+\.\d+|\d+\s*%", text))


# ── B4. 폴백 상수를 실측값처럼 적지 않는다 (지금 지키고 있다) ────────────────────

def test_fred_fallback_is_not_presented_as_data():
    """FRED 폴백이면 숫자를 적지 않고 못 읽었다고 쓴다.

    폴백은 하드코딩 상수다. 프롬프트에 숫자로 들어가면 모델은 오늘의 관측치로
    인용한다 — 그리고 그 인용은 리포트에서 사실처럼 보인다.
    """
    with mock.patch("backend.services.market_data.get_fred_macro", return_value={
        "source": "fallback", "fed_rate": 4.25, "unemployment": 4.1, "cpi": 2.9,
    }):
        block = ai_analysis.build_macro_block("US")

    assert _mentions_failure(block), f"폴백인데 실패를 알리지 않는다: {block!r}"
    assert not _has_numbers(block), (
        f"fallback constants reached the prompt as figures: {block!r} -- the "
        "model quotes them as today's readings. "
        "(폴백 상수가 실측값처럼 실렸다.)"
    )


def test_real_fred_data_does_reach_the_prompt():
    """대조군 — 진짜 값은 숫자로 들어간다.

    없으면 위 검사는 "어떤 경우에도 숫자를 안 넣는" 구현으로 통과한다. 그건
    폴백을 막는 게 아니라 거시지표 기능이 죽은 것이다.
    """
    with mock.patch("backend.services.market_data.get_fred_macro", return_value={
        "source": "fred", "fed_rate": 4.25, "unemployment": 4.1, "cpi": 2.9,
        "gdp": 2.1, "t10y2y": 0.35, "bamlh0a0hym2": 310,
    }):
        block = ai_analysis.build_macro_block("US")

    assert "4.25" in block, f"실측값이 프롬프트에 없다: {block!r}"
    assert not _mentions_failure(block), f"정상인데 실패로 적었다: {block!r}"


# ── C2. 수집 실패도 프롬프트에 남는다 (아직 안 지킨다) ──────────────────────────

_C2_XFAIL = (
    "build_macro_block returns an empty string when collection raises, so the "
    "macro section disappears from the prompt with nothing in its place. An "
    "omission is not 'no data' to the model — it is no instruction at all, so "
    "it fills the gap from prior knowledge. Say the collection failed instead, "
    "the way the FRED-fallback branch already does. Fixing it in the function "
    "also makes both call sites safe: gather_yfinance_market_data skips a "
    "falsy block silently while generate_daily_brief substitutes a failure "
    "line, and only one of them is right. (owner: reportmanage)"
)


@pytest.mark.xfail(strict=True, reason=_C2_XFAIL)
@pytest.mark.parametrize("market, target, err", [
    ("US", "backend.services.market_data.get_fred_macro", "FRED down"),
    ("KR", "backend.services.korea_macro.get_korea_macro", "ECOS down"),
])
def test_collection_failure_is_stated_not_omitted(market, target, err):
    """수집이 예외로 죽어도 블록이 사라지면 안 된다."""
    with mock.patch(target, side_effect=RuntimeError(err)):
        block = ai_analysis.build_macro_block(market)

    assert block.strip(), (
        f"build_macro_block({market}) returned an empty string on failure -- "
        "the macro section vanishes and the model fills the silence from "
        "memory. (생략이 환각을 부른다.)"
    )
    assert _mentions_failure(block), f"실패를 알리지 않는다: {block!r}"


# ── 호출자 쪽 보완 — 지금 동작하는 것을 고정한다 ────────────────────────────────

def test_daily_brief_fills_in_when_the_block_is_empty():
    """`generate_daily_brief` 는 빈 블록을 그냥 두지 않는다.

    함수가 고쳐지면 이 보완은 필요 없어지지만, 그때까지는 이것이 유일한
    방어선이다. 없애기 전에 위 xfail 이 먼저 사라져야 한다.
    """
    with mock.patch.object(ai_analysis, "build_macro_block", return_value=""), \
         mock.patch.object(ai_analysis, "call_claude", lambda prompt, *a, **kw: prompt):
        prompt = ai_analysis.generate_daily_brief(
            {"AAPL": {"q": 1, "avg": 1.0, "sector": "Tech"}},
            {"AAPL": {"price": 2.0, "chg_pct": 1.0, "pnl_pct": 1.0,
                      "pos_val": 2.0, "day_pnl": 0.5}},
            [], "US")

    assert _mentions_failure(prompt), (
        "an empty macro block left no trace in the daily brief -- the section "
        "is simply gone and the model has no reason to avoid the topic. "
        "(빈 블록이 흔적 없이 사라졌다.)"
    )

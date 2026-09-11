"""
**"정확히 0" 과 "거의 0" 은 다르게 새어 나간다.**

샤프 비율의 분모(변동성)를 `vol > 0` 으로만 걸렀을 때 무엇이 나갔는지 실측이
남아 있다 — 매일 정확히 +0.05% 오르는 결정론적 입력의 동일비중 변동성은
0 이 아니라 **1.803e-15** 였다. 부동소수 잡음이다. `> 0` 을 통과해서
`equal_weight_sharpe = 47,693,150,800,467.95` 가 응답에 실렸다.

그래서 이 자리의 위장은 **두 방향**이다:

    분모가 정확히 0      → `0.0`  화면에서 '위험조정수익 없음' = 최악으로 읽힌다
    분모가 잡음만큼 양수  → 천문학적 숫자가 **측정값인 얼굴로** 나간다

`0.0` 만 찾는 검사는 두 번째를 못 잡는다. 오히려 두 번째가 더 위험하다 —
0 은 눈에 띄지만 4.8e13 은 "뭔가 계산됐다" 로 읽힌다.

## 하한이 한 곳에 있는지도 잰다

같은 식이 네 자리에 있었고 하한이 셋으로 갈려 있었다(`> 0` · `> 1e-12` ·
프론트에도 `> 0`). 지금은 `portfolio_calculator.MIN_VOL_FOR_RATIO` 한 곳에서
읽는다. **갈리면 한 화면의 두 숫자가 서로 다른 기준으로 계산된다.**
"""
from __future__ import annotations

import pathlib

import pytest

from backend.services.optimizer import _ratio_or_none
from backend.services.portfolio_calculator import MIN_VOL_FOR_RATIO

# 실제로 나왔던 값. 상수로 적는 것이 맞다 — "그때 무엇이 통과했는가" 의
# 기록이지 오늘 계산해야 할 값이 아니다.
_OBSERVED_NOISE_VOL = 1.803e-15
_OBSERVED_BAD_SHARPE = 47_693_150_800_467.95

# 300일 중 한 번 1틱만 움직인 계열의 변동성 — 작지만 **진짜** 변동성이다.
_TINY_BUT_REAL_VOL = 4.583e-05


# ── 두 방향 다 막는다 ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("vol, why", [
    (0.0, "정확히 0"),
    (_OBSERVED_NOISE_VOL, "부동소수 잡음 — 실제로 나왔던 값"),
    (-1e-9, "음수 (분산 계산이 어긋난 경우)"),
    (1e-300, "0 은 아니지만 비율이 무의미한 크기"),
])
def test_a_denominator_that_is_not_really_volatility_yields_nothing(vol, why):
    """분모가 진짜 변동성이 아니면 비율을 안 낸다.

    `0.0` 만 찾으면 두 번째 경우를 놓친다. 그게 실제로 새어 나간 쪽이다 —
    0 은 눈에 띄지만 4.8e13 은 "뭔가 계산됐다" 로 읽힌다.
    """
    assert _ratio_or_none(_OBSERVED_BAD_SHARPE, vol) is None, (
        f"{why}: a ratio was published over a denominator of {vol!r} -- "
        "the number reads as a measurement and it is not one. (§1.3 a)"
    )


@pytest.mark.parametrize("ratio", [float("inf"), float("-inf"), float("nan")])
def test_a_non_finite_ratio_yields_nothing(ratio):
    """분모가 멀쩡해도 비율 자체가 유한하지 않으면 안 낸다.

    분모만 보면 `inf` 가 그대로 나간다. JSON 으로는 `Infinity` 가 되고
    파서에 따라 통째로 깨진다.
    """
    assert _ratio_or_none(ratio, 0.15) is None


def test_a_small_but_real_volatility_still_gets_a_ratio():
    """대조군 — 작아도 **진짜** 변동성이면 비율이 나온다.

    없으면 위 검사들은 "언제나 None" 이라는 구현으로도 통과한다. 그러면
    저변동성 포트폴리오의 샤프가 통째로 사라지고, 화면은 그걸 "계산 불가"
    로 그린다 — 반대 방향의 §1.3 위반이다.

    이 값은 300일 중 한 번 1틱만 움직인 계열에서 실측된 것이다. 하한을
    올리다 보면 제일 먼저 걸리는 쪽이라 경계로 쓴다.
    """
    got = _ratio_or_none(1.42, _TINY_BUT_REAL_VOL)

    assert got == pytest.approx(1.42), (
        f"a genuinely low-volatility portfolio lost its Sharpe ratio ({got}) -- "
        f"the floor {MIN_VOL_FOR_RATIO} is now above real market data."
    )


def test_the_floor_sits_between_the_two():
    """하한이 잡음과 실제 변동성 **사이**에 있다.

    위 두 검사가 같이 성립하려면 그래야 한다. 하한을 옮기는 사람이
    한쪽만 보고 옮기면 여기가 먼저 빨개진다.
    """
    assert _OBSERVED_NOISE_VOL < MIN_VOL_FOR_RATIO < _TINY_BUT_REAL_VOL, (
        f"the floor {MIN_VOL_FOR_RATIO} no longer separates float noise "
        f"({_OBSERVED_NOISE_VOL}) from real low volatility "
        f"({_TINY_BUT_REAL_VOL}) -- one of the two checks above is now vacuous."
    )


# ── 하한이 한 곳에서만 온다 ───────────────────────────────────────────────────
#
# 같은 식이 네 자리에 있었고 하한이 셋으로 갈려 있었다(`> 0` · `> 1e-12` ·
# 프론트에도 `> 0`). 갈리면 **한 화면의 두 숫자가 서로 다른 기준으로**
# 계산되고, 어느 쪽이 맞는지 화면에서는 구별되지 않는다.
#
# 처음엔 줄 단위 정규식으로 찾았는데 2건이 나왔고 **둘 다 위반이 아니었다** —
# 하나는 그 사고를 설명하는 docstring 안의 인용, 하나는 아래 예외다. AST 로
# 바꾸니 docstring 은 비교식이 아니라서 아예 안 걸린다. 남는 건 진짜 하나다.

_SRC = pathlib.Path(__file__).resolve().parents[1]
_RATIO_FILES = [
    "services/optimizer.py",
    "services/portfolio_optimizer.py",
    "services/portfolio_calculator.py",
]

# 발행되지 않는 자리. **이 하나는 예외가 맞다** — 옵티마이저 목적함수가
# 탐색을 그쪽에서 밀어내려고 쓰는 페널티(`1e6`)이고, 사용자에게 나가는
# 숫자가 아니다. 하한을 공유할 이유도 없다: 여기서 `None` 을 돌려주면
# `minimize` 가 죽는다.
_ALLOWED = {
    ("services/optimizer.py", "neg_sharpe"):
        "옵티마이저 목적함수의 페널티 — 발행되지 않는다",
}


def _floor_comparisons():
    """변동성을 숫자 리터럴과 비교하는 자리 — (파일, 함수, 줄, 소스)."""
    import ast

    out = []
    for rel in _RATIO_FILES:
        path = _SRC / rel
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        # 각 비교식이 어느 함수 안인지 — 가장 안쪽 함수 이름을 쓴다.
        scopes = [(n.name, n.lineno, getattr(n, "end_lineno", n.lineno))
                  for n in ast.walk(tree)
                  if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]

        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            left = ast.unparse(node.left)
            if "vol" not in left.lower():
                continue
            if not any(isinstance(c, ast.Constant) and isinstance(c.value, (int, float))
                       for c in node.comparators):
                continue
            # 공유 상수와 비교하는 것은 리터럴이 아니다 — 위 조건에서 이미
            # 빠지지만, 이름으로 비교하는 형태가 늘어도 안전하게 둔다.
            if "MIN_VOL_FOR_RATIO" in ast.unparse(node):
                continue
            inner = [name for name, lo, hi in scopes if lo <= node.lineno <= hi]
            out.append((rel, inner[-1] if inner else "<module>",
                        node.lineno, ast.unparse(node)))
    return out


def test_no_new_place_writes_its_own_volatility_floor():
    """변동성 하한을 리터럴로 다시 적는 자리가 늘지 않는다."""
    found = _floor_comparisons()
    new = [f for f in found if (f[0], f[1]) not in _ALLOWED]

    assert not new, (
        "a volatility floor is written out again instead of reading "
        "MIN_VOL_FOR_RATIO -- two numbers on one screen then use different "
        "rules, and the screen cannot say which:\n"
        + "\n".join(f"  {rel}:{line} in {fn}()  {src}" for rel, fn, line, src in new)
    )


def test_the_allowed_exception_still_exists():
    """예외로 적어 둔 자리가 사라졌으면 그 줄을 지워야 한다.

    한 방향만 검사하면 목록이 조용히 낡는다. 지운 사람은 초록을 보고
    지나가고, 다음 사람은 이 줄을 읽고 "거기 있구나" 로 믿는다.
    """
    present = {(rel, fn) for rel, fn, _, _ in _floor_comparisons()}
    gone = sorted(k for k in _ALLOWED if k not in present)

    assert not gone, (
        "these are listed as allowed exceptions but no longer exist -- delete "
        "the lines:\n" + "\n".join(f"  {k}  ({_ALLOWED[k]})" for k in gone)
    )


def test_the_scan_sees_both_answers():
    """판정기 자체를 잰다 — 정답을 아는 합성 입력으로 양방향.

    "아무것도 못 찾음" 과 "아무 문제 없음" 은 결과가 똑같이 생긴다.
    """
    import ast
    import textwrap

    def scan(src):
        tree = ast.parse(textwrap.dedent(src))
        hits = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            if "vol" not in ast.unparse(node.left).lower():
                continue
            if not any(isinstance(c, ast.Constant) and isinstance(c.value, (int, float))
                       for c in node.comparators):
                continue
            if "MIN_VOL_FOR_RATIO" in ast.unparse(node):
                continue
            hits.append(ast.unparse(node))
        return hits

    assert scan("sharpe = ret / vol if vol > 0 else None") == ["vol > 0"]
    assert scan("x = eq_vol >= 1e-12") == ["eq_vol >= 1e-12"]
    # 공유 상수를 쓰면 안 걸린다.
    assert scan("s = r / vol if vol > MIN_VOL_FOR_RATIO else None") == []
    # 그 사고를 설명하는 docstring 은 비교식이 아니다 — 줄 단위 스캔이
    # 여기서 오탐을 냈다.
    assert scan('"""`vol > 0` 으로만 걸렀을 때 무엇이 나갔는지 실측했다."""') == []
    # 변동성과 무관한 비교는 안 걸린다.
    assert scan("if len(weights) > 0: pass") == []

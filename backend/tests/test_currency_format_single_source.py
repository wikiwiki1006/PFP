"""
A6 — 통화 포맷은 리포 전체에 한 곳이다.

`report_writer._fmt_amount` / `_fmt_price` 가 그 한 곳이다. 통화 기호를 값 옆에
직접 붙여 쓰는 코드는 **그 한 곳의 사본**이고, 사본은 원본이 고쳐질 때 같이
고쳐지지 않는다. `$333605.94B` 사고가 살아남은 방식이 정확히 그것이다 —
`report_writer` 는 고쳐졌고 사본은 남았다.

## 다른 규칙과 형태가 다르다

`test_prompt_rules.py` 는 **프롬프트 문자열**을 본다. 그런데 사본 문제는 문자열에
드러나지 않는 자리에도 있다 — 사용자에게 그대로 보이는 에러 메시지나 매매 신호
사유 같은 곳이다. 그래서 이것만 **소스 구조**를 본다.

## 무엇을 위반으로 보는가

f-string 안에서 **통화 기호 바로 뒤에 값이 보간되는 것**. 기호는 리터럴(`"$"` 로
끝나는 문자열)이거나 통화 기호를 담은 변수(`cur`·`symbol`·`sym`)다.

포맷 지정자를 요구하지 않는다. `f"${x}B"` 처럼 지정자 없이 붙이는 형태가
`portfolio_optimizer` 의 실제 결함이고, 지정자를 요구하면 그걸 놓친다.

## 알려진 위반은 목록으로 관리한다 (허용 목록이 아니다)

지금 14곳이 걸린다. 전부 남의 소유 파일이라 여기서 고칠 수 없다. 그렇다고
허용 목록에 넣으면 **의도된 예외처럼 보인다** — 이건 예외가 아니라 아직 안 고친
결함이다.

그래서 목록이 **정확히 일치**해야 통과한다. 새로 생기면 실패하고, 고쳐도
실패한다("목록에서 지워라"). 목록이 조용히 낡지 않는 유일한 형태다.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
ROOT = BACKEND.parent

_CURRENCY_SYMBOLS = ("₩", "$", "€", "¥")
# 통화 기호를 담고 있을 법한 변수 이름 조각. 좁게 둔다 — 넓히면 오탐이 난다.
_CURRENCY_VARS = ("cur", "symbol", "sym")

# 통화 포맷의 단일 출처. 여기 안에서 기호를 값에 붙이는 것이 이 함수들의 일이다.
_CANONICAL = {
    ("backend/services/report_writer.py", "_fmt_amount"),
    ("backend/services/report_writer.py", "_fmt_price"),
}

# 아직 안 고친 위반. (파일, 감싸는 함수) -> 무엇이 문제이고 누가 소유하는가.
# 줄 번호로 키를 잡지 않는다 — 위아래 편집만으로 목록이 흔들린다.
_KNOWN_VIOLATIONS = {
    ("backend/services/daily_report.py", "_build_prompt"):
        "종목 줄·합계 줄이 통화 포맷을 인라인으로 다시 구현한다 "
        "(cur + 포맷 지정자). (소유: reportmanage)",
    ("backend/services/trading_signals.py", "scan_universe_with_targets"):
        "매매 신호의 reason 문자열 3곳이 '$' 와 소수점 2자리를 고정한다 "
        "(중앙선 회귀·돌파 기대). 프롬프트가 아니라 **화면에 그대로 보이는** "
        "문자열이라, 한국 종목이면 '중앙선 $71900.00' 이 사용자에게 뜬다. "
        "(소유: programoptimize)",
}


# ── 검출 ──────────────────────────────────────────────────────────────────────

def _enclosing_function(tree: ast.AST, node: ast.AST) -> str:
    best = None
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if n.lineno <= node.lineno <= (n.end_lineno or n.lineno):
                if best is None or n.lineno > best.lineno:
                    best = n
    return best.name if best else "<module>"


def _scan_source(rel: str, src: str) -> list[tuple[str, int, str, str]]:
    """(파일, 줄, 감싸는 함수, 보간된 식) 목록."""
    tree = ast.parse(src)
    hits = []
    for joined in ast.walk(tree):
        if not isinstance(joined, ast.JoinedStr):
            continue
        for i, part in enumerate(joined.values):
            if i == 0 or not isinstance(part, ast.FormattedValue):
                continue
            prev = joined.values[i - 1]

            if isinstance(prev, ast.Constant) and isinstance(prev.value, str):
                marked = prev.value.endswith(_CURRENCY_SYMBOLS)
            elif isinstance(prev, ast.FormattedValue):
                name = (ast.get_source_segment(src, prev.value) or "").lower()
                marked = any(k in name for k in _CURRENCY_VARS)
            else:
                marked = False

            if marked:
                expr = ast.get_source_segment(src, part.value) or "?"
                hits.append((rel, part.lineno, _enclosing_function(tree, part), expr))
    return hits


def _inline_currency_sites() -> list[tuple[str, int, str, str]]:
    hits = []
    for path in sorted(BACKEND.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if "/tests/" in rel:
            continue
        hits += _scan_source(rel, path.read_text(encoding="utf-8"))
    return [h for h in hits if (h[0], h[2]) not in _CANONICAL]


# ── 검출기 자기검사 ────────────────────────────────────────────────────────────
#
# 검출기가 망가지면 "위반 0건" 이 되고, 그건 아래 목록 검사도 통째로 무력화한다
# (빈 집합끼리 비교하면 통과한다). 답을 아는 소스로 양방향을 잰다.

_SRC_LITERAL = 'def f(x):\n    return f"가격 ${x:,.2f}"\n'
_SRC_NO_SPEC = 'def f(x):\n    return f"시가총액 ${x}B"\n'
_SRC_CUR_VAR = 'def f(cur, x):\n    return f"종가 {cur}{x:,.0f}"\n'
_SRC_WON = 'def f(v):\n    return f"현재가 ₩{v:,.0f}"\n'
_SRC_DELEGATES = 'def f(v, c):\n    return f"현재가 {_fmt_price(v, c)}"\n'
_SRC_NO_CURRENCY = 'def f(p):\n    return f"수익률 {p:+.2f}%"\n'
_SRC_PERCENT_VAR = 'def f(pct, x):\n    return f"{pct}{x:,.0f}"\n'


@pytest.mark.parametrize("src, expected, why", [
    (_SRC_LITERAL, True, "리터럴 기호 + 보간"),
    (_SRC_NO_SPEC, True, "포맷 지정자가 없어도 잡는다 — optimizer 의 실제 형태"),
    (_SRC_CUR_VAR, True, "통화 변수 + 보간"),
    (_SRC_WON, True, "원화도 같다"),
    (_SRC_DELEGATES, False, "단일 출처에 위임하면 위반이 아니다"),
    (_SRC_NO_CURRENCY, False, "통화가 아니면 잡지 않는다"),
    (_SRC_PERCENT_VAR, False, "통화 이름이 아닌 변수는 기호로 보지 않는다"),
])
def test_detector_catches_what_it_should(src, expected, why):
    assert bool(_scan_source("synthetic.py", src)) is expected, why


# ── 본 검사 ───────────────────────────────────────────────────────────────────

def test_currency_formatting_has_a_single_source():
    """단일 출처 밖에서 통화 기호를 값에 붙이는 곳이, 알려진 목록과 정확히 같아야 한다.

    새 위반이 생기면 실패하고, 고쳐도 실패한다. 후자가 성가셔 보이지만 의도한
    것이다 — 목록에 남은 항목은 다음 사람에게 "아직 안 고쳤다" 는 뜻이라,
    고쳐진 뒤에도 남아 있으면 거짓말이 된다.
    """
    live = {(rel, func) for rel, _, func, _ in _inline_currency_sites()}
    known = set(_KNOWN_VIOLATIONS)

    new = sorted(live - known)
    fixed = sorted(known - live)

    assert not new, (
        f"{len(new)} new inline currency format site(s): {new} -- these are "
        "copies of report_writer._fmt_amount/_fmt_price and will not be "
        "updated when it changes. Call the canonical formatter, or add the "
        "entry to _KNOWN_VIOLATIONS with what is wrong and who owns it. "
        "(통화 포맷 사본이 늘었다.)"
    )
    assert not fixed, (
        f"{len(fixed)} entry/entries in _KNOWN_VIOLATIONS no longer match any "
        f"code: {fixed} -- they were fixed. Delete them from the ledger so it "
        "keeps telling the truth about what is still broken. "
        "(고쳐진 항목이 목록에 남아 있다.)"
    )


def test_canonical_formatters_still_exist():
    """단일 출처가 사라지면 위 검사의 기준점이 없어진다.

    `_CANONICAL` 은 파일·함수 이름으로 적혀 있어, 이름이 바뀌면 조용히 아무것도
    가리키지 않게 된다. 그러면 그 두 함수 자체가 위반으로 잡혀 소음이 되거나,
    반대로 기준이 사라진 줄 모른 채 지나간다.
    """
    from backend.services import report_writer

    for _, name in _CANONICAL:
        assert hasattr(report_writer, name), (
            f"report_writer.{name} is gone -- _CANONICAL no longer points at "
            "the single source of currency formatting"
        )

    assert report_writer._fmt_price(71900, "KRW") == "₩71,900", "원화는 소수점이 없다"
    assert report_writer._fmt_price(231.45, "USD") == "$231.45", "달러는 소수점 2자리"


def test_ledger_entries_say_why():
    """목록의 모든 항목에 이유가 있어야 한다.

    이유 없는 항목은 다음 사람이 화석인지 결정인지 구별하지 못한다.
    """
    thin = [k for k, v in _KNOWN_VIOLATIONS.items() if len(v.strip()) < 20]
    assert not thin, f"_KNOWN_VIOLATIONS entries without a real reason: {thin}"

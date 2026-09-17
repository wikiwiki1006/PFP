"""
프론트 단위 스위트(`frontend/e2e/unit/*.test.mjs`)를 pytest 게이트에 태운다.

그 파일들은 진짜 `.ts` 를 node 로 불러 재는 검사인데, **어떤 게이트에서도 돌지
않았다.** 게이트는 `pytest backend/tests` 와 `npm run build` 뿐이고, `tsc` 는
타입만 본다. 그래서 거기 적힌 단언이 깨져도 아무도 모른다 — 검사가 있다는
사실이 "덮였다" 로 읽히는 형태다.

`node --test` 를 파일마다 서브프로세스로 돌리고 그 결과를 pytest 결과로 옮긴다.

## 종료코드만 보면 안 된다 — node 가 초록으로 답하는 빈 경우가 둘 있다

실제 node 24 에 물어 확인했다:

    글로브가 아무것도 못 찾음      exit 0 · tests 0
    파일에 테스트가 하나도 없음    exit 0 · tests 1 · pass 1   (파일 자체를 한 건으로 센다)

그래서 파일 목록은 파이썬이 직접 찾고(비면 실패), 파일마다 **파일 이름이 아닌
테스트가 하나 이상 보고됐는지**를 본다. 불러오다 죽은 파일(문법 오류)도 파일
이름으로 된 `not ok` 한 건이라 같은 기준에 걸린다.

skip · todo 도 실패로 올린다. 게이트에서 skip 은 통과와 구별되지 않는다 —
빠진 검사가 초록으로 남는다. 일부러 미루는 것이면 이 파일이 아니라 그 테스트를
지우거나 고친다.

## node 가 없으면 실패다

skip 이 아니다. node 는 `npm run build` 에 이미 필요해서 게이트를 돌릴 수 있는
곳에는 있다. skip 으로 두면 node 없는 환경에서 이 스위트가 조용히 사라진다.

`e2e/browser` 는 넣지 않는다 — 서버가 떠 있어야만 뜻이 있다 (e2e/README.md).
"""
from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
from dataclasses import dataclass, field

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
FRONTEND = ROOT / "frontend"
UNIT_DIR = FRONTEND / "e2e" / "unit"
_TIMEOUT_S = 120

_POINT = re.compile(r"^\s*(ok|not ok) \d+ - (.*?)(?: # (SKIP|TODO)\b.*)?$")
_COUNT = re.compile(r"^# (tests|pass|fail|cancelled|skipped|todo) (\d+)$")


@dataclass
class Verdict:
    returncode: int | None
    counts: dict[str, int] = field(default_factory=dict)
    names: list[str] = field(default_factory=list)
    output: str = ""
    problems: list[str] = field(default_factory=list)


def _unescape_tap(text: str) -> str:
    """node 의 TAP 리포터는 이름의 `\\` 와 `#` 앞에 역슬래시를 붙인다."""
    out, i = [], 0
    while i < len(text):
        if text[i] == "\\" and i + 1 < len(text):
            out.append(text[i + 1])
            i += 2
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def _names_the_file(name: str, path: pathlib.Path, cwd: pathlib.Path) -> bool:
    """이 테스트 이름이 **파일 자체**를 가리키는가.

    node 는 테스트가 없거나 불러오다 죽은 파일을, 명령줄에 넘긴 **그 경로 그대로**
    이름 붙인 한 건으로 보고한다. 절대경로를 넘기면 절대경로다 — 처음에 파일
    이름만 비교했다가 자기검사가 잡았다.
    """
    try:
        p = pathlib.Path(_unescape_tap(name))
        if not p.is_absolute():
            p = cwd / p
        return p.resolve() == path.resolve()
    except (OSError, ValueError):
        return False


def _node() -> str:
    node = shutil.which("node")
    if not node:
        pytest.fail(
            "node is not on PATH, so the frontend unit suite cannot run. It is already "
            "required for `npm run build`; a skip here would silently drop these checks "
            "from the gate. (node 가 없다 — skip 하지 않는다)"
        )
    return node


def run_node_test_file(path: pathlib.Path, cwd: pathlib.Path = FRONTEND) -> Verdict:
    """한 파일을 `node --test` 로 돌리고 게이트 기준으로 판정한다."""
    try:
        proc = subprocess.run(
            [_node(), "--test", "--test-reporter=tap", str(path)],
            cwd=cwd, capture_output=True, timeout=_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode("utf-8", "replace")
        return Verdict(None, output=out, problems=[f"did not finish within {_TIMEOUT_S}s"])

    out = proc.stdout.decode("utf-8", "replace") + proc.stderr.decode("utf-8", "replace")
    v = Verdict(proc.returncode, output=out)
    for line in out.splitlines():
        if m := _COUNT.match(line.strip()):
            v.counts[m.group(1)] = int(m.group(2))
        elif m := _POINT.match(line):
            v.names.append(m.group(2))

    if proc.returncode != 0:
        v.problems.append(f"node exited {proc.returncode}")
    if "tests" not in v.counts:
        v.problems.append("no TAP summary -- the runner did not report")
    for key in ("fail", "cancelled"):
        if v.counts.get(key):
            v.problems.append(f"{key} {v.counts[key]}")
    for key in ("skipped", "todo"):
        if v.counts.get(key):
            v.problems.append(f"{key} {v.counts[key]} -- a skipped check reads as passed in the gate")
    if not [n for n in v.names if not _names_the_file(n, path, cwd)]:
        v.problems.append(
            "no test ran -- node reports a file without tests (or one that failed to "
            "load) as a single test named after the file"
        )
    return v


def _unit_files() -> list[pathlib.Path]:
    return sorted(UNIT_DIR.rglob("*.test.mjs"))


# ── 판정기 자기검사 — 실제 node 에 답을 아는 파일을 준다 ──────────────────────

_KNOWN = {
    "pass": ("test('ok', () => assert.equal(1, 1))", True),
    "fail": ("test('bad', () => assert.equal(1, 2))", False),
    "async-fail": ("test('bad', async () => { await null; throw new Error('boom') })", False),
    "no-tests": ("export const nothing = 1", False),
    "syntax-error": ("test('broken', () => {", False),
    "skip": ("test.skip('later', () => {}); test('ok', () => {})", False),
    "todo": ("test.todo('later'); test('ok', () => {})", False),
    "nested-suite": ("describe('s', () => { test('inner', () => {}) })", True),
}


@pytest.mark.parametrize("case", sorted(_KNOWN))
def test_the_verdict_matches_known_answers(case, tmp_path):
    body, should_pass = _KNOWN[case]
    f = tmp_path / f"{case}.test.mjs"
    f.write_text(
        "import test, { describe } from 'node:test'\n"
        "import assert from 'node:assert/strict'\n" + body + "\n",
        encoding="utf-8",
    )

    v = run_node_test_file(f, cwd=tmp_path)

    assert (not v.problems) is should_pass, (
        f"{case}: expected {'pass' if should_pass else 'fail'}, got problems={v.problems} "
        f"counts={v.counts} names={v.names}\n{v.output[-800:]}"
    )


# ── 게이트 ────────────────────────────────────────────────────────────────────

def test_the_unit_suite_is_not_empty():
    """파일이 하나도 없으면 아래 파라미터화 검사는 **0건으로 통과**한다."""
    files = _unit_files()
    assert files, (
        f"no *.test.mjs under {UNIT_DIR.relative_to(ROOT)} -- the unit suite moved or "
        "was emptied, and the bridge below would pass with zero checks. "
        "(단위 스위트가 비었다)"
    )


@pytest.mark.parametrize(
    "path", _unit_files(), ids=lambda p: p.relative_to(UNIT_DIR).as_posix(),
)
def test_frontend_unit_file(path):
    v = run_node_test_file(path)
    assert not v.problems, (
        f"{path.relative_to(ROOT).as_posix()}: {'; '.join(v.problems)}\n"
        f"counts={v.counts}\n--- node output (tail) ---\n{v.output[-3000:]}"
    )

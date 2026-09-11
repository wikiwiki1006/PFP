"""
어디에도 정의되지 않은 이름이 없어야 한다 — `logger` 형태.

두 번 났다. 둘 다 **실패했을 때만 도는 줄**이었다:

    market_data.py       `logger.warning(...)` — 이 모듈의 로거는 `_logger` 다
    market_calendar.py   `logger.warning(...)` — 이 모듈에는 로거가 없었다

두 번 다 전체 게이트가 초록이었다. 성공 경로는 요청마다 돌아서 오타가 즉시
드러나는데, `except` 안이나 폴백 안의 줄은 그 상황을 만들어 본 테스트가
없으면 **영원히 실행되지 않는다.** `market_calendar` 쪽 폴백은 작성된 이후
한 번도 돈 적이 없었고, 그래서 docstring 이 약속한 '평일이면 개장' 대신
`NameError` 가 호출자에게 올라갔다.

이런 이름은 **그 분기를 실행하지 않고도** 잡을 수 있다. 실행 여부와 무관하게
정적으로 보이기 때문이다. 그래서 분기 커버리지를 늘리는 것과 별개로 이 검사를
둔다 — 커버리지는 늘리기 어렵고 이건 공짜다.

## 판정은 보수적이다

함수 안 **어디서든** 묶이는 이름은 정의된 것으로 본다. 순서를 따지지 않으므로
'할당 전 사용' 은 놓친다. 대신 **아예 존재하지 않는 이름**은 확실히 잡는다 —
두 사고가 모두 그 형태였고, 순서까지 보려다 거짓 양성을 내면 게이트가 무시된다.

`from x import *` 가 있는 모듈은 건너뛴다. 무엇이 들어왔는지 알 수 없어
전부 미정의로 보이기 때문이다. 지금 그런 모듈은 없다.

프론트엔드는 보지 않는다 — `npm run build` 의 `tsc` 가 같은 일을 이미 한다.
"""
from __future__ import annotations

import ast
import builtins
import pathlib

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
ROOT = BACKEND.parent

_BUILTINS = set(dir(builtins)) | {
    "__file__", "__name__", "__doc__", "__spec__", "__package__", "__builtins__",
}


def _bound_names(node) -> set[str]:
    """이 스코프에서 **직접** 묶이는 이름. 중첩 함수 안쪽은 그 스코프의 몫이다."""
    out: set[str] = set()

    def walk(n):
        for child in ast.iter_child_nodes(n):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                out.add(child.name)
                continue
            if isinstance(child, ast.Lambda):
                continue
            if isinstance(child, (ast.Import, ast.ImportFrom)):
                for alias in child.names:
                    out.add((alias.asname or alias.name).split(".")[0])
            elif isinstance(child, ast.Name) and isinstance(child.ctx, (ast.Store, ast.Del)):
                out.add(child.id)
            elif isinstance(child, ast.ExceptHandler) and child.name:
                out.add(child.name)
            elif isinstance(child, (ast.Global, ast.Nonlocal)):
                out.update(child.names)
            walk(child)

    walk(node)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        args = node.args
        for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
            out.add(arg.arg)
        if args.vararg:
            out.add(args.vararg.arg)
        if args.kwarg:
            out.add(args.kwarg.arg)
    return out


def _loaded_names(node) -> list[ast.Name]:
    out: list[ast.Name] = []

    def walk(n):
        for child in ast.iter_child_nodes(n):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.ClassDef, ast.Lambda)):
                continue
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
                out.append(child)
            walk(child)

    walk(node)
    return out


def undefined_names(src: str) -> list[tuple[int, str, str]]:
    """(줄, 이름, 스코프) 목록. `import *` 가 있으면 빈 목록."""
    tree = ast.parse(src)
    if any(isinstance(n, ast.ImportFrom) and any(a.name == "*" for a in n.names)
           for n in ast.walk(tree)):
        return []

    found: list[tuple[int, str, str]] = []

    def visit(node, enclosing: set[str], label: str):
        scope = enclosing | _bound_names(node)
        for name in _loaded_names(node):
            if name.id not in scope:
                found.append((name.lineno, name.id, label or "<module>"))
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                visit(child, scope, f"{label}.{child.name}" if label else child.name)
            elif isinstance(child, ast.Lambda):
                visit(child, scope, label)

    visit(tree, _bound_names(tree) | _BUILTINS, "")
    return found


# ── 검출기 자기검사 ────────────────────────────────────────────────────────────
#
# "0건" 이 '문제 없음' 인지 '아무것도 안 본 것' 인지 가르는 유일한 방법이다.
# 스코프 분석은 조용히 망가지기 쉽다 — 한 종류의 바인딩을 놓치면 그 뒤로는
# 전부 미정의로 보이거나(소음) 전부 정의된 것으로 보인다(침묵).

_KNOWN_ANSWERS = [
    # 실제로 두 번 난 사고의 형태
    ("import logging\n_log = logging.getLogger(__name__)\n"
     "def f():\n    try:\n        pass\n    except Exception as e:\n"
     "        logger.warning(e)\n", "logger", "모듈 로거 이름이 다르다"),
    ("def f(value):\n    return valeu + 1\n", "valeu", "평범한 오타"),
    # 잡으면 안 되는 것들 — 바인딩의 종류마다 하나씩
    ("import logging\nlogger = logging.getLogger(__name__)\n"
     "def f():\n    logger.warning('x')\n", None, "모듈 전역"),
    ("def f():\n    import json\n    return json.dumps({})\n", None, "함수 안 import"),
    ("x = 0\ndef f():\n    global x\n    x = 1\n    return x\n", None, "global 선언"),
    ("def f(items):\n    return [i * 2 for i in items]\n", None, "컴프리헨션 변수"),
    ("def f():\n    try:\n        pass\n    except Exception as e:\n"
     "        return str(e)\n", None, "except as"),
    ("def f(cm):\n    with cm as h:\n        return h\n", None, "with as"),
    ("def outer():\n    v = 1\n    def inner():\n        return v\n"
     "    return inner()\n", None, "중첩 함수가 바깥 이름을 본다"),
    ("def f(xs):\n    return len(sorted(xs))\n", None, "빌트인"),
    ("class C:\n    A = 1\n    def m(self):\n        return C.A\n", None, "클래스 이름"),
    ("def f(*args, **kw):\n    return args, kw\n", None, "가변 인자"),
    ("def f(xs):\n    return [y for x in xs if (y := x * 2)]\n", None, "월러스"),
]


@pytest.mark.parametrize("src, expected, why", _KNOWN_ANSWERS,
                         ids=[w for _, _, w in _KNOWN_ANSWERS])
def test_detector_gives_known_answers(src, expected, why):
    names = {n for _, n, _ in undefined_names(src)}
    if expected is None:
        assert not names, f"{why}: 잡으면 안 되는데 {names} 를 잡았다"
    else:
        assert expected in names, f"{why}: {expected} 를 놓쳤다 (잡은 것: {names})"


def test_detector_reproduces_the_market_calendar_incident():
    """실제 파일에서 로거 정의만 지우면 잡히는지.

    합성 소스만으로는 부족하다 — 실제 모듈은 훨씬 복잡하고, 스코프 분석이
    그 복잡도에서 무너질 수 있다. 사고가 났던 파일 자체로 확인한다.
    """
    src = (BACKEND / "services" / "market_calendar.py").read_text(encoding="utf-8")
    assert "logger = logging.getLogger(__name__)" in src, (
        "market_calendar 의 로거 정의가 바뀌었다 — 이 재현의 전제가 없다"
    )
    broken = src.replace("logger = logging.getLogger(__name__)",
                         "_absent = logging.getLogger(__name__)", 1)

    names = {n for _, n, _ in undefined_names(broken)}
    assert "logger" in names, (
        "the scanner missed an undefined logger in the very file where this "
        "happened -- scope analysis has broken somewhere. (검출기가 망가졌다.)"
    )


# ── 본 검사 ───────────────────────────────────────────────────────────────────

def test_backend_has_no_undefined_names():
    """`backend/` 전체에 정의되지 않은 이름이 없어야 한다."""
    problems: list[str] = []
    for path in sorted(BACKEND.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        for line, name, scope in undefined_names(path.read_text(encoding="utf-8")):
            problems.append(f"  {rel}:{line}  {name}  (in {scope})")

    assert not problems, (
        f"{len(problems)} name(s) are not defined anywhere they are used:\n"
        + "\n".join(problems)
        + "\nThese raise NameError the moment the line runs. Lines inside "
          "except blocks and fallbacks may never have run, so the suite can be "
          "green while one waits. (실패 경로의 미정의 이름이다.)"
    )

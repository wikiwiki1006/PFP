"""
DB 쓰기 호출이 `except: pass` 에 덮여 있으면 안 된다.

repo 쓰기 함수는 실패하면 예외를 던진다. 호출부가 그 예외를 조용히 삼키면
**쓰기가 안 된 것을 아무도 모른다** — 사용자에게는 저장된 것처럼 보이고,
로그에도 아무것도 남지 않아 나중에 원인을 찾을 단서조차 없다. repo 에 예외를
추가한 작업 자체가 무의미해진다.

이 파일은 `test_market_isolation.py` 와 같은 계열이다: 개별 동작이 아니라
**호출부 전체를 훑어 성질을 고정한다.** grep 으로는 형태가 조금만 달라도 놓친다.

## 게이트로서의 설계

전수 검사 테스트는 **거짓 양성으로 남의 작업을 막는 순간 무력화된다.** 다들
우회하거나 지워버리고, 그러면 게이트 전체가 신뢰를 잃는다. 틀린 게이트는 없는
게이트보다 나쁘다. 그래서 판정을 좁게 잡았다:

- 핸들러 본문이 `pass`(또는 docstring 뿐)일 때만 실패시킨다. "로그가 있는가" 는
  형태가 다양해서 오판이 난다 — 애매하면 통과시킨다.
- 실제로 repo 에서 import 한 이름만 센다. 같은 이름의 지역 함수를 잡지 않는다.
- 의도적으로 삼키는 곳은 허용 목록에 **왜 허용하는지와 함께** 적는다. 이유가
  같이 있어야, 나중에 누가 그 항목을 보고 화석인지 결정인지 구별할 수 있다.

## 쓰기 함수 목록을 하드코딩하지 않는 이유

목록을 손으로 적으면 새 쓰기 함수가 생겼을 때 검사에서 **조용히 빠진다** —
검사가 통과하는데 아무것도 안 보고 있는 상태가 된다. §1.3 과 같은 형태다.
그래서 `backend/db/*.py` 를 파싱해 쓰기 SQL 을 담은 public 함수를 그때그때
뽑는다. 도출 자체가 망가지면 목록이 비어 검사가 조용히 통과하므로,
`test_write_function_detection_still_works` 가 그것을 막는다.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
ROOT = BACKEND.parent
DB_DIR = BACKEND / "db"

# repo 함수 본문에 이 조각이 있으면 쓰기로 본다.
_WRITE_SQL = ("INSERT", "UPDATE ", "DELETE FROM", "TRUNCATE")

# 반드시 검출돼야 하는 핵심 쓰기들. 도출 로직이 망가지면 여기서 먼저 걸린다.
_MUST_DETECT = {
    "save_holding", "add_trade", "delete_holding",
    "update_trade_by_id", "delete_trade_by_id", "wipe_portfolio",
}

# 의도적으로 삼키는 곳. (파일, 감싸는 함수, 호출) -> 왜 허용하는가.
#
# 이유 없이 추가하지 않는다 — 이유가 없으면 다음 사람이 화석인지 결정인지 모른다.
# 그리고 이유는 **요약이고, 원문 위치를 가리킨다.** 여기에 근거를 통째로 옮겨
# 적으면 코드가 바뀌었을 때 이 목록만 낡은 채 남아 둘이 갈라진다.
_ALLOWED = {
    (
        "backend/services/trading_signals.py",
        "get_sp500_universe",
        "save_common",
    ): (
        "캐시 쓰기가 실패해도 정적 유니버스 폴백 결과는 정상이다 — 조용한 것이 "
        "설계된 동작. 근거: get_sp500_universe() docstring "
        "('실패 시 SP500_NASDAQ_UNIVERSE로 폴백')."
    ),
}


# ── 쓰기 함수 도출 ─────────────────────────────────────────────────────────────

def _write_functions() -> dict[str, str]:
    """`backend/db/*.py` 의 public 함수 중 쓰기 SQL 을 담은 것. {함수명: 모듈}"""
    found: dict[str, str] = {}
    for path in sorted(DB_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name.startswith("_"):
                continue
            sql = " ".join(
                s.value.upper()
                for s in ast.walk(node)
                if isinstance(s, ast.Constant) and isinstance(s.value, str)
            )
            if any(k in sql for k in _WRITE_SQL):
                found[node.name] = f"backend.db.{path.stem}"
    return found


# ── 호출부 검사 ────────────────────────────────────────────────────────────────

def _imported_write_names(tree: ast.AST, writes: dict[str, str]) -> set[str]:
    """이 모듈이 backend.db 에서 실제로 가져온 쓰기 이름만.

    이름만 맞으면 잡는 방식은 동명의 지역 함수까지 잡는다. 거짓 양성 하나가
    게이트 전체의 신뢰를 깎으므로, import 를 확인한 것만 센다.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("backend.db"):
            for alias in node.names:
                local = alias.asname or alias.name
                if alias.name in writes:
                    names.add(local)
    return names


def _is_noop(stmt: ast.stmt) -> bool:
    """`pass`, `...`, 그리고 docstring 만인 줄. 셋 다 아무 일도 하지 않는다.

    `...` 은 `ast.Ellipsis` 가 아니라 `Expr(Constant(Ellipsis))` 로 파싱된다.
    (`ast.Ellipsis` 는 3.12 에서 deprecated 고 3.14 에서 사라진다.) 그걸로
    검사하면 `except: ...` 를 조용하지 않다고 읽어 놓친다.
    """
    if isinstance(stmt, ast.Pass):
        return True
    return (isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Constant)
            and (stmt.value.value is Ellipsis or isinstance(stmt.value.value, str)))


def _silent(handler: ast.ExceptHandler) -> bool:
    """핸들러가 아무것도 하지 않는가."""
    return all(_is_noop(s) for s in handler.body)


def _enclosing_function(tree: ast.AST, node: ast.AST) -> str:
    """호출을 감싸는 가장 안쪽 함수 이름. 허용 목록의 키라 줄 번호보다 안정적이다."""
    best = None
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if n.lineno <= node.lineno <= (n.end_lineno or n.lineno):
                if best is None or n.lineno > best.lineno:
                    best = n
    return best.name if best else "<module>"


def _scan_source(rel: str, src: str, writes: dict[str, str]):
    """소스 하나에서 (파일, 줄, 감싸는 함수, 쓰기 함수, except 줄) 을 찾는다."""
    tree = ast.parse(src)
    local = _imported_write_names(tree, writes)
    if not local:
        return []

    # (try 본문 범위, except 줄, 조용한가)
    handlers = [
        (t.body[0].lineno, t.body[-1].end_lineno, h.lineno, _silent(h))
        for t in ast.walk(tree) if isinstance(t, ast.Try)
        for h in t.handlers
    ]

    hits = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in local):
            continue
        covering = [h for h in handlers if h[0] <= node.lineno <= h[1]]
        if not covering:
            continue                       # try 가 없으면 예외가 전파된다 — 정상
        innermost = min(covering, key=lambda h: h[1] - h[0])
        if innermost[3]:
            hits.append((rel, node.lineno, _enclosing_function(tree, node),
                         node.func.id, innermost[2]))
    return hits


def _swallowed_calls() -> list[tuple[str, int, str, str, int]]:
    """(파일, 줄, 감싸는 함수, 호출된 쓰기 함수, except 줄) 목록."""
    writes = _write_functions()
    hits = []
    for path in sorted(BACKEND.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if "/tests/" in rel or "/db/" in rel:
            continue                       # repo 자기 자신은 대상이 아니다
        hits += _scan_source(rel, path.read_text(encoding="utf-8"), writes)
    return hits


# ── 검출기 자체를 검사한다 ─────────────────────────────────────────────────────
#
# 이 검사가 없으면 검출기가 망가져도 "삼키는 곳 0건" 으로 통과한다. 리포를
# 훑는 테스트는 언제나 이 함정을 안고 있다 — 볼 것이 없는 것과 문제가 없는 것이
# 출력에서 똑같이 보인다. 그래서 답을 아는 소스를 넣어 답이 나오는지 본다.
# 프로덕션 파일을 건드리지 않고도 이빨을 확인할 수 있다.

_IMPORT = "from backend.db.portfolio_repo import save_holding\n"

_SWALLOWS = _IMPORT + """
def handler():
    try:
        save_holding("AAPL", 1, 1.0, "Tech", "u1")
    except Exception:
        pass
"""

_LOGS = _IMPORT + """
def handler():
    try:
        save_holding("AAPL", 1, 1.0, "Tech", "u1")
    except Exception as e:
        logger.warning(f"저장 실패: {e}")
"""

_RERAISES = _IMPORT + """
def handler():
    try:
        save_holding("AAPL", 1, 1.0, "Tech", "u1")
    except Exception:
        raise
"""

_NO_TRY = _IMPORT + """
def handler():
    save_holding("AAPL", 1, 1.0, "Tech", "u1")
"""

_ELLIPSIS = _IMPORT + """
def handler():
    try:
        save_holding("AAPL", 1, 1.0, "Tech", "u1")
    except Exception:
        ...
"""

_LOCAL_HOMONYM = """
def save_holding(*a):
    return None

def handler():
    try:
        save_holding("AAPL", 1, 1.0, "Tech", "u1")
    except Exception:
        pass
"""

_CONVERTS = _IMPORT + """
def handler():
    try:
        save_holding("AAPL", 1, 1.0, "Tech", "u1")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
"""

_OUTER_TRY_INNER_LOG = _IMPORT + """
def handler():
    try:
        try:
            save_holding("AAPL", 1, 1.0, "Tech", "u1")
        except Exception as e:
            logger.warning(e)
    except Exception:
        pass
"""


@pytest.mark.parametrize("src, expected, why", [
    (_SWALLOWS, True, "except: pass"),
    (_ELLIPSIS, True, "except: ... (pass 와 같다)"),
    (_LOGS, False, "로그를 남긴다"),
    (_RERAISES, False, "다시 던진다"),
    (_CONVERTS, False, "HTTPException 으로 변환한다 — 삼키는 게 아니다"),
    (_NO_TRY, False, "try 가 없어 예외가 전파된다"),
    (_LOCAL_HOMONYM, False, "repo 에서 import 하지 않은 동명의 지역 함수"),
    (_OUTER_TRY_INNER_LOG, False, "가장 안쪽 핸들러가 로그를 남긴다"),
])
def test_detector_catches_what_it_should(src, expected, why):
    """답을 아는 소스로 검출기를 잰다 — 놓치는 쪽과 과잉 검출 양쪽 모두."""
    hits = _scan_source("synthetic.py", src, {"save_holding": "backend.db.portfolio_repo"})
    assert bool(hits) is expected, (
        f"detector returned {len(hits)} hit(s), expected "
        f"{'one' if expected else 'none'} for: {why}"
    )


# ── 테스트 ────────────────────────────────────────────────────────────────────

def test_write_function_detection_still_works():
    """쓰기 함수 도출이 살아 있는지 먼저 확인한다.

    도출이 망가지면 목록이 비고, 그러면 아래 검사는 볼 것이 없어 **조용히
    통과한다.** 통과했는데 아무것도 안 본 상태가 가장 나쁘다.
    """
    writes = _write_functions()

    missing = _MUST_DETECT - set(writes)
    assert not missing, (
        f"write-function detection missed {sorted(missing)} -- the scan below "
        "would pass while looking at nothing. Check _WRITE_SQL against how "
        "backend/db/*.py spells its statements. "
        "(도출이 망가지면 검사가 조용히 통과한다.)"
    )
    assert len(writes) >= len(_MUST_DETECT), f"only {len(writes)} write functions derived"


def test_no_db_write_is_silently_swallowed():
    """`except: pass` 에 덮인 repo 쓰기 호출이 없어야 한다."""
    unexpected = [
        hit for hit in _swallowed_calls()
        if (hit[0], hit[2], hit[3]) not in _ALLOWED
    ]

    detail = "\n".join(
        f"  {rel}:{ln} in {func}() -> {call}(), swallowed by except at line {hline}"
        for rel, ln, func, call, hline in unexpected
    )
    assert not unexpected, (
        f"{len(unexpected)} DB write call(s) sit inside a handler that only "
        f"passes, so a failed write leaves no trace anywhere:\n{detail}\n"
        "Log it, re-raise it, or add it to _ALLOWED with the reason it is "
        "deliberate. (쓰기 실패가 조용히 사라진다.)"
    )


def test_allowlist_has_no_fossils():
    """허용 목록에 이제 존재하지 않는 항목이 남아 있으면 안 된다.

    항목이 가리키던 코드가 사라졌는데 목록만 남으면, 다음 사람은 그것을 아직
    유효한 결정으로 읽는다. 허용 목록은 코드와 같이 움직여야 한다.
    """
    live = {(rel, func, call) for rel, _, func, call, _ in _swallowed_calls()}
    fossils = sorted(set(_ALLOWED) - live)

    assert not fossils, (
        f"_ALLOWED lists {len(fossils)} entry/entries that no longer match any "
        f"swallowed call: {fossils}. Delete them. "
        "(코드가 사라진 허용 항목은 화석이다.)"
    )

"""
Macro Spreads 는 **완전히** 지웠다 (d35e41f, 사용자 요청).

탭만 가린 것이 아니라 그 기능이 쓰던 것을 전부 걷었다 — 엔드포인트
`GET /api/signals/market-situation`, 계산(`compute_macro_spread_levels` 와
헬퍼), 스케줄러의 24시간 주기 작업, 프론트 패널·탭·API 함수·타입. 탭만
빼면 나머지가 호출자 없이 남아 다음 사람이 "이 엔드포인트는 쓰이는구나" 로
읽는다는 것이 그 커밋의 이유다.

여기서 지키는 성질은 셋이다.

  ① 그 경로는 **존재한 적 없는 경로와 구별되지 않는다**
  ② 스케줄러 루프가 그 작업을 돌리지 않는다
  ③ 그 기능의 이름이 백엔드 코드 어디에도 없다 (문서·주석은 괜찮다)

프론트 쪽 되살림 — API 함수만 돌아와 없는 경로를 부르는 것 — 은
`test_frontend_calls_live_routes.py` 가 기능 이름과 무관하게 잡는다.

되살리기로 **결정**했다면 이 파일도 그 결정과 함께 지운다. 이 검사가 막는 것은
결정 없이 돌아오는 경우다 — 병합이 옛 브랜치를 끌고 오거나, 되돌리기가 한
커밋을 통째로 되살리는 것.

## ① 을 상수로 적지 않는 이유

"404 를 기대" 로 쓰면 틀린다. 그 경로의 응답은 **빌드 상태에 달렸다.**
`frontend/dist` 가 있으면 `main.py` 가 SPA 캐치올을 등록해 GET 은
`{"detail":"존재하지 않는 API 경로입니다."}` 404 를 주고, 다른 메서드는
캐치올의 경로에만 걸려 405(`allow: GET`)가 된다. 없으면 전부 Starlette 기본
404 다. 그래서 같은 앱에 **존재한 적 없는 이웃 경로**를 같이 묻고 둘이 같은지를
본다 — 어느 빌드 상태에서든 성립하는 주장이다.

그 비교가 무언가를 재는지는 대조군이 보인다: 같은 접두사의 살아 있는 이웃은
존재한 적 없는 경로와 **구별된다.**
"""
from __future__ import annotations

import ast
import pathlib
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
BACKEND = ROOT / "backend"
ET = ZoneInfo("America/New_York")

_REMOVED = "/api/signals/market-situation"
_NEVER_EXISTED = "/api/signals/zz-never-a-route"
# 살아 있는 이웃. 필수 쿼리(ticker)가 없으면 **핸들러에 들어가기 전에** 검증에서
# 끝나므로 네트워크·DB 를 건드리지 않고 "여기에 라우트가 있다" 만 드러난다.
_LIVE_NEIGHBOUR = "/api/signals/signal-score"
_METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")
# 지운 핸들러는 market 으로 갈렸다(한국은 available:false). 쿼리가 라우팅을
# 바꾸지는 않지만, 되살아난 핸들러가 한 시장에서만 답할 수도 있어 같이 묻는다.
_QUERIES = ("", "?market=US", "?market=KR")


# ── ① 경로 ────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    """진짜 앱 — 라우터 등록 순서와 SPA 캐치올까지 운영과 같은 라우팅.

    `with TestClient(app)` 로 열지 않는다. 열면 startup(DB 풀·스키마·스케줄러)이
    돈다. 라우팅을 재는 데는 필요 없다. 서버 예외는 500 응답으로 받는다 — 되살아난
    핸들러가 터지는 것도 "존재한 적 없는 경로와 다르다" 의 한 형태다.
    """
    from backend.main import app
    return TestClient(app, raise_server_exceptions=False)


def _answer(client, method: str, path: str) -> tuple:
    r = client.request(method, path)
    return (r.status_code, r.headers.get("content-type"), r.headers.get("allow"), r.content)


def _body(answer: tuple) -> str:
    """실패 메시지용. 비교는 바이트로 하고, 사람에게는 글자로 보여 준다."""
    return repr(answer[3].decode("utf-8", errors="replace")[:80])


def test_the_removed_endpoint_answers_like_a_path_that_never_existed(client):
    differ = []
    for method in _METHODS:
        for q in _QUERIES:
            gone = _answer(client, method, _REMOVED + q)
            never = _answer(client, method, _NEVER_EXISTED + q)
            if gone != never:
                differ.append(
                    f"{method} {_REMOVED}{q} -> {gone[0]} {_body(gone)}   "
                    f"never-existed -> {never[0]} {_body(never)}"
                )
    assert not differ, (
        "the removed Macro Spreads endpoint can be told apart from a path that never "
        "existed, so something serves it again:\n  " + "\n  ".join(differ) + "\n"
        "d35e41f removed it at the user's request. If it is coming back by decision, "
        "delete this test with that decision. (지운 엔드포인트가 다시 답한다)"
    )


def test_control_a_live_neighbour_is_told_apart(client):
    """대조군 — 이 비교는 살아 있는 경로를 **알아본다.**

    없으면 위 검사는 "모든 경로가 같은 응답" 인 앱(라우터가 통째로 빠진 앱)에서도
    통과한다.
    """
    live = _answer(client, "GET", _LIVE_NEIGHBOUR)
    never = _answer(client, "GET", _NEVER_EXISTED)
    assert live != never, (
        f"GET {_LIVE_NEIGHBOUR} answered exactly like a path that never existed "
        f"({live[0]} {_body(live)}) -- either the signals router is not mounted or "
        "this comparison cannot see routes, and the removal check above measures "
        "nothing. (대조군이 살아 있는 경로를 못 알아본다)"
    )


# ── ② 스케줄러 ─────────────────────────────────────────────────────────────────

def _code_names(fn) -> set[str]:
    """함수 코드가 쓰는 이름과 문자열 상수 — 중첩 코드 객체까지, docstring 은 뺀다.

    작업 이름표(`_run_safe` 의 첫 인자)만 보면 이름표만 바꿔 되살린 작업을
    놓친다. 지운 작업은 `compute_macro_spread_levels` 를 import 하고
    `"market_situation"` 키에 저장했다 — 코드 객체에 그 흔적이 남는다.
    """
    code = getattr(fn, "__code__", None)
    if code is None:
        return set()
    doc = getattr(fn, "__doc__", None)
    out: set[str] = set()
    stack = [code]
    while stack:
        c = stack.pop()
        out.update(c.co_names)
        for const in c.co_consts:
            if isinstance(const, str) and const != doc:
                out.add(const)
            elif hasattr(const, "co_names"):
                stack.append(const)
    return out


_FEATURE_TERMS = ("macro_spread", "market_situation", "market-situation")


def _mentions_feature(text: str) -> bool:
    low = text.lower()
    return any(term in low for term in _FEATURE_TERMS)


def _drive_the_loop(monkeypatch) -> list[tuple[str, object]]:
    """`scheduler._loop` 를 **가짜 시계로** 이틀 이상 돌리고, 시작된 작업을 모은다.

    작업은 실행하지 않는다 — `_run_safe` 를 기록기로 바꾼다. 재는 것은 "루프가
    무엇을 시작하는가" 다. 첫 반복만 보면 첫 실행을 미룬 작업(`last_x = now` 로
    시작)을 놓치므로, 가장 긴 주기의 두 배 동안 돌린다. 장중·장외 분기가 둘 다
    오도록 미국 확장 시간대 판정은 가짜 시계를 따른다. 일일 수집은 스레드로
    시작되므로 그 자리에서 실행해 안쪽 작업까지 기록한다.
    """
    from backend.db import scheduler as sch
    from backend.services.market_calendar import is_us_extended_hours

    intervals = [v for k, v in vars(sch).items()
                 if k.startswith("_") and k.endswith("_INTERVAL") and isinstance(v, (int, float))]
    span = max(2 * 86400, 2 * max(intervals))
    start = datetime(2026, 9, 14, 0, 0, tzinfo=ET).timestamp()      # 월요일 0시
    step = getattr(sch, "_SNAPSHOT_INTERVAL", 60)
    clock = {"t": start, "checks": 0}
    ran: list[tuple[str, object]] = []

    class _Stop:
        def is_set(self):
            # 루프가 더는 wait() 로 쉬지 않게 바뀌면 시계가 안 가서 영원히 돈다.
            # 반복 수에 상한을 둬 멈추고, 아래 대조군이 그걸 드러낸다.
            clock["checks"] += 1
            return clock["t"] - start >= span or clock["checks"] > 4 * span / step

        def wait(self, seconds):
            clock["t"] += seconds
            return self.is_set()

    class _InlineThread:
        def __init__(self, target=None, **_kwargs):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(sch, "_stop_event", _Stop())
    monkeypatch.setattr(sch, "time", SimpleNamespace(time=lambda: clock["t"]))
    monkeypatch.setattr(sch, "threading", SimpleNamespace(Thread=_InlineThread))
    monkeypatch.setattr(sch, "_run_safe", lambda name, fn, *a: ran.append((name, fn)))
    monkeypatch.setattr(sch, "_in_extended_hours",
                        lambda: is_us_extended_hours(datetime.fromtimestamp(clock["t"], ET)))
    monkeypatch.setattr(sch, "_sp500_update_due", lambda: True)
    monkeypatch.setattr(sch, "_sp500_volume_backfill_due", lambda: False)

    sch._loop()
    assert clock["t"] - start >= span, (
        f"the loop stopped after {clock['t'] - start:.0f}s of simulated time, short of "
        f"{span}s -- it no longer waits on _stop_event, so this harness cannot advance "
        "its clock and saw only the first pass. (가짜 시계가 가지 않았다)"
    )
    return ran


def test_the_scheduler_loop_never_starts_the_macro_spread_job(monkeypatch):
    ran = _drive_the_loop(monkeypatch)

    # 대조군 — 기록기가 실제로 루프의 작업을 받는다. 지운 작업의 옆자리(같은
    # Timing Engine 절의 신호 스캔)와, 지운 작업과 같은 24시간 주기의 작업이
    # **두 번** 시작됐어야 이 시뮬레이션이 그 주기를 넘겼다고 말할 수 있다.
    names = [name for name, _ in ran]
    assert "signal_scan" in names and names.count("universe") >= 2, (
        f"the harness did not see the loop's periodic jobs (saw {sorted(set(names))}, "
        f"universe x{names.count('universe')}) -- if these jobs were renamed, rename "
        "them here; otherwise the check below is looking at nothing. "
        "(대조군: 루프가 도는 작업을 기록기가 못 받았다)"
    )

    offenders = sorted({
        f"{name!r} -> {getattr(fn, '__qualname__', fn)}"
        for name, fn in ran
        if _mentions_feature(name)
        or _mentions_feature(getattr(fn, "__name__", ""))
        or any(_mentions_feature(s) for s in _code_names(fn))
    })
    assert not offenders, (
        f"the scheduler loop starts the removed Macro Spreads job: {offenders} -- "
        "d35e41f deleted it with the endpoint it fed. (지운 주기 작업이 다시 돈다)"
    )


# ── ③ 코드 ────────────────────────────────────────────────────────────────────

def _docstring_ids(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                ids.add(id(body[0].value))
    return ids


def _feature_mentions(source: str, label: str) -> list[str]:
    """코드 안의 기능 이름 — 정의·참조·import·인자·**문서가 아닌** 문자열.

    줄 단위로 찾지 않는다. 지운 사실을 적어 둔 주석이나 docstring("Macro
    Spreads 는 지웠다")까지 잡으면 기록을 남기는 사람이 벌을 받는다. AST 에는
    주석이 없고, docstring 은 위치로 가려낸다.
    """
    tree = ast.parse(source, filename=label)
    docs = _docstring_ids(tree)
    hits = []
    for node in ast.walk(tree):
        texts: list[str] = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            texts.append(node.name)
        elif isinstance(node, ast.Name):
            texts.append(node.id)
        elif isinstance(node, ast.Attribute):
            texts.append(node.attr)
        elif isinstance(node, ast.alias):
            texts += [node.name, node.asname or ""]
        elif isinstance(node, ast.arg):
            texts.append(node.arg)
        elif isinstance(node, ast.keyword) and node.arg:
            texts.append(node.arg)
        elif (isinstance(node, ast.Constant) and isinstance(node.value, str)
              and id(node) not in docs):
            texts.append(node.value)
        for text in texts:
            if _mentions_feature(text):
                hits.append(f"{label}:{getattr(node, 'lineno', '?')} {text[:70]!r}")
    return hits


_TRICKY = '''
"""Macro Spreads 와 market_situation 은 d35e41f 에서 지웠다."""   # 문서 — 통과
# compute_macro_spread_levels 를 되살리지 말 것                    # 주석 — 통과

def keep():
    """_update_macro_spread_history 는 없다."""                    # 문서 — 통과
    log("Macro Spreads removed")                                   # 다른 표기 — 통과

def compute_macro_spread_levels():                                 # 정의
    return get_common(f"market_situation:{market}")                # 캐시 키

from backend.services.trading_signals import compute_macro_spread_levels as levels   # import
_run_safe("macro_spread", levels)                                  # 작업 이름
router.get("/market-situation")                                    # 경로
'''


def test_control_the_scanner_sees_code_not_prose():
    """대조군 — 스캐너가 답을 아는 소스에서 코드만 잡는다.

    못 잡으면 아래 검사는 언제나 초록이고, 문서까지 잡으면 지운 사실을 적어
    두는 사람이 빨간불을 받는다.
    """
    hits = _feature_mentions(_TRICKY, "tricky.py")
    lines = sorted({int(h.split(":")[1].split()[0]) for h in hits})
    assert lines == [9, 10, 12, 13, 14], (
        f"scanner hit lines {lines}, expected the code lines 9, 10, 12, 13, 14 only:\n  "
        + "\n  ".join(hits)
    )


def test_no_backend_code_carries_the_feature():
    offenders = []
    for path in sorted(BACKEND.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith("backend/tests/") or "__pycache__" in path.parts:
            continue
        offenders += _feature_mentions(path.read_text(encoding="utf-8"), rel)
    assert not offenders, (
        "Macro Spreads code is back in the backend:\n  " + "\n  ".join(offenders) + "\n"
        "d35e41f removed the endpoint, the calculation and the scheduler job together; a "
        "piece without its callers reads as a live feature to the next person. "
        "(지운 기능의 코드가 돌아왔다)"
    )

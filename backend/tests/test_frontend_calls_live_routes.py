"""
프론트가 부르는 API 경로는 전부 백엔드에 **살아 있는** 경로다.

계기는 Macro Spreads 삭제(d35e41f)다. 그 커밋은 엔드포인트와 프론트의
`getMarketSituation` 을 같이 지웠다. 프론트 쪽만 되돌아오면 — 옛 브랜치에서
패널을 살리거나 병합이 한쪽만 가져오면 — `npm run build` 는 초록이다. 타입도
맞고 함수도 있다. 그 탭을 누르는 순간에야 404 가 뜬다.

같은 모양은 기능 이름과 무관하게 반복된다: 백엔드에서 경로를 지우거나 이름을
바꿨는데 프론트 호출이 남는 것. 그래서 이름을 금지하지 않고 **성질**을 건다 —
프론트 소스의 모든 `<x>.<method>('/api/...')` 가 백엔드 라우팅에서 실제로 그
메서드로 잡혀야 한다.

## 라우팅은 진짜 런타임에 묻는다

경로 템플릿을 여기서 정규식으로 옮겨 적어 대조하지 않는다 (CLAUDE.md §6).
앱의 라우트 객체에 Starlette 가 요청마다 쓰는 `matches(scope)` 를 그대로 묻는다.

그래야 하는 이유를 이 검사를 만들다 겪었다. FastAPI 0.138 은 `include_router`
를 평평하게 펴지 않고 `_IncludedRouter` 로 감싸 둔다. `app.routes` 에서
`APIRoute` 만 골라 대조했더니 include 된 라우터의 경로는 하나도 안 보이고 앱에 직접
붙은 것(`/health`·루트 정적 파일·SPA 캐치올)만 보였다. 그 상태로 GET 호출은
전부 캐치올에 잡혀 "살아 있음" 으로 나왔다. 그래서:

  - SPA 캐치올은 이름이 아니라 **존재한 적 없는 경로를 잡는가** 로 빼낸다
  - 대조군: 살아 있는 이웃은 잡힌다 (감싼 라우터 안까지 보는지)
  - 대조군: 같은 경로라도 메서드가 다르면 안 잡힌다 (FULL 과 PARTIAL 을 가르는지)

## 추출은 TypeScript 파서로

`helpers/extract_api_calls.js` 가 `frontend/node_modules/typescript` 로 소스를
읽는다. 추출기가 모르는 모양(변수에 담은 경로, `fetch`)은 조용히 빼지 않고
`stray` 로 돌려 이 검사를 빨갛게 만든다. 추출기 자체는 답을 아는 소스로 먼저
잰다.
"""
from __future__ import annotations

import itertools
import json
import pathlib
import shutil
import subprocess

import pytest
from starlette.routing import Match

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
EXTRACTOR = ROOT / "backend" / "tests" / "helpers" / "extract_api_calls.js"
FRONTEND_SRC = ROOT / "frontend" / "src"
API_LAYER = "frontend/src/api/index.ts"

# `${…}` 자리에 넣는 한 조각. 숫자로 둔다 — `{x}` 에도, `{x:int}` 같은 변환기가
# 붙은 경로에도 잡힌다.
_SAMPLE_SEGMENT = "1"
_NEVER_EXISTED = "/api/signals/zz-never-a-route"
# 살아 있는 GET 전용 경로.
_LIVE_GET = "/api/signals/signal-score"


def _extract(*targets: pathlib.Path) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.fail(
            "node is not on PATH, so the frontend call sites cannot be read. It is "
            "already required for `npm run build`. (node 가 없다)"
        )
    proc = subprocess.run(
        [node, str(EXTRACTOR), *map(str, targets)],
        capture_output=True, text=True, encoding="utf-8",
    )
    if proc.returncode != 0:
        pytest.fail(f"extract_api_calls.js exited {proc.returncode}: {proc.stderr.strip()}")
    return json.loads(proc.stdout)


def _scope(method: str, path: str) -> dict:
    return {"type": "http", "method": method, "path": path, "root_path": "",
            "query_string": b"", "headers": []}


def _served(routes, method: str, path: str) -> bool:
    scope = _scope(method, path)
    return any(route.matches(scope)[0] == Match.FULL for route in routes)


@pytest.fixture(scope="module")
def endpoint_routes():
    """앱의 최상위 라우트 중 **엔드포인트인 것.**

    존재한 적 없는 경로를 GET 으로 온전히 잡는 라우트는 엔드포인트가 아니라
    캐치올이다 (`frontend/dist` 가 있을 때만 등록된다). 이름으로 고르지 않아서
    이름이 바뀌어도 같은 기준으로 빠진다.
    """
    from backend.main import app
    never = _scope("GET", _NEVER_EXISTED)
    return [r for r in app.routes if r.matches(never)[0] != Match.FULL]


def _concrete_paths(call: dict) -> list[str]:
    """템플릿을 실제 요청 경로들로. 쿼리 문자열은 라우팅과 무관해 뗀다."""
    choices = [values if values else [_SAMPLE_SEGMENT] for values in call["spans"]]
    out = []
    for combo in itertools.product(*choices):
        text = call["parts"][0] + "".join(v + p for v, p in zip(combo, call["parts"][1:]))
        out.append(text.split("?", 1)[0])
    return out


# ── 대조군 ────────────────────────────────────────────────────────────────────

def test_control_the_matcher_sees_included_routes_and_their_methods(endpoint_routes):
    assert _served(endpoint_routes, "GET", _LIVE_GET), (
        f"GET {_LIVE_GET} is not found in the app's routes -- the matcher does not "
        "look inside included routers (FastAPI wraps them as _IncludedRouter), so every "
        "call would look dead or, with the SPA fallback counted, alive. "
        "(대조군: 감싼 라우터 안을 못 본다)"
    )
    assert not _served(endpoint_routes, "POST", _LIVE_GET), (
        f"POST {_LIVE_GET} counts as served, but that route is GET only -- the matcher "
        "accepts a path match without the method, so a call with the wrong verb passes. "
        "(대조군: 메서드를 안 본다)"
    )


_TRICKY = """\
// api.get('/api/in-a-line-comment')
/* api.post('/api/in-a-block-comment') */
import { api } from './client'

/** 예: api.delete('/api/in-a-jsdoc') */
export const example = "api.get('/api/inside-a-string')"

export const typed = () => api.get<{ ok: boolean }>('/api/typed')

export const multiline = (id: string) =>
  api.put(
    `/api/items/${id}`,
    { id },
  )

export const social = (kind: 'naver' | 'kakao', token: string) =>
  api.post(`/api/auth/${kind}`, { token })

export const inlineQuery = (n: number) => api.post(`/api/scan?top_n=${n}`)

const hidden = '/api/held-in-a-variable'
export const viaVariable = () => api.get(hidden)

export const Link = () => <a href="/api/in-jsx">x</a>
"""


def test_extractor_reads_calls_not_text(tmp_path):
    """추출기 자기검사 — 답을 아는 소스.

    주석·JSDoc·문자열 속 호출은 호출이 아니다. 여러 줄 인자와 제네릭 호출은
    호출이다. 매개변수의 리터럴 유니언은 값으로 펼친다. 변수에 담긴 경로와 JSX
    속성은 **모르는 모양**이라 stray 로 온다.
    """
    src = tmp_path / "tricky.tsx"
    src.write_text(_TRICKY, encoding="utf-8")

    got = _extract(src)

    calls = sorted((c["method"], tuple(c["parts"]), json.dumps(c["spans"])) for c in got["calls"])
    assert calls == sorted([
        ("GET", ("/api/typed",), "[]"),
        ("PUT", ("/api/items/", ""), "[null]"),
        ("POST", ("/api/auth/", ""), '[["naver", "kakao"]]'),
        ("POST", ("/api/scan?top_n=", ""), "[null]"),
    ]), calls
    assert sorted(s["text"] for s in got["stray"]) == [
        '"/api/in-jsx"', "'/api/held-in-a-variable'",
    ], got["stray"]

    social = next(c for c in got["calls"] if c["parts"][0] == "/api/auth/")
    assert sorted(_concrete_paths(social)) == ["/api/auth/kakao", "/api/auth/naver"]
    scan = next(c for c in got["calls"] if c["parts"][0].startswith("/api/scan"))
    assert _concrete_paths(scan) == ["/api/scan"]


# ── 성질 ──────────────────────────────────────────────────────────────────────

def test_every_frontend_api_call_has_a_live_backend_route(endpoint_routes):
    got = _extract(FRONTEND_SRC)

    assert not got["stray"], (
        "API paths the extractor cannot attribute to a call -- a path held in a variable "
        "or sent through fetch is invisible to the check below, which would then pass by "
        "not counting it. Call api.<method>('/api/...') directly or teach "
        "extract_api_calls.js the new shape:\n  "
        + "\n  ".join(f"{s['file']}:{s['line']} {s['text']}" for s in got["stray"])
        + "\n(추출기가 모르는 모양)"
    )
    assert any(c["file"] == API_LAYER for c in got["calls"]), (
        f"no calls were read from {API_LAYER} -- the API layer moved or the extractor "
        "is reading the wrong tree, and 'no dead calls' would be vacuous. "
        "(API 계층에서 호출을 하나도 못 읽었다)"
    )

    dead = []
    for call in got["calls"]:
        for path in _concrete_paths(call):
            if not _served(endpoint_routes, call["method"], path):
                dead.append(f"{call['method']} {path}   ({call['file']}:{call['line']})")
    assert not dead, (
        "the frontend calls API routes the backend does not serve:\n  "
        + "\n  ".join(dead) + "\n"
        "Either the backend route was removed or renamed and this caller was left "
        "behind, or a removed caller came back without its route. The build cannot see "
        "this; the user sees a 404/405 when they reach that screen. "
        "(프론트가 없는 경로를 부른다)"
    )

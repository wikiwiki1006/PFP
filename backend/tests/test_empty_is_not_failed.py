"""
화면에서 **"없음" 과 "못 받음" 이 같은 칸이 되지 않게** 한다.

```jsx
{q.isLoading && <p>로딩 중...</p>}
{!q.isLoading && rows.length === 0 && <p>저장된 레포트 없음</p>}
```

`isError` 가 없다. 서버가 죽어도 목록이 비어 보이고, 사용자는 **자기가 만든
리포트가 사라진 줄 안다.** 기다리면 되는 상황을 "없음" 으로 읽는다.
CLAUDE.md §1.3 의 프론트판이다 — 백엔드에서 없앤 형태가 화면에서 다시 난다.

실제로 세 번 났다. `LensReport` 의 주식·산업 목록 두 곳이 그랬고(고쳐졌다),
**같은 파일의 세 번째 자리는 아직 그대로다.** 두 고친 자리 바로 옆인데도
남았다는 것이, 이걸 사람 눈에 맡기면 안 된다는 근거다.

## 무엇을 세는가

컴포넌트(파일 최상위 함수) 하나를 단위로 본다.

  · JSX 에서 `X.length === 0` 으로 빈 상태를 그리고,
  · 그 조건줄(또는 바로 위 몇 줄)이 어떤 쿼리 `qQ` 를 언급하는데,
  · **그 컴포넌트 안에서 `qQ.isError` 를 한 번도 읽지 않으면** 위반이다.

컴포넌트 단위인 것이 중요하다. `LensReport.tsx` 에는 `histQ` 가 세 개
있고 서로 다른 컴포넌트 것이다. 파일 단위로 보면 고쳐진 두 곳의
`histQ.isError` 가 **안 고쳐진 세 번째를 가려 준다.**

## 판정기를 믿기 전에 판정기를 잰다

"아무것도 못 찾음" 과 "아무 문제 없음" 은 화면에서 똑같이 생겼다. 그래서
아래 `test_the_detector_sees_both_answers` 가 정답을 아는 합성 입력으로
양방향을 확인한다. 그게 없으면 정규식이 하나 어긋나 **영원히 0건**을
보고하는 상태를 초록으로 읽는다.

## 원장은 양방향이다

`_KNOWN` 에 없는 위반이 생기면 실패한다. **`_KNOWN` 에 적힌 것이 고쳐져도
실패한다** — 고친 사람이 줄을 지우게 만들어서, 이 목록이 조용히 낡지 않게
한다.
"""
from __future__ import annotations

import pathlib
import re

import pytest

_SRC = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src"

# 최상위 컴포넌트·헬퍼의 시작. 들여쓰기 없는 선언만 경계로 본다.
_TOP_LEVEL = re.compile(r"^(?:export\s+default\s+)?(?:async\s+)?"
                        r"(?:function|const|class)\s+([A-Za-z_$][\w$]*)")
_EMPTY_STATE = re.compile(r"\.length\s*===\s*0")
# 이 리포의 쿼리 변수는 전부 `...Q` 로 끝난다 (useQuery/useDemoQuery 결과).
_QUERY_USE = re.compile(r"\b([A-Za-z_$][\w$]*Q)\s*\.\s*(?:isLoading|isError|data|isFetching)\b")
_LOOKBACK = 4          # 같은 JSX 조건 묶음이 걸치는 줄 수


class Violation:
    def __init__(self, path, component, query, line_no, line):
        self.key = f"{path}::{component}::{query}"
        self.line_no = line_no
        self.line = line.strip()

    def __repr__(self):
        return f"{self.key} (:{self.line_no}) {self.line[:70]}"


def _components(lines: list[str]) -> list[tuple[str, int, int]]:
    """(이름, 시작줄index, 끝줄index) — 파일 최상위 선언으로 자른다."""
    marks = [(m.group(1), i) for i, l in enumerate(lines)
             if (m := _TOP_LEVEL.match(l))]
    if not marks:
        return [("<module>", 0, len(lines))]
    out = []
    for (name, start), (_, nxt) in zip(marks, marks[1:] + [("", len(lines))]):
        out.append((name, start, nxt))
    return out


def scan_source(text: str, path: str = "?") -> list[Violation]:
    """빈 상태를 그리면서 그 쿼리의 실패를 안 읽는 자리."""
    lines = text.splitlines()
    found: list[Violation] = []
    for name, start, end in _components(lines):
        block = lines[start:end]
        guarded = {m.group(1) for l in block
                   for m in re.finditer(r"\b([A-Za-z_$][\w$]*Q)\s*\.\s*isError\b", l)}
        for i, line in enumerate(block):
            if not _EMPTY_STATE.search(line):
                continue
            # 조건줄 자신부터 위로 훑어 이 빈 상태가 어느 쿼리에 달렸는지 찾는다.
            window = block[max(0, i - _LOOKBACK):i + 1]
            queries = {m.group(1) for l in window for m in _QUERY_USE.finditer(l)}
            for q in sorted(queries - guarded):
                found.append(Violation(path, name, q, start + i + 1, line))
    return found


def scan_tree() -> list[Violation]:
    out = []
    for f in sorted(_SRC.rglob("*.tsx")):
        rel = f.relative_to(_SRC).as_posix()
        out.extend(scan_source(f.read_text(encoding="utf-8"), rel))
    return out


# ── 판정기 자체를 잰다 ─────────────────────────────────────────────────────────
#
# 정답을 아는 입력으로 **양방향**을 확인한다. 한 방향만 재면 "아무것도 안
# 잡는" 정규식이 초록으로 통과한다.

_CAUGHT = """
function Broken() {
  const histQ = useQuery({ queryKey: ['x'] })
  return (
    <div>
      {histQ.isLoading && <p>로딩 중...</p>}
      {!histQ.isLoading && rows.length === 0 && <p>저장된 레포트 없음</p>}
    </div>
  )
}
"""

_CAUGHT_SPLIT_LINES = """
function BrokenAcrossLines() {
  const sectorQ = useQuery({ queryKey: ['s'] })
  return (
    <div>
      {sectorQ.isLoading ? (
        <p>로딩 중...</p>
      ) : sorted.length === 0 ? (
        <p>데이터 없음</p>
      ) : <Table />}
    </div>
  )
}
"""

_CLEAN_GUARDED = """
function Guarded() {
  const histQ = useQuery({ queryKey: ['x'] })
  return (
    <div>
      {histQ.isLoading && <p>로딩 중...</p>}
      {histQ.isError && <p>목록을 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.</p>}
      {!histQ.isLoading && !histQ.isError && rows.length === 0 && <p>저장된 레포트 없음</p>}
    </div>
  )
}
"""

_CLEAN_NO_QUERY = """
function LocalOnly({ items }) {
  const [text, setText] = useState('')
  if (items.length === 0) return <p>항목이 없습니다</p>
  return <button disabled={text.length === 0}>보내기</button>
}
"""

# 같은 파일의 다른 컴포넌트가 고쳐져 있어도 안 고쳐진 쪽이 가려지면 안 된다.
# 이게 실제로 일어난 형태다 — `LensReport.tsx` 의 `histQ` 세 개.
_SHADOWING = _CLEAN_GUARDED + _CAUGHT


@pytest.mark.parametrize("source, expected, why", [
    pytest.param(_CAUGHT, 1, "한 줄 조건", id="한줄-조건"),
    pytest.param(_CAUGHT_SPLIT_LINES, 1, "조건이 여러 줄에 걸친다", id="여러줄-삼항"),
    pytest.param(_CLEAN_GUARDED, 0, "isError 를 읽는다", id="정상-가드됨"),
    pytest.param(_CLEAN_NO_QUERY, 0, "쿼리와 무관한 빈 상태", id="정상-쿼리없음"),
    pytest.param(_SHADOWING, 1, "고쳐진 이웃이 가리면 안 된다", id="같은이름-다른컴포넌트"),
])
def test_the_detector_sees_both_answers(source, expected, why):
    """정답을 아는 입력에서 맞는 개수를 센다.

    "못 찾음" 과 "문제 없음" 은 결과가 똑같이 생긴다. 이 검사가 없으면
    정규식 하나가 어긋나 **영원히 0건**인 상태가 초록으로 남는다.
    """
    hits = scan_source(source, "synthetic.tsx")
    assert len(hits) == expected, f"{why}: {expected}건이어야 하는데 {hits}"


def test_the_detector_actually_reads_the_repository():
    """전제 — 훑을 파일이 있다.

    경로가 어긋나면 위반 0건이 나오고, 그건 "깨끗하다" 와 구별되지 않는다.
    """
    files = list(_SRC.rglob("*.tsx"))
    assert len(files) > 20, f"frontend/src 에서 tsx 를 {len(files)}개만 찾았다 — 경로가 틀렸다"


# ── 원장 ───────────────────────────────────────────────────────────────────────
#
# 지금 남아 있는 자리. **고치면 이 줄을 지워야 한다** — 안 지우면 실패한다.
# 각 항목의 근거는 그 코드 옆이 아니라 여기 한 줄로만 적는다: 형태가 전부
# 같아서 (실패를 "없음" 으로 그린다) 설명이 길어질수록 원장이 낡는다.
_KNOWN = {
    "pages/AlphaTerminal.tsx::HoldingsPanel::tradesQ":
        "거래 조회가 실패해도 '거래 기록 없음'",
    "pages/AlphaTerminal.tsx::SectorPerfPanel::sectorTableQ":
        "섹터표 조회가 실패해도 '데이터 없음'",
    "pages/LensReport.tsx::HistoryTab::histQ":
        "위 둘과 같은 파일인데 **안 고쳐졌다** — 세 번째 자리",
    "pages/MacroScenario.tsx::MacroScenario::histQ":
        "`histQ.data?.length === 0` 이라 실패하면 아무것도 안 그린다 — 빈 패널",
}


def test_no_new_place_shows_a_failure_as_emptiness():
    """원장에 없는 자리가 생기면 실패한다."""
    new = [v for v in scan_tree() if v.key not in _KNOWN]

    assert not new, (
        "a new screen renders a failed fetch as an empty result -- the user "
        "reads 'wait and retry' as 'your data is gone'. Read that query's "
        "`.isError` and say what happened instead of drawing an empty state:\n"
        + "\n".join(f"  {v}" for v in new)
    )


def test_the_ledger_does_not_outlive_the_problem():
    """원장에 적힌 것이 고쳐졌으면 그 줄을 지워야 한다.

    한 방향만 검사하면 목록이 조용히 낡는다. 고친 사람은 초록을 보고
    지나가고, 다음 사람은 그 줄을 읽고 "아직 안 고쳐졌구나" 로 믿는다.
    """
    still = {v.key for v in scan_tree()}
    fixed = sorted(k for k in _KNOWN if k not in still)

    assert not fixed, (
        "these are fixed but still listed in _KNOWN -- delete the lines so the "
        "ledger keeps meaning what it says:\n"
        + "\n".join(f"  {k}  ({_KNOWN[k]})" for k in fixed)
    )

"""
색을 고르는 자리에서 `null → 0` 으로 바꾸지 않는다.

값이 없으면 화면은 `—` 를 그린다. 그런데 **색은 따로 계산된다.** 그 계산에
`null` 을 `0` 으로 바꾸는 헬퍼가 끼면, 계산 불가가 "0" 의 색을 입는다:

    fv(m.vix) > 25 ? 빨강 : fv(m.vix) > 18 ? 주황 : 초록
    VIX 를 못 읽음 → fv → 0 → 둘 다 거짓 → **초록** ("변동성 낮음")

숫자는 `—` 인데 타일은 초록이다. 사용자는 숫자가 비었다는 건 알아채도, 초록이
"안심해도 된다" 는 뜻인지 "모른다" 는 뜻인지 구별할 방법이 없다.

**값이 `—` 로 바뀌는 것은 눈에 띄고 색이 안 바뀌는 것은 안 띈다.** 그래서 같은
결함이 다섯 번 반복됐고, 매번 값만 고쳐지고 색은 남았다.

## 규약은 이미 파일에 적혀 있다

`AlphaTerminal.tsx` 는 `fv` 정의 바로 위에 이렇게 적어 두었다:

    // ... 되므로, 색에 쓰면 계산 불가가 초록으로 칠해진다. 색은 chgColor 를 쓴다.

    const fv = (v) => isNA(v) ? 0 : v            ← 계산용
    const chgColor = (v) => isNA(v) || v === 0 ? 회색 : v > 0 ? 초록 : 빨강   ← 색용

`chgColor` 는 "모름" 과 "보합" 을 회색으로 따로 뺀다. **규칙도 있고 올바른
도구도 있는데 호출부 하나가 안 따른다.** 이 파일은 그 규약을 실행 가능한
형태로 만든다.

## 판정 범위 — 좁게 재고 결정했다

TypeScript 를 AST 로 파싱하지 않는다. 줄 단위 정규식으로 충분한지 먼저 쟀다:

    느슨한 판정(색 힌트 + 비교 + null→0)      8건 중 진짜 1~2건 → 소음
    좁힌 판정(색 리터럴 2개 이상인 삼항)      1건 중 진짜 1건 → 채택

`chgColor` 를 쓰는 6곳은 걸리지 않는다 — 올바른 형태가 통과하는 것을 같이
확인했다.

## 맨 필드 비교(`x >= 0 ? 초록 : 빨강`)까지 넓히지 않는 이유 — 재보고 정했다

헬퍼를 안 거치는 쪽이 더 흔하고 더 위험할 수 있다는 지적이 있었다. 그쪽은
null 을 의식조차 안 한 코드일 테니까. 넓힌 판정으로 리포 전체를 재봤다:

    새로 걸리는 것         14건
    그중 진짜 후보         **0건**

전부 둘 중 하나였다. **하나: 가드가 이미 있고 다른 줄에 있다.**

    color={m.vix == null ? '#64748b'                  ← 가드
           : m.vix > 25 ? '#ef4444' : '#10b981'}      ← 걸리는 줄

    {d.cash_flow != null && (                          ← 감싸는 JSX 조건
      ... style={{ color: d.cash_flow > 0 ? ... }}

**둘: 문자열 동등 비교다** (`type === 'ADD'`, `model_tier === 'deep'`).
`null === 'deep'` 은 거짓이라 기본 가지로 갈 뿐, null 이 0 의 색을 입는
형태가 아니다. 위험한 것은 **nullable 값에 대한 크기 비교**다 —
`null >= 0` 이 `true` 라서.

거짓 양성 14건의 원인이 튜닝으로 줄지 않는다: 가드가 인접 줄이나 감싸는
JSX 조건에 있는 것을 줄 단위 검사로는 볼 수 없다. 그걸 따라가려면 TS 파서와
제어 흐름 분석이 필요하고, 지금 얻는 것(진짜 후보 0건)보다 비싸다.

뒤집힌 결론 하나: 이 리포에서 **맨 필드 비교는 전부 가드가 있고, 가드가
없던 것은 `fv()` 를 쓴 쪽이었다.** 헬퍼가 "null 은 처리했다" 는 느낌을 줘서
가드를 안 쓰게 만든 것으로 보인다.

**놓치는 것**: 조건과 색이 다른 줄에 있으면 안 걸린다.

    const pos = (s.val ?? 0) >= 0        ← 여기
    ...
    backgroundColor: pos ? 초록 : 빨강    ← 여기

지금 그런 자리가 하나 있는데, 바로 위에서 `known` 으로 폭을 0 으로 만들어
막대가 보이지 않으므로 실제 피해가 없다. 변수를 따라가려면 AST 가 필요하고,
그 비용은 지금 얻는 것보다 크다. 그 형태가 실제로 화면에 나타나면 이 판단을
다시 본다.
"""
from __future__ import annotations

import pathlib
import re

import pytest

FRONTEND_SRC = pathlib.Path(__file__).resolve().parent.parent.parent / "frontend" / "src"

# null·undefined 를 0 으로 바꾸는 표현. `?? 0.3` 같은 기본값은 제외한다.
_NULL_TO_ZERO = re.compile(r"\bfv\s*\(|\?\?\s*0(?![\d.])|\|\|\s*0(?![\d.])")
# 색 리터럴 — '#rrggbb' 또는 tailwind 의 text-[#...]
_COLOR_LITERAL = re.compile(
    r"'#[0-9a-fA-F]{3,8}'|\"#[0-9a-fA-F]{3,8}\"|(?:text|bg|border)-\[#[0-9a-fA-F]{3,8}\]")
_COMPARISON = re.compile(r"[<>]=?(?!=)|===|!==")


def _flags(line: str) -> bool:
    """이 줄이 '색을 고르는 삼항 + 조건에 null→0' 인가.

    색 리터럴을 **둘 이상** 요구하는 것이 핵심이다. 하나면 정적 색상이 붙은
    평범한 줄이 전부 걸린다 — 실제로 그렇게 재봤더니 8건 중 6건이 정적
    className 이었다.
    """
    if "?" not in line or ":" not in line:
        return False
    if not _NULL_TO_ZERO.search(line):
        return False
    if not _COMPARISON.search(line):
        return False
    return len(_COLOR_LITERAL.findall(line)) >= 2


def _violations() -> list[tuple[str, int, str]]:
    out = []
    root = FRONTEND_SRC.parent.parent
    for path in sorted(FRONTEND_SRC.rglob("*.ts*")):
        rel = path.relative_to(root).as_posix()
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _flags(line):
                out.append((rel, i, line.strip()))
    return out


# ── 검출기 자기검사 ────────────────────────────────────────────────────────────
#
# 정규식 하나가 어긋나면 "위반 0건" 이 되고, 그건 통과와 구별되지 않는다.

@pytest.mark.parametrize("line, expected, why", [
    ("color={fv(m.vix) > 25 ? '#ef4444' : '#10b981'}", True,
     "null→0 이 색을 고르는 조건에 있다"),
    ("style={{ color: (v ?? 0) >= 0 ? '#10b981' : '#ef4444' }}", True,
     "?? 0 도 같다"),
    ("className={cn(x > 0 ? 'text-[#10b981]' : 'text-[#ef4444]')}", False,
     "null→0 이 없으면 정상"),
    ("color={chgColor(m.perf_1w)}", False,
     "올바른 헬퍼는 걸리지 않는다"),
    ("<span className='text-[#10b981]'>{n ?? 0}</span>", False,
     "정적 색 + 개수 표시 — 색을 고르는 자리가 아니다"),
    ("<td className='text-[#cbd5e1]'>{fv(h.qty)}</td>", False,
     "정적 색 하나뿐이면 색을 고르는 것이 아니다"),
    ("style={{ left: `${(q.threshold ?? 0.3) * 100}%` }}", False,
     "?? 0.3 은 null→0 이 아니다"),
    ("const known = s.val != null", False, "가드 자체는 위반이 아니다"),
])
def test_detector_catches_what_it_should(line, expected, why):
    assert _flags(line) is expected, why


# ── 규약이 살아 있는지 ─────────────────────────────────────────────────────────

def test_the_colour_helper_still_separates_unknown_from_flat():
    """`chgColor` 가 '모름'·'보합' 을 색으로 구분하는지.

    이 검사의 전제는 "올바른 도구가 있다" 는 것이다. `chgColor` 가 사라지거나
    `isNA` 분기를 잃으면, 위반을 지적하면서 대안을 못 주는 상태가 된다.
    """
    src = (FRONTEND_SRC / "pages" / "AlphaTerminal.tsx").read_text(encoding="utf-8")

    assert "const chgColor" in src, (
        "chgColor is gone -- there is no longer a colour helper that "
        "distinguishes unknown from zero. (색용 헬퍼가 사라졌다.)"
    )
    body = src.split("const chgColor", 1)[1][:200]
    assert "isNA" in body, (
        f"chgColor no longer checks isNA: {body[:120]!r} -- unknown and flat "
        "collapse to the same colour again. (모름과 보합이 다시 같아졌다.)"
    )


# ── 본 검사 ───────────────────────────────────────────────────────────────────

_KNOWN: dict[tuple[str, str], str] = {}


def test_no_colour_is_chosen_from_a_zeroed_unknown():
    """색을 고르는 조건에 null→0 헬퍼가 있는 곳이, 알려진 목록과 정확히 같아야 한다.

    통화 원장과 같은 형태다 — 새로 생기면 실패하고, 고쳐도 실패한다
    ("목록에서 지워라"). 목록이 조용히 낡지 않는다.
    """
    live = set()
    detail = {}
    for rel, line_no, text in _violations():
        marker = next((m for (f, m) in _KNOWN if f == rel and m in text), None)
        key = (rel, marker) if marker else (rel, text[:40])
        live.add(key)
        detail[key] = f"{rel}:{line_no}  {text[:110]}"

    new = sorted(k for k in live if k not in _KNOWN)
    fixed = sorted(k for k in _KNOWN if k not in live)

    assert not new, (
        "a colour is chosen from a value that was forced to 0 when unknown:\n"
        + "\n".join(f"  {detail[k]}" for k in new)
        + "\nUse a helper that keeps unknown separate (see chgColor), or add "
          "the entry to _KNOWN with what breaks and who owns it. "
          "(계산 불가가 0 의 색을 입는다.)"
    )
    assert not fixed, (
        f"_KNOWN lists {fixed}, which no longer matches any line -- it was "
        "fixed. Delete the entry so the list keeps describing what is still "
        "broken. (고쳐진 항목이 목록에 남아 있다.)"
    )


def test_known_entries_say_why():
    thin = [k for k, v in _KNOWN.items() if len(v.strip()) < 20]
    assert not thin, f"_KNOWN entries without a real reason: {thin}"

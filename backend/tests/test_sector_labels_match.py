"""
섹터 한글 라벨 표가 백엔드와 프론트에서 어긋나면 안 된다.

같은 표가 두 언어로 존재한다 — `markets.SECTOR_LABEL_KO`(리포트 프롬프트가
쓴다)와 `AlphaTerminal.SECTOR_LABEL_KO`(화면이 쓴다). 언어가 다르니 사본
하나는 피할 수 없고, 그래서 **어긋나지 않는 것**만 남는다.

## 이 검사가 무엇을 막는가

**키가 한쪽에만 생기는 것.** 프론트는 `SECTOR_LABEL_KO[r.sector] ?? r.sector`
로 찾는다. 없으면 **영어 내부 키가 그대로 화면에 나온다** — `IT_HARDWARE` 가
섹터 이름 자리에 뜬다. 오류가 아니라 그냥 그렇게 보인다.

**값이 갈리는 것.** 화면과 리포트가 같은 섹터를 다른 이름으로 부른다.
사용자는 둘을 나란히 보는데 같은 것인지 알 수 없다.

**쓰이는 키에 라벨이 없는 것.** 새 ETF 행을 `sector_etfs` 에 넣으면서 라벨을
빠뜨리면 위 두 가지가 동시에 일어난다. 그게 실제 결함 경로다.

## TS 값을 옮겨 적지 않는다

프론트 표를 파이썬으로 **베껴 적고** 비교하면, 베끼면서 잘못 읽은 것은 양쪽에
똑같이 들어가 검사가 볼 수 없다 — "내가 일관되게 읽었는가" 만 검증된다
(CLAUDE.md §6). 그래서 `helpers/extract_ts_const.js` 가 실제 `.tsx` 에서
리터럴을 잘라내 **자바스크립트 엔진에게 평가시키고**, 이 파일은 그 결과를
받는다. 주석·후행 쉼표·따옴표 규칙을 내가 다시 구현하지 않는다.

node 가 없으면 skip 이 아니라 **fail** 이다. skip 은 조용히 사라지고, 이
검사가 사라지면 두 표는 어긋나도 아무 말이 없다. node 는 이미 `npm run build`
에 필요하므로 게이트를 돌릴 수 있는 환경에는 있다.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest

from backend.services import markets

_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
_EXTRACTOR = _ROOT / "backend" / "tests" / "helpers" / "extract_ts_const.js"
_TSX = _ROOT / "frontend" / "src" / "pages" / "AlphaTerminal.tsx"


def _node_const(path: pathlib.Path, name: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.fail(
            "node is not on PATH, so the frontend table cannot be read. "
            "Transcribing it here instead would only check that this file and "
            "that file were read the same way by the same person. node is "
            "already required for `npm run build`. "
            "(전사본 대조는 아무것도 검증하지 않는다.)"
        )
    proc = subprocess.run(
        [node, str(_EXTRACTOR), str(path), name],
        capture_output=True, text=True, encoding="utf-8",
    )
    if proc.returncode != 0:
        pytest.fail(f"could not read {name} from {path.name}: {proc.stderr.strip()}")
    return json.loads(proc.stdout)


# ── 추출기 자기검사 ────────────────────────────────────────────────────────────
#
# 추출이 조용히 일부를 흘리면 비교가 엉뚱한 이유로 실패하거나(원인을 못 찾는다)
# 통째로 비면 비교가 무의미해진다. 답을 아는 소스로 먼저 잰다.

_TRICKY_TS = """
// 앞의 주석
const OTHER = { 'X': '무시' }

const TARGET: Record<string, string> = {
  'A':  '가',          // 줄 끝 주석
  "B":  "나",
  // 통째로 주석인 줄
  'C':  '다 } 라',     // 값 안의 중괄호
  'D':  '마',          // 후행 쉼표
}

const AFTER = { 'Y': '무시' }
"""


def test_the_extractor_reads_a_tricky_literal(tmp_path):
    """주석·따옴표 혼용·값 속 중괄호·후행 쉼표를 다 넘겨야 한다.

    직접 정규식으로 짜면 이 중 하나에서 조용히 틀린다. 그래서 잘라내기만 하고
    평가는 엔진에 맡기는데, **잘라내기가 맞는지**는 여기서 잰다.
    """
    f = tmp_path / "sample.tsx"
    f.write_text(_TRICKY_TS, encoding="utf-8")

    assert _node_const(f, "TARGET") == {"A": "가", "B": "나", "C": "다 } 라", "D": "마"}


def test_the_extractor_does_not_silently_return_nothing(tmp_path):
    """없는 이름을 물으면 조용히 빈 dict 가 아니라 실패해야 한다.

    빈 dict 를 돌려주면 아래 비교가 "프론트 표가 비었다" 로 실패하는데,
    원인이 파일 문제인지 추출 문제인지 구별되지 않는다.
    """
    f = tmp_path / "sample.tsx"
    f.write_text(_TRICKY_TS, encoding="utf-8")

    # `pytest.fail` 은 `BaseException` 이라 `pytest.raises(Exception)` 에 안 걸린다.
    # 추출기를 직접 불러 종료 코드를 본다 — 그게 계약이다.
    proc = subprocess.run(
        [shutil.which("node"), str(_EXTRACTOR), str(f), "NO_SUCH_CONST"],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert proc.returncode != 0, "없는 상수인데 성공으로 끝났다"
    assert not proc.stdout.strip(), f"없는 상수인데 값을 냈다: {proc.stdout!r}"


# ── 본 검사 ───────────────────────────────────────────────────────────────────

def test_the_two_sector_label_tables_are_identical():
    """두 표의 키와 값이 정확히 같아야 한다.

    키가 갈리면 한쪽 화면에 영어 내부 키가 뜨고, 값이 갈리면 화면과 리포트가
    같은 섹터를 다른 이름으로 부른다.
    """
    frontend = _node_const(_TSX, "SECTOR_LABEL_KO")
    backend = markets.SECTOR_LABEL_KO

    only_backend = sorted(set(backend) - set(frontend))
    only_frontend = sorted(set(frontend) - set(backend))
    differing = sorted(k for k in set(backend) & set(frontend)
                       if backend[k] != frontend[k])

    assert not only_backend, (
        f"sector keys the backend labels but the frontend does not: "
        f"{only_backend} -- the screen falls back to the raw key and shows "
        "the English identifier in the sector column. "
        "(프론트 표에 없으면 내부 키가 그대로 보인다.)"
    )
    assert not only_frontend, (
        f"sector keys only the frontend labels: {only_frontend} -- the report "
        "prompt will carry the raw key instead."
    )
    assert not differing, (
        "the same sector has two different Korean names: "
        + ", ".join(f"{k}: backend={backend[k]!r} frontend={frontend[k]!r}"
                    for k in differing)
        + " -- the screen and the report disagree about what to call it."
    )


def test_every_sector_key_in_use_has_a_label():
    """실제로 내보내는 섹터 키에 라벨이 다 있어야 한다.

    이게 실제 결함 경로다 — `sector_etfs` 에 새 ETF 행을 넣으면서 라벨 표
    두 개를 갱신하는 걸 잊는 것. 표끼리만 비교하면 **둘 다 빠뜨린 경우**를
    못 잡는다.
    """
    frontend = _node_const(_TSX, "SECTOR_LABEL_KO")

    from backend.services.market_data import sector_etfs_for

    used: set[str] = set()
    for market in ("US", "KR"):
        used.update(key for key, _etf in sector_etfs_for(market))

    assert used, "섹터 ETF 목록이 비었다 — 이 검사의 전제가 없다"

    missing_backend = sorted(used - set(markets.SECTOR_LABEL_KO))
    missing_frontend = sorted(used - set(frontend))

    assert not missing_backend, (
        f"sector keys with no Korean label in the backend table: "
        f"{missing_backend} -- the report prompt ships the raw key."
    )
    assert not missing_frontend, (
        f"sector keys with no Korean label in the frontend table: "
        f"{missing_frontend} -- the screen shows the raw key."
    )

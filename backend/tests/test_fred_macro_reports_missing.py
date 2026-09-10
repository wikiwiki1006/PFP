"""
FRED 거시지표는 못 읽은 값을 지어내지 않는다 (B4).

이 값은 두 곳으로 간다 — LLM 프롬프트(`ai_analysis.build_macro_block`)와 화면의
거시 탭. 둘 다 사용자가 읽는 숫자다. 그런데 **이 경로에는 테스트가 하나도
없었다.** 없다는 사실 자체가 눈에 띄지 않는다: 통과하는 테스트가 500개 가까이
있는데 그중 이 함수를 부르는 것이 없다는 건 출력 어디에도 안 나온다.

실제로 이 공백에 `NameError` 하나가 숨었다. `_logger` 를 `logger` 로 잘못 쓴
줄이 있었는데 전체 게이트가 초록이었다. 그 줄은 **일부 필드만 없을 때**만
실행되는데, 그 경우를 아무도 만들어 본 적이 없었기 때문이다. 아래
`test_partial_failure_...` 가 그 줄을 지난다.

## 무엇이 잘못돼 있었나

    fed_rate = _last("FEDFUNDS") or 5.33     # or 가 실측 0.0 을 갈아치운다
    cpi = 3.4                                 # 13개월 미만이면 지어냄
    gdp = 0.0                                 # 열이 없으면 '성장률 0%'
    except: return {하드코딩 8개}             # 로그도 없이

`or` 가 가장 위험했다. 제로금리 시기(2008-2015, 2020-2022)에 FEDFUNDS 는 실제로
0 에 가까웠고, 그때를 조회하면 **"기준금리 5.33%" 가 실측값으로** 나갔다.
0 은 거짓이지 없는 값이 아니다.

## 그리고 이것이 프롬프트 쪽 가드를 뚫었다

`build_macro_block` 은 `source == "fallback"` 이면 숫자를 프롬프트에 넣지
않는다. 그런데 **성공 경로에서 필드별로 상수를 섞으면 `source` 는 여전히
`"FRED"`** 다. 가드는 맞게 만들어져 있었고 생산자가 거짓말을 했다.

그래서 이 파일은 소비자가 아니라 **생산자**를 검사한다. 표시를 믿는 소비자가
있는 한, 표시가 정직한지는 생산자 쪽에서 고정돼야 한다.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest import mock

import pandas as pd
import pytest

from backend.services import market_data as md

_SERIES = ["FEDFUNDS", "UNRATE", "DGS10", "DGS2",
           "CPIAUCSL", "A191RL1Q225SBEA", "BAMLH0A0HYM2"]

# 예전에 지어내던 상수들. 어떤 성공 응답에도 이 값이 나오면 안 된다.
_INVENTED = {"fed_rate": 5.33, "cpi": 3.4, "unemployment": 3.7}


def _frame(rows: int = 24, **cols) -> pd.DataFrame:
    """FRED 응답 대역. 주지 않은 열은 아예 없는 열로 둔다."""
    idx = pd.date_range(end=datetime.now(), periods=rows, freq="ME")
    return pd.DataFrame({k: v for k, v in cols.items()}, index=idx)


def _fred(df=None, error: Exception | None = None) -> dict:
    """`get_fred_macro` 를 캐시 없이 한 번 돌린다.

    `pandas_datareader.data` 를 `sys.modules` 로 통째 갈아끼우면 안 된다 —
    import 기계가 깨져서 함수가 `except` 로 빠지고, 그러면 **성공 경로를
    검증했다고 착각**하게 된다. 실제 모듈의 속성만 바꾼다.
    """
    def reader(*a, **kw):
        if error is not None:
            raise error
        return df

    with mock.patch.object(md, "_cached", lambda key, ttl, fn: fn()), \
         mock.patch("pandas_datareader.data.DataReader", reader):
        return md.get_fred_macro()


# ── 대조군 ────────────────────────────────────────────────────────────────────

def test_complete_data_comes_through():
    """값이 다 있으면 그대로 나온다. `missing` 은 비어 있다.

    없으면 아래 검사들은 "언제나 None 을 주는" 구현으로 전부 통과한다.
    지어내지 않는 것이 목적이지 아무것도 안 주는 것이 목적이 아니다.
    """
    out = _fred(_frame(
        FEDFUNDS=[4.25] * 24, UNRATE=[4.1] * 24, DGS10=[4.5] * 24, DGS2=[4.15] * 24,
        CPIAUCSL=[100 + i for i in range(24)],
        A191RL1Q225SBEA=[2.1] * 24, BAMLH0A0HYM2=[3.1] * 24,
    ))

    assert out["source"] == "FRED"
    assert out["missing"] == [], f"값이 다 있는데 missing 이 있다: {out['missing']}"
    assert out["fed_rate"] == 4.25
    assert out["unemployment"] == 4.1
    assert out["t10y2y"] == pytest.approx(0.35)
    assert out["cpi"] is not None and out["gdp"] == 2.1


# ── `or` 회귀 — 0 은 없는 값이 아니다 ───────────────────────────────────────────

@pytest.mark.parametrize("field, column", [
    ("fed_rate", "FEDFUNDS"),
    ("unemployment", "UNRATE"),
    ("y10", "DGS10"),
    ("y2", "DGS2"),
])
def test_a_measured_zero_survives(field, column):
    """실측 0.0 이 폴백 상수로 갈아치워지지 않는다.

    `_last(...) or 5.33` 은 0.0 을 거짓으로 보고 상수를 넣는다. 제로금리
    시기의 FEDFUNDS 가 정확히 그 값이었다.
    """
    cols = {c: [0.0] * 24 for c in ("FEDFUNDS", "UNRATE", "DGS10", "DGS2")}
    cols["CPIAUCSL"] = [100.0] * 24
    cols["A191RL1Q225SBEA"] = [0.0] * 24
    cols["BAMLH0A0HYM2"] = [0.0] * 24

    out = _fred(_frame(**cols))

    assert out[field] == 0.0, (
        f"{field} came back as {out[field]!r} for a measured 0.0 in {column} -- "
        "a zero rate is a fact, not a missing value, and replacing it ships an "
        "invented figure as an observation. (실측 0 이 상수로 바뀌었다.)"
    )
    assert field not in out["missing"], f"{field} 는 있는 값인데 missing 에 들어갔다"


# ── 계산 불가는 None 이다 ──────────────────────────────────────────────────────

def test_cpi_needs_thirteen_months():
    """CPI 는 전년동월비라 13개월이 필요하다. 모자라면 None 이다.

    예전에는 3.4 를 넣었다 — 계산할 수 없는 값을 실측처럼 내보낸 것이다.
    """
    out = _fred(_frame(
        rows=6,
        FEDFUNDS=[4.25] * 6, UNRATE=[4.1] * 6, DGS10=[4.5] * 6, DGS2=[4.15] * 6,
        CPIAUCSL=[100 + i for i in range(6)],
        A191RL1Q225SBEA=[2.1] * 6, BAMLH0A0HYM2=[3.1] * 6,
    ))

    assert out["cpi"] is None, (
        f"cpi={out['cpi']!r} with only 6 months of CPIAUCSL -- year-over-year "
        "needs 13. An uncomputable value is not 3.4. (계산 불가를 상수로 메웠다.)"
    )
    assert "cpi" in out["missing"], "계산 못 한 항목이 missing 에 없다"


def test_absent_columns_become_none_not_zero():
    """열 자체가 없으면 None 이다. 0.0 이 아니다.

    '성장률 0%' 는 '성장이 멈췄다' 는 뜻이지 '모른다' 가 아니다 (§1.3).
    """
    out = _fred(_frame(FEDFUNDS=[4.25] * 24, UNRATE=[4.1] * 24))

    for field in ("gdp", "y10", "y2", "t10y2y", "bamlh0a0hym2", "cpi"):
        assert out[field] is None, (
            f"{field}={out[field]!r} although its column was absent -- 0.0 reads "
            "as a measurement of zero. (없는 값을 0 으로 적었다.)"
        )
        assert field in out["missing"], f"{field} 가 missing 에 없다"


# ── 부분 실패가 실측으로 위장되지 않는다 (가드를 뚫었던 형태) ────────────────────

def test_partial_failure_is_listed_not_disguised():
    """일부만 못 읽어도 `source` 는 "FRED" 다 — 그래서 `missing` 이 있어야 한다.

    소비자(`build_macro_block`)는 `source` 만 보고 실측 여부를 판단했다.
    성공 경로에 상수를 섞으면 그 판단이 조용히 무너진다. 가드는 맞았고
    생산자가 거짓말을 했다.

    이 테스트는 **일부 필드만 없을 때만 실행되는 경고 줄**도 지난다. 그 줄에
    `NameError` 가 있었는데 전체 게이트가 초록이었다 — 이 경우를 만들어 본
    테스트가 없었기 때문이다.
    """
    out = _fred(_frame(
        FEDFUNDS=[4.25] * 24, UNRATE=[4.1] * 24, DGS10=[4.5] * 24, DGS2=[4.15] * 24,
        CPIAUCSL=[100 + i for i in range(24)],
        # A191RL1Q225SBEA(GDP)·BAMLH0A0HYM2(HY) 는 없다
    ))

    assert out["source"] == "FRED", "부분 실패는 조회 자체의 실패가 아니다"
    assert set(out["missing"]) == {"gdp", "bamlh0a0hym2"}, (
        f"missing={out['missing']} -- a partial failure must name what is "
        "absent, because consumers decide by `source` alone and it still says "
        "FRED. (부분 실패가 실측으로 위장된다.)"
    )
    for field, invented in _INVENTED.items():
        assert out.get(field) != invented or field not in out["missing"], (
            f"{field} equals the old hardcoded constant {invented}"
        )


def test_lookup_failure_returns_nothing_and_says_so():
    """조회 자체가 실패하면 전 필드 None, `source` 는 "fallback" 이다."""
    out = _fred(error=RuntimeError("FRED down"))

    keys = ["fed_rate", "unemployment", "cpi", "gdp",
            "y10", "y2", "t10y2y", "bamlh0a0hym2"]
    assert out["source"] == "fallback"
    assert set(out["missing"]) == set(keys), f"missing={out['missing']}"
    for k in keys:
        assert out[k] is None, (
            f"{k}={out[k]!r} after a failed lookup -- a plausible fake number is "
            "worse than a blank, because the screen draws it as normal. "
            "(조회 실패인데 숫자가 나왔다.)"
        )


# ── 소비자 쪽 — None 인 행은 프롬프트에 안 들어간다 ─────────────────────────────

def test_build_macro_block_omits_rows_it_could_not_read():
    """`None` 인 지표는 프롬프트에서 빠진다. 'N/A' 로 적지 않는다.

    모델은 'N/A' 를 수치처럼 인용한다. 이건 이미 동작하는데 고정돼 있지
    않았다 — 여기서 잡아 둔다.
    """
    from backend.services import ai_analysis

    with mock.patch("backend.services.market_data.get_fred_macro", return_value={
        "source": "FRED", "fed_rate": 4.25, "unemployment": None, "cpi": None,
        "gdp": None, "t10y2y": 0.35, "bamlh0a0hym2": None,
        "missing": ["unemployment", "cpi", "gdp", "bamlh0a0hym2"],
    }):
        block = ai_analysis.build_macro_block("US")

    assert "Fed funds" in block and "4.25" in block, f"읽은 값이 빠졌다: {block!r}"
    assert "N/A" not in block and "None" not in block, (
        f"a placeholder reached the prompt: {block!r} -- the model quotes it as "
        "a figure. (없는 값을 문자열로 넣었다.)"
    )
    for absent in ("Unemployment", "CPI YoY", "GDP growth", "HY spread"):
        assert absent not in block, (
            f"'{absent}' is in the prompt although its value was None: {block!r}"
        )

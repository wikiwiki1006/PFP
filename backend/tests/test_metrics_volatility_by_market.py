"""
포트폴리오 지표의 `vix` 는 **그 시장의** 변동성 지수다 (ba60ceb).

라우터가 시장과 무관하게 프레임에 `^VIX` 를 넣기 때문에, 한국 포트폴리오의
`vix` 가 미국 VIX 였다. 2026-09-17 에 둘이 VIX 15.66 · VKOSPI 43.02 로 갈려 한국
위험을 3분의 1로 보여 줬고, 그 값은 한국 AI 피드백 프롬프트에 "VKOSPI" 라는
이름으로 실린다.

    한국  market_data.volatility_index("KR") 의 value. 못 읽으면 None —
          프레임에 ^VIX 가 멀쩡히 있어도 그걸로 메우지 않는다 (§1.3).
    미국  프레임의 ^VIX 그대로. volatility_index 를 부르지 않는다.

`volatility_index` 는 **대역**으로 둔다. 진짜를 부르면 인베스팅으로 나가다 네트워크
가드에 걸려 테스트가 실패한다 — 그 자체가 "이 검사가 대역 없이 돌면 안 된다"
는 신호다. 한국 경로는 거래일 판정(`is_kr_trading_day`)도 타므로 관측 캘린더도
주입한다.
"""
from __future__ import annotations

import logging
from datetime import timedelta

import pandas as pd
import pytest

from backend.services import market_calendar, market_data
from backend.services import portfolio_calculator as pc

_IDX = pd.bdate_range("2026-06-01", periods=60)
_FRAME_VIX = 16.46
_KR = {"005930.KS": {"q": 10, "avg": 70000, "sector": "Tech"}}
_US = {"AAPL": {"q": 10, "avg": 150.0, "sector": "Tech"}}


def _frame(stock: str, stock_px: float, bench: str) -> pd.DataFrame:
    n = len(_IDX)
    return pd.DataFrame({
        stock: [stock_px * (1 + 0.001 * i) for i in range(n)],
        bench: [100.0 * (1 + 0.0008 * i) for i in range(n)],
        # 라우터가 시장과 무관하게 넣는 열 — 한국 프레임에도 있다.
        "^VIX": [20.0] * (n - 1) + [_FRAME_VIX],
    }, index=_IDX)


def _metrics(holdings: dict, frame: pd.DataFrame, market: str) -> dict:
    curve = pc.build_equity_curve(holdings, [], frame, market=market)
    return pc.calculate_metrics(holdings, frame, curve, market=market)


@pytest.fixture(autouse=True)
def krx_calendar(monkeypatch):
    start, end = _IDX[0].date() - timedelta(days=30), _IDX[-1].date() + timedelta(days=30)
    days = frozenset(start + timedelta(days=i) for i in range((end - start).days + 1)
                     if (start + timedelta(days=i)).weekday() < 5)
    monkeypatch.setattr(market_calendar, "_krx_trading_days", lambda: days)


@pytest.fixture
def vol_index(monkeypatch):
    """`market_data.volatility_index` 대역. `.answer` 로 돌려줄 것(dict 또는 예외)을 정한다."""
    class Double:
        calls: list[str] = []
        answer: object = None

    def fake(market):
        Double.calls.append(market)
        if isinstance(Double.answer, BaseException):
            raise Double.answer
        return Double.answer

    Double.calls = []
    monkeypatch.setattr(market_data, "volatility_index", fake)
    return Double


def _answer(value):
    return {"value": value, "prev_close": None, "change_pct": None, "as_of": None,
            "label": "VKOSPI", "source": "investing"}


def test_korea_reports_its_own_volatility_index(vol_index):
    vol_index.answer = _answer(43.02)

    m = _metrics(_KR, _frame("005930.KS", 70000.0, "^KS11"), "KR")

    assert m["vix"] == 43.02, f"KR vix is {m['vix']} -- expected VKOSPI 43.02 (frame ^VIX is {_FRAME_VIX})"
    assert vol_index.calls == ["KR"]


@pytest.mark.parametrize("answer", [_answer(None), RuntimeError("volatility lookup broke")],
                         ids=["값-없음", "예외"])
def test_korea_leaves_vix_empty_rather_than_borrowing_the_us_index(vol_index, caplog, answer):
    frame = _frame("005930.KS", 70000.0, "^KS11")
    assert frame["^VIX"].iloc[-1] == _FRAME_VIX, "precondition: a usable US VIX sits in the frame"
    vol_index.answer = answer
    caplog.set_level(logging.WARNING, logger=pc.logger.name)

    m = _metrics(_KR, frame, "KR")

    assert m["vix"] is None, (
        f"KR vix is {m['vix']} although VKOSPI could not be read -- a US measurement "
        "under the Korean name is worse than an empty cell. (미국 VIX 로 메웠다)"
    )
    if isinstance(answer, BaseException):
        assert any("VKOSPI" in r.getMessage() for r in caplog.records
                   if r.levelno >= logging.WARNING), "the exception left no trace"


def test_the_us_reads_the_frame_and_never_asks_for_the_index(vol_index):
    vol_index.answer = _answer(43.02)

    m = _metrics(_US, _frame("AAPL", 150.0, "^GSPC"), "US")

    assert m["vix"] == _FRAME_VIX
    assert vol_index.calls == [], (
        "the US path called volatility_index -- the frame already carries a live ^VIX "
        "and the extra lookup is a network call on every /metrics"
    )

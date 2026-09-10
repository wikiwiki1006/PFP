"""거래 마커가 차트에서 사라지지 않아야 한다.

차트에는 거래일만 남는다 — 주말은 제거되고 자산이 없던 앞구간도 잘린다.
거래를 날짜 완전일치로만 붙이면 그런 날에 기록한 거래가 조용히 없어진다.
"""
from backend.services.portfolio_calculator import _trades_by_chart_date

# 2026-01-05(월) ~ 01-09(금) 한 주. 주말은 차트에 없다.
WEEK = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09"]


def _tr(date, ticker="AAPL", ttype="BUY"):
    return {"date": date, "ticker": ticker, "type": ttype, "q": 1, "price": 100}


def test_exact_date_keeps_position():
    got = _trades_by_chart_date([_tr("2026-01-07")], WEEK)
    assert list(got) == ["2026-01-07"]


def test_weekend_trade_is_not_lost():
    """토요일 거래는 다음 거래일로 옮겨 붙는다 — 사라지면 안 된다."""
    got = _trades_by_chart_date([_tr("2026-01-10")], WEEK)   # 토요일
    assert sum(len(v) for v in got.values()) == 1


def test_trade_before_chart_start_is_not_lost():
    """차트 시작보다 앞선 거래도 첫 점에 붙는다."""
    got = _trades_by_chart_date([_tr("2025-12-20")], WEEK)
    assert got.get("2026-01-05") and len(got["2026-01-05"]) == 1


def test_trade_after_chart_end_lands_on_last_point():
    got = _trades_by_chart_date([_tr("2026-03-01")], WEEK)
    assert got.get("2026-01-09") and len(got["2026-01-09"]) == 1


def test_no_trade_is_dropped():
    """주말·공휴일·범위 밖이 섞여도 건수는 보존된다."""
    trades = [
        _tr("2026-01-05"), _tr("2026-01-10"), _tr("2026-01-11"),
        _tr("2025-11-01"), _tr("2026-01-08"), _tr("2027-01-01"),
    ]
    got = _trades_by_chart_date(trades, WEEK)
    assert sum(len(v) for v in got.values()) == len(trades)
    assert set(got) <= set(WEEK)


def test_empty_chart_returns_empty():
    assert _trades_by_chart_date([_tr("2026-01-05")], []) == {}

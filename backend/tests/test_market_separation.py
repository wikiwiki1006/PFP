"""
미국·한국 시장이 서로 섞이지 않는지 검증한다.

사용자 요구는 "완전히 분리"였다. 분리는 세 층에서 각각 깨질 수 있어
층마다 따로 확인한다.

  ① 티커 판별  — 어떤 시장 종목인지 접미사로 가른다
  ② 저장 쿼리  — holdings 조회·저장에 market 조건이 실제로 들어가는가
  ③ 거래일 계산 — 시장별 거래일 수가 달라 상수 기준을 쓰면 한쪽이 망가진다
"""
from datetime import date

import pytest

from backend.services import markets as M


# ── ① 티커 → 시장 판별 ──────────────────────────────────────────────────────

@pytest.mark.parametrize("ticker,expected", [
    ("005930.KS", "KR"),   # 삼성전자
    ("196170.KQ", "KR"),   # 알테오젠 (코스닥)
    ("^KS11",     "KR"),   # 코스피 지수 — 접미사가 없어 지수 목록으로 판별한다
    ("AAPL",      "US"),
    ("BRK-B",     "US"),
    ("",          "US"),   # 빈 값은 기본 시장
])
def test_market_of_ticker(ticker, expected):
    assert M.market_of_ticker(ticker) == expected


def test_belongs_to_rejects_cross_market():
    """한국 화면에서 미국 종목을 등록하면 안 된다 (그 반대도)."""
    assert M.belongs_to("005930.KS", "KR")
    assert not M.belongs_to("005930.KS", "US")
    assert M.belongs_to("AAPL", "US")
    assert not M.belongs_to("AAPL", "KR")


def test_market_param_normalizes_unknown_values():
    """모르는 값이 와도 예외 대신 기본 시장이어야 한다 —
    시장을 안 보내는 옛 프론트가 기존대로 동작해야 하기 때문이다."""
    assert M.market_param("kr") == "KR"
    assert M.market_param("KR") == "KR"
    assert M.market_param("JP") == "US"
    assert M.market_param("")   == "US"


def test_two_markets_do_not_share_sector_etfs():
    """섹터 등락률이 섞이지 않으려면 대표 ETF 가 겹치면 안 된다."""
    us = {t for _, t in M.US.sector_etfs}
    kr = {t for _, t in M.KR.sector_etfs}
    assert not (us & kr)


# ── ② holdings 쿼리에 market 이 들어가는가 ──────────────────────────────────

class _Cursor:
    def __init__(self, sink):
        self.sink = sink
        self.rows = []

    def execute(self, sql, params=None):
        self.sink.setdefault("calls", []).append(
            (" ".join(sql.split()), list(params or []))
        )

    def fetchall(self):
        return self.rows

    def __enter__(self):  return self
    def __exit__(self, *a):  return False


class _Conn:
    def __init__(self, sink):  self.sink = sink
    def cursor(self):  return _Cursor(self.sink)
    def __enter__(self):  return self
    def __exit__(self, *a):  return False


def test_get_holdings_filters_by_market(monkeypatch):
    import backend.db.portfolio_repo as repo

    sink: dict = {}
    monkeypatch.setattr(repo, "is_available", lambda: True)
    monkeypatch.setattr(repo, "get_conn", lambda: _Conn(sink))

    repo.get_holdings(user_id="u1", market="KR")

    sql, params = sink["calls"][0]
    assert "market" in sql.lower(), "조회 SQL 에 market 조건이 없다"
    assert "KR" in params, f"KR 이 파라미터로 전달되지 않았다: {params}"


def test_save_holding_writes_market(monkeypatch):
    import backend.db.portfolio_repo as repo

    sink: dict = {}
    monkeypatch.setattr(repo, "is_available", lambda: True)
    monkeypatch.setattr(repo, "get_conn", lambda: _Conn(sink))

    repo.save_holding("005930.KS", 5, 70000.0, user_id="u1", market="KR")

    sql, params = sink["calls"][0]
    assert "market" in sql.lower()
    assert "KR" in params


# ── ③ 시장별 거래일 수 ──────────────────────────────────────────────────────

def test_expected_sessions_differ_between_markets():
    """한국은 설·추석 등으로 거래일이 미국보다 적다.

    이 차이 때문에 '150행 이상' 같은 상수 기준을 쓰면 한국 종목이 영원히
    미달로 남아, 수집기가 같은 종목을 매번 다시 받고 신호 스캔은 한 번도
    갱신되지 않는다. 실제로 그 버그가 있었다.
    """
    from backend.db.market_cache import _expected_sessions

    since, until = date(2026, 1, 1), date(2026, 8, 31)
    us = _expected_sessions(since, until, "US")
    kr = _expected_sessions(since, until, "KR")
    other = _expected_sessions(since, until, "OTHER")

    assert 150 < us < 180
    assert 140 < kr < 180
    assert kr < us, "한국 거래일이 미국보다 적어야 한다"
    assert other == (until - since).days + 1, "24시간 자산은 매일 거래한다"


def test_korean_preferred_shares_excluded():
    """우선주·ETN 은 스캔 유니버스에서 뺀다. 보통주와 거의 같이 움직여
    같은 종목이 두 번 잡히고, 거래가 얇아 기술적 신호도 흐려진다."""
    from backend.services.korea_universe import _is_common_stock

    assert _is_common_stock("005930")       # 삼성전자
    assert not _is_common_stock("005935")   # 삼성전자우
    assert not _is_common_stock("005387")   # 현대차2우B
    assert not _is_common_stock("610063")   # ETN

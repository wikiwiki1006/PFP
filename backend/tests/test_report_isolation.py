"""
'과거 레포트' 목록에 남의 리포트가 섞이지 않는지 검증한다.

예전에는 list_reports 가 `WHERE (user_id=%s OR scope='shared')` 로 조회했다.
종목·산업 리서치는 scope='shared' 로 저장되므로, 다른 사용자가 만든 리포트가
모두의 과거 목록에 나타났다. 목록은 "내가 무엇을 분석했는가"의 기록인데
남의 활동이 그대로 보이는 셈이었다.

공용 리포트의 재사용 자체는 유지된다 — 다만 목록이 아니라 **조회 시점**에,
find_fresh_shared_report() 를 통해서만 일어난다.
"""
from datetime import datetime

import backend.db.reports_repo as repo


class _Cursor:
    """실행된 SQL 과 파라미터를 붙잡아 두는 커서."""

    def __init__(self, sink, rows):
        self.sink, self.rows = sink, rows

    def execute(self, sql, params=None):
        self.sink["sql"] = " ".join(sql.split())
        self.sink["params"] = list(params or [])

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Conn:
    def __init__(self, sink, rows):
        self.sink, self.rows = sink, rows

    def cursor(self):
        return _Cursor(self.sink, self.rows)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _patch(monkeypatch, rows):
    sink: dict = {}
    monkeypatch.setattr(repo, "is_available", lambda: True)
    monkeypatch.setattr(repo, "get_conn", lambda: _Conn(sink, rows))
    return sink


def test_list_reports_queries_only_own(monkeypatch):
    sink = _patch(monkeypatch, [])
    repo.list_reports("alice")

    assert "scope='shared'" not in sink["sql"], (
        "목록 조회가 공용 리포트를 끌어온다 — 남의 리포트가 과거 목록에 보인다"
    )
    assert "WHERE user_id=%s" in sink["sql"]
    assert sink["params"][0] == "alice"


def test_own_shared_report_still_listed(monkeypatch):
    """내가 만든 리포트는 공용으로 저장됐어도 내 목록에 남아야 한다."""
    rows = [("lens_AAPL.md", "equity_research", {}, datetime(2026, 8, 21), "shared", "alice")]
    _patch(monkeypatch, rows)

    out = repo.list_reports("alice")
    assert len(out) == 1
    assert out[0]["mine"] is True
    assert out[0]["shared"] is True


def test_shared_lookup_is_the_only_cross_user_path(monkeypatch):
    """종목 검색 시 쓰이는 경로는 그대로 공용 리포트를 찾아야 한다."""
    import inspect

    src = inspect.getsource(repo.find_fresh_shared_report)
    assert "scope='shared'" in src, (
        "공용 리포트 재사용 경로까지 막으면 같은 종목을 매번 새로 생성하게 된다"
    )

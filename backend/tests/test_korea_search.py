"""
한국 종목 검색은 **화면에 보이는 이름으로** 찾는다 — `korea_universe.search_listed` (9244fc7).

이름이 두 벌이다. KRX 상장목록은 정식명('케이티' · '현대자동차' · '한국전력공사'),
화면이 쓰는 이름 사전은 통칭('KT' · '현대차' · '한국전력'). 한국 화면이 종목을 이름으로만
보여 주게 되자 사람은 보이는 이름을 치는데, 검색은 정식명만 봤다:

    KT        → KTcs · KTis · KT지니뮤직 … (KT 가 5위 밖)
    현대차     → 현대차증권만
    KT&G · KODEX 200 → 결과 없음 (사전에만 있다)
    한국전력    → 같은 종목 두 줄 (상장목록 중복 행)

그리고 목록 첫 줄이 Enter 의 답(`pickOnEnter`)이라, 순서가 틀리면 엉뚱한 종목이 골라진다.

## 대역

상장목록 · 이름 사전 · 시총순 유니버스를 대역으로 둔다. 이름은 실제 KRX 정식명과
네이버 통칭을 그대로 썼다(통합 실측의 사례). 구체 사례에 더해, 대역 안의 **모든
이름과 코드**를 검색어로 훑는 성질 검사를 둔다 — 사례를 아무리 골라도 "완전일치가
첫 줄" 을 전부 적을 수는 없다.
"""
from __future__ import annotations

import pytest

from backend.services import korea_universe as ku

_LISTED = [                        # KRX 정식명 — 한국전력공사는 실제로 두 행이다
    ("030200.KS", "케이티"), ("058850.KS", "KTcs"), ("058860.KS", "KTis"),
    ("043610.KQ", "KT지니뮤직"), ("033780.KS", "케이티앤지"),
    ("005380.KS", "현대자동차"), ("001500.KS", "현대차증권"),
    ("003550.KS", "LG"), ("373220.KS", "LG에너지솔루션"), ("051910.KS", "LG화학"),
    ("066570.KS", "LG전자"),
    ("034730.KS", "SK"), ("000660.KS", "SK하이닉스"), ("402340.KS", "SK스퀘어"),
    ("015760.KS", "한국전력공사"), ("015760.KS", "한국전력공사"),
    ("005930.KS", "삼성전자"), ("005935.KS", "삼성전자우"), ("028260.KS", "삼성물산"),
    ("000810.KS", "삼성화재"), ("001360.KS", "삼성제약"), ("012750.KS", "에스원"),
    ("035720.KS", "카카오"), ("323410.KS", "카카오뱅크"), ("377300.KS", "카카오페이"),
]
_NAMES = {                          # 화면이 쓰는 이름 사전 — ETF 는 여기에만 있다
    "030200.KS": "KT", "033780.KS": "KT&G", "005380.KS": "현대차", "015760.KS": "한국전력",
    "005930.KS": "삼성전자", "000660.KS": "SK하이닉스", "003550.KS": "LG", "034730.KS": "SK",
    "373220.KS": "LG에너지솔루션", "035720.KS": "카카오",
    "069500.KS": "KODEX 200", "278530.KS": "KODEX 200TR",
}
_UNIVERSE = [                       # 시총 내림차순
    "005930.KS", "000660.KS", "373220.KS", "005380.KS", "035720.KS", "051910.KS",
    "034730.KS", "015760.KS", "033780.KS", "030200.KS", "003550.KS", "066570.KS",
    "028260.KS", "402340.KS", "323410.KS", "377300.KS", "000810.KS", "069500.KS",
]


@pytest.fixture(autouse=True)
def data(monkeypatch):
    monkeypatch.setattr(ku, "get_listed_all", lambda refresh=False: [
        {"ticker": t, "name": n, "market": "KOSPI"} for t, n in _LISTED])
    monkeypatch.setattr(ku, "name_map", lambda: dict(_NAMES))
    monkeypatch.setattr(ku, "get_scan_universe", lambda: list(_UNIVERSE))


def _rows(q, limit=5):
    return [(r["ticker"], r["name"]) for r in ku.search_listed(q, limit)]


# ── 통합이 잰 사례 ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("query, first", [
    ("KT", ("030200.KS", "KT")),
    ("현대차", ("005380.KS", "현대차")),
    ("현대자동차", ("005380.KS", "현대차")),
    ("LG", ("003550.KS", "LG")),
    ("SK", ("034730.KS", "SK")),
    ("KT&G", ("033780.KS", "KT&G")),
    ("KODEX 200", ("069500.KS", "KODEX 200")),
    ("kt", ("030200.KS", "KT")),
], ids=["KT", "현대차", "현대자동차-정식명", "LG", "SK", "KT&G-사전에만-통칭", "KODEX200-사전에만", "소문자"])
def test_the_name_on_screen_finds_that_stock_first(query, first):
    rows = _rows(query)
    assert rows and rows[0] == first, (
        f"'{query}' → {rows} -- the first row is what Enter picks, and it is not {first}. "
        "(화면에 보이는 이름으로 친 종목이 첫 줄이 아니다)"
    )


def test_the_securities_firm_follows_the_carmaker():
    assert _rows("현대차")[:2] == [("005380.KS", "현대차"), ("001500.KS", "현대차증권")]


def test_a_stock_listed_twice_appears_once():
    rows = _rows("한국전력")
    assert rows == [("015760.KS", "한국전력")], rows


# ── 대조군 — 고치기 전에도 맞던 순서 ─────────────────────────────────────────

def test_control_samsung_bigger_companies_first():
    rows = _rows("삼성", limit=10)
    tickers = [t for t, _ in rows]
    assert tickers[0] == "005930.KS", rows
    ranked = [t for t in tickers if t in _UNIVERSE]
    unranked = [t for t in tickers if t not in _UNIVERSE]
    assert tickers == ranked + unranked, f"an unranked company sits above a ranked one: {rows}"
    assert "012750.KS" not in tickers, "에스원 does not contain the query in any of its names"


def test_control_code_and_kakao():
    assert _rows("005930")[0] == ("005930.KS", "삼성전자")
    assert [t for t, _ in _rows("카카오")] == ["035720.KS", "323410.KS", "377300.KS"]


# ── 성질 — 대역 안의 모든 이름과 코드로 ───────────────────────────────────────

def _all_queries():
    names = {n for _, n in _LISTED} | set(_NAMES.values())
    codes = {t.split(".")[0] for t, _ in _LISTED} | {t.split(".")[0] for t in _NAMES}
    return sorted(names | codes)


def _exact_candidates(query):
    q = query.strip().upper()
    out = set()
    for t, n in _LISTED + list(_NAMES.items()):
        if t.split(".")[0] == query.strip() or n.upper() == q:
            out.add(t)
    return out


@pytest.mark.parametrize("query", _all_queries())
def test_every_name_and_code_finds_its_stock_first_without_duplicates(query):
    rows = ku.search_listed(query, limit=5)
    tickers = [r["ticker"] for r in rows]

    assert len(tickers) == len(set(tickers)), f"'{query}' returned a ticker twice: {tickers}"
    assert len(rows) <= 5
    exact = _exact_candidates(query)
    assert exact, "test setup: every query comes from the fixture"
    assert tickers and tickers[0] in exact, (
        f"'{query}' exactly names {sorted(exact)} but the first row is {rows[:1]}"
    )
    for r in rows:
        if r["ticker"] in _NAMES:
            assert r["name"] == _NAMES[r["ticker"]], (
                f"{r['ticker']} is shown as {r['name']!r}, not its on-screen name {_NAMES[r['ticker']]!r}"
            )


def test_an_empty_query_finds_nothing():
    assert ku.search_listed("   ") == []

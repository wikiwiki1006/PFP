"""
`market_cache` 의 실패 경로 — 분기 29개 중 27개가 미실행이었다.

이 모듈은 두 가지를 혼자 책임진다. **`market_prices` 의 유일한 SQL 기록
지점**이고(§1.6), 무엇을 다시 받아올지 정하는 **stale 판정**이다. 전자가
틀리면 위조 종가가 영구 저장되고, 후자가 틀리면 yfinance 를 전량 다시 때린다.

## 이 검사들이 무엇을 막는가

**전량 stale 로 떨어지는 것.** `get_stale_tickers` 는 DB 를 못 읽으면
`list(tickers)` 를 돌려준다 — "전부 오래됐다" 는 뜻이라 호출자가 전 종목을
다시 받는다. DB 가 잠깐 흔들린 것이 **yfinance 전량 재수집**이 되고, 레이트
리밋은 IP 단위라 창 다섯이 같이 걸린다 (§7.7). 이 폴백 자체는 맞는 선택이다
(모르면 다시 받는 쪽이 안전하다) — 다만 **그 선택이 무엇을 부르는지**가
고정돼 있지 않았다.

**yfinance 캐시 손상이 영구화되는 것.** 프로세스를 강제 종료하면 sqlite
파일은 남는데 테이블이 비는 상태가 된다. yfinance 는 그걸 감지하지 않고
`no such table: _tz_kv` 를 던지며, 파일이 있으니 다시 만들지도 않는다 — 한 번
이 상태가 되면 재시작해도 영구히 재발한다(실제로 겪었다).
"""
from __future__ import annotations

import logging
import sqlite3
from unittest import mock

import pytest

from backend.db import market_cache as mc


# ── stale 판정: 못 읽으면 '전부 오래됨' 이다 ───────────────────────────────────

def test_everything_is_stale_when_the_database_is_unavailable():
    """DB 가 없으면 전 종목을 stale 로 본다.

    모르면 다시 받는 쪽이 안전하다 — 낡은 값을 신선하다고 우기는 것보다 낫다.
    다만 그 선택은 **전량 재수집**을 부르므로, 무엇이 돌아오는지 고정해 둔다.
    """
    with mock.patch.object(mc, "is_available", return_value=False):
        assert mc.get_stale_tickers(["AAPL", "MSFT"]) == ["AAPL", "MSFT"]


def test_a_failed_stale_query_falls_back_to_everything_and_logs(caplog):
    """조회가 터져도 같은 선택을 하되 **로그를 남긴다.**

    로그가 없으면 "왜 갑자기 전 종목을 다시 받았는가" 를 나중에 알 수 없다.
    그 재수집이 레이트리밋을 부르면, 원인은 DB 인데 증상은 yfinance 에서 난다.
    """
    with mock.patch.object(mc, "is_available", return_value=True), \
         mock.patch.object(mc, "get_conn", side_effect=RuntimeError("db gone")), \
         caplog.at_level(logging.WARNING):
        out = mc.get_stale_tickers(["AAPL", "MSFT"])

    assert out == ["AAPL", "MSFT"]
    assert caplog.records, (
        "a failed staleness query silently refetched everything -- the cause "
        "is the database but the symptom appears at yfinance, and nothing "
        "connects them. (원인과 증상이 다른 곳에서 난다.)"
    )


def test_volume_staleness_falls_back_the_same_way():
    """거래량 쪽도 같다 — 한쪽만 보수적이면 두 경로가 어긋난다."""
    with mock.patch.object(mc, "is_available", return_value=False):
        assert mc.get_volume_stale_tickers(["AAPL"]) == ["AAPL"]


def test_an_empty_request_asks_for_nothing():
    """대조군 — 빈 요청에는 빈 결과다.

    없으면 위 검사들은 "언제나 전부 stale" 이라는 구현으로도 통과한다. 그건
    보수적인 게 아니라 캐시가 죽은 것이다.
    """
    with mock.patch.object(mc, "is_available", return_value=False):
        assert mc.get_stale_tickers([]) == []
        assert mc.get_volume_stale_tickers([]) == []


# ── 공용 캐시: DB 가 없으면 조용한 no-op ───────────────────────────────────────

def test_shared_cache_is_a_no_op_without_a_database():
    """DB 가 없으면 읽기는 `None`, 쓰기는 아무 일도 하지 않는다.

    예외를 던지면 캐시를 쓰는 모든 경로가 같이 죽는다. 캐시는 없어도 되는
    것이고, 없을 때 조용한 것이 맞다.
    """
    with mock.patch.object(mc, "is_available", return_value=False), \
         mock.patch.object(mc, "get_conn",
                           side_effect=AssertionError("DB 가 없는데 커넥션을 잡았다")):
        assert mc.get_common("anything") is None
        mc.save_common("anything", {"a": 1})          # 예외가 나면 실패한다


def test_snapshot_readers_are_no_ops_without_a_database():
    """스냅샷도 같다 — 빈 dict 와 '신선하지 않음' 이다."""
    with mock.patch.object(mc, "is_available", return_value=False), \
         mock.patch.object(mc, "get_conn",
                           side_effect=AssertionError("DB 가 없는데 커넥션을 잡았다")):
        assert mc.get_snapshot() == {}
        assert mc.is_snapshot_fresh() is False
        mc.save_snapshot({"AAPL": {"price": 1.0}})


def test_price_reads_are_no_ops_without_a_database():
    """가격·거래량 조회도 DB 없이 예외를 내지 않는다."""
    with mock.patch.object(mc, "is_available", return_value=False), \
         mock.patch.object(mc, "get_conn",
                           side_effect=AssertionError("DB 가 없는데 커넥션을 잡았다")):
        assert mc.get_prices_from_db(["AAPL"], "1mo") is None
        assert mc.get_volume_from_db(["AAPL"], "1mo") is None


# ── yfinance 캐시 손상: 한 번 나면 재시작해도 영구히 재발한다 ──────────────────

def _make_sqlite(path, with_table: bool):
    conn = sqlite3.connect(str(path))
    try:
        if with_table:
            conn.execute("CREATE TABLE _tz_kv (k TEXT, v TEXT)")
            conn.commit()
    finally:
        conn.close()


def test_a_healthy_cache_file_is_recognised(tmp_path):
    """대조군 — 테이블이 있는 파일은 정상으로 본다.

    없으면 아래 검사는 "무엇이든 손상으로 본다" 는 구현으로 통과하는데, 그건
    매 기동마다 멀쩡한 캐시를 지우는 것이다.
    """
    db = tmp_path / "tkr-tz.db"
    _make_sqlite(db, with_table=True)

    assert mc._yf_cache_db_has_tables(str(db)) is True


@pytest.mark.parametrize("setup, why", [
    (lambda p: _make_sqlite(p, with_table=False), "테이블이 하나도 없다"),
    (lambda p: p.write_bytes(b"not a database at all"), "sqlite 파일이 아니다"),
    (lambda p: p.write_bytes(b""), "빈 파일"),
])
def test_a_damaged_cache_file_is_detected(tmp_path, setup, why):
    """열 수 없거나 테이블이 없으면 손상으로 본다.

    강제 종료로 WAL 체크포인트가 유실되면 파일은 남고 테이블만 빈다. yfinance
    는 그걸 감지하지 않고, 파일이 있으니 다시 만들지도 않는다.
    """
    db = tmp_path / "tkr-tz.db"
    setup(db)

    assert mc._yf_cache_db_has_tables(str(db)) is False, why


def test_repair_removes_only_the_damaged_files(tmp_path, caplog):
    """손상된 것만 지우고 멀쩡한 것은 남긴다. 그리고 **지웠다고 로그를 남긴다.**

    조용히 지우면 "왜 타임존 캐시가 매번 비어 있는가" 를 추적할 수 없다.
    """
    good = tmp_path / "cookies.db"
    bad = tmp_path / "tkr-tz.db"
    _make_sqlite(good, with_table=True)
    _make_sqlite(bad, with_table=False)
    (tmp_path / "tkr-tz.db-wal").write_bytes(b"stale wal")

    with caplog.at_level(logging.WARNING):
        mc._repair_yf_cache_dir(str(tmp_path))

    assert not bad.exists(), "손상된 파일이 남았다 — 재시작해도 같은 오류가 난다"
    assert not (tmp_path / "tkr-tz.db-wal").exists(), "-wal 잔여물이 남았다"
    assert good.exists(), "멀쩡한 캐시까지 지웠다"
    assert caplog.records, "캐시를 지우고 아무 기록도 남기지 않았다"


def test_repair_is_quiet_when_nothing_is_damaged(tmp_path, caplog):
    """대조군 — 손상이 없으면 아무것도 지우지 않고 로그도 안 남긴다.

    멀쩡한 상태에서 경고가 매번 찍히면 그 경고는 읽히지 않게 된다.
    """
    for name in ("tkr-tz.db", "cookies.db", "isin-tkr.db"):
        _make_sqlite(tmp_path / name, with_table=True)

    with caplog.at_level(logging.WARNING):
        mc._repair_yf_cache_dir(str(tmp_path))

    assert all((tmp_path / n).exists()
               for n in ("tkr-tz.db", "cookies.db", "isin-tkr.db"))
    assert not caplog.records, f"정상인데 경고가 찍혔다: {[r.message for r in caplog.records]}"


def test_repair_tolerates_a_missing_directory(tmp_path):
    """디렉터리가 아예 없어도 예외를 내지 않는다 — 기동 경로에서 돈다."""
    mc._repair_yf_cache_dir(str(tmp_path / "does-not-exist"))


# ── DB 는 있는데 행이 없을 때 ──────────────────────────────────────────────────
#
# 위 검사들은 "DB 가 없다" 를 잰다. 이건 **DB 는 멀쩡한데 그 티커의 행이 없는**
# 경우다 — 코드에서 다른 분기이고, 신규 종목이면 매번 지나는 길이다.
#
# 실DB 를 쓰되 **상태를 직접 만든다.** "이 창의 DB 에 무엇이 있느냐" 에 기대면
# 그 결과는 `pfp_test` 에 대한 사실일 뿐이고 다른 창에서는 다른 분기가 돈다.
# 티커는 실재하지 않는 이름을 쓴다 — `market_prices` 키가 (ticker, price_date)
# 라 실재 티커를 쓰면 다른 테스트와 부딪친다.

_ABSENT = "__TEST_ABSENT__"
_CACHE_KEY = "__TEST_CACHE__"


@pytest.fixture
def clean_cache_rows(live_db):
    """이 테스트가 만든 행만 지운다."""
    from backend.db import get_conn

    def purge():
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM market_prices WHERE ticker LIKE %s", ("__TEST%",))
                cur.execute("DELETE FROM common_cache WHERE cache_type LIKE %s", ("__TEST%",))

    purge()
    yield
    purge()


def test_reading_a_ticker_with_no_rows_returns_nothing(clean_cache_rows):
    """행이 없는 티커는 `None` 이다 — 빈 프레임이 아니다.

    빈 프레임을 돌려주면 호출자가 "값이 없는 기간" 으로 읽고 계산을 이어간다.
    `None` 만이 "이 티커는 캐시에 아예 없다" 를 말한다.
    """
    assert mc.get_prices_from_db([_ABSENT], "1mo") is None
    assert mc.get_volume_from_db([_ABSENT], "1mo") is None


def test_a_ticker_with_no_rows_is_stale(clean_cache_rows):
    """캐시에 없는 티커는 stale 이다 — 신규 종목이 여기로 온다."""
    assert mc.get_stale_tickers([_ABSENT]) == [_ABSENT]


def test_a_frame_the_calendar_rejects_writes_nothing(clean_cache_rows):
    """비거래일 행만 담긴 프레임은 한 행도 저장하지 않는다.

    이 함수가 `market_prices` 의 **유일한 기록 지점**이라(§1.6), 호출자가
    ffill 된 프레임을 넘겨도 위조 종가가 영구 저장되지 않아야 한다. 주말만
    담아 그 성질을 잰다.
    """
    import pandas as pd

    weekend = pd.to_datetime(["2026-09-05", "2026-09-06"])   # 토·일
    mc.save_prices_to_db(pd.DataFrame({_ABSENT: [100.0, 101.0]}, index=weekend))

    assert mc.get_prices_from_db([_ABSENT], "1mo") is None, (
        "a weekend row reached market_prices -- this function is the only "
        "write point precisely so a fabricated close cannot be stored. "
        "(비거래일 행이 저장됐다.)"
    )


def test_a_trading_day_frame_does_write(clean_cache_rows):
    """대조군 — 확정된 거래일 행은 저장된다.

    없으면 위 검사는 "아무것도 저장하지 않는다" 는 구현으로도 통과한다.
    그건 가드가 아니라 수집이 죽은 것이다.
    """
    import pandas as pd

    from backend.services.market_calendar import last_completed_session

    day = pd.to_datetime([last_completed_session()])
    mc.save_prices_to_db(pd.DataFrame({_ABSENT: [123.45]}, index=day))

    out = mc.get_prices_from_db([_ABSENT], "1mo")
    assert out is not None and not out.empty, "확정 거래일 종가가 저장되지 않았다"


@pytest.mark.parametrize("value, why", [
    ({"a": 1, "b": [2, 3]}, "dict"),
    ([1, 2, 3], "list"),
    ("그냥 문자열", "문자열 — psycopg2 가 str 로 돌려주므로 json.loads 가 실패한다"),
    (42, "숫자"),
    (True, "불리언"),
])
def test_a_cached_value_survives_the_round_trip(clean_cache_rows, value, why):
    """넣은 것이 그대로 돌아온다.

    문자열 케이스가 특별하다. 컬럼이 `JSONB` 라 값은 항상 유효한 JSON 인데,
    JSON 문자열(`"그냥 문자열"`)은 psycopg2 가 파이썬 `str` 로 풀어서 준다.
    그러면 `json.loads("그냥 문자열")` 이 실패하고 **원문 그대로 돌려주는
    분기**로 간다. `None` 으로 떨어뜨리면 캐시에 값이 있는데도 미스가 되어
    매번 다시 만든다 — 캐시가 조용히 꺼진 상태다.

    (처음엔 임의의 비-JSON 문자열을 직접 INSERT 해서 이 분기를 재려 했는데,
    `JSONB` 컬럼이 그걸 거부한다. 스키마가 막아 둔 상황을 재려 한 것이었고,
    실제로 도달하는 경로는 이쪽이다.)
    """
    mc.save_common(_CACHE_KEY, value)

    assert mc.get_common(_CACHE_KEY) == value, why


def test_a_missing_cache_key_is_a_miss(clean_cache_rows):
    """없는 키는 `None` 이다 — 대조군."""
    assert mc.get_common("__TEST_NEVER_WRITTEN__") is None

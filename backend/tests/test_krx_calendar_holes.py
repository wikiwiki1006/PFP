"""
KRX 캘린더는 한 소스의 구멍 때문에 거래일을 잃지 않는다.

이 집합은 **"없으면 휴장"** 으로 읽힌다. 그래서 소스의 데이터 공백이 곧
휴장일이 된다. 2026-09-10(목)에 실제로 그랬다 — `^KS11`·`^KQ11` 양쪽에 그날이
없었지만 `005930.KS`·`000660.KS` 에는 정상 봉이 있었다. KRX 는 열려 있었고
야후 지수 피드만 구멍이 났다.

한 번 휴장으로 읽히면 스스로 못 고친다. `save_prices_to_db` 의 가드는 날짜
선후가 아니라 **개장 여부**를 보므로 그날 종가는 나중에 다시 받아도 저장되지
못하고, `last_completed_kr_session()` 은 그 전날을 가리켜 수집기는 최신이라고
믿는다. 그래서 지수 하나가 아니라 **여러 소스의 합집합**으로 만든다.

## 폴백은 작성된 뒤 한 번도 실행된 적이 없었다

소스를 모두 놓쳤을 때 "평일이면 개장" 으로 내려가는 폴백이 docstring 에
적혀 있었는데, 그 경로의 `logger.warning` 이 **이 모듈에 없는 이름**을 불렀다.
그래서 실제로는 폴백 대신 `NameError` 가 호출자에게 올라갔다.

    NameError: name 'logger' is not defined

`market_data.py` 에서 난 것과 같은 종류인데(`logger`/`_logger`), 여기는 아예
정의가 없었다. 폴백을 실행시켜 본 테스트가 없으면 이런 줄은 영원히 안 도는
채로 남는다. 아래 `test_all_sources_failing_falls_back_to_weekdays` 가 그 줄을
지난다.

## 캐시가 3단이라 끊는 순서가 중요하다

    프로세스 메모(10분) → DB 캐시(2시간) → yfinance

가짜 소스를 심어도 **앞 두 단에서 잘리면 아무것도 검증하지 못한다.** 앞
테스트가 남긴 메모가 그대로 넘어오면 테스트는 엉뚱한 이유로 통과한다.
그래서 픽스처가 매번 세 단을 모두 끊는다.
"""
from __future__ import annotations

from datetime import date, timedelta
from unittest import mock

import pandas as pd
import pytest

from backend.services import market_calendar as mc

# 캘린더 범위를 만들 기준일들. 미래 날짜를 쓰지 않는다 — `recent` 계산이
# 오늘을 보므로 과거로 고정해야 결과가 흔들리지 않는다.
_BASE = date(2026, 9, 1)          # 화요일


def _weekdays(start: date, count: int) -> list[date]:
    out, d = [], start
    while len(out) < count:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


_DAYS = _weekdays(_BASE, 10)
_HOLE = _DAYS[7]                  # 한 소스에만 빠진 날 (09-10 의 역할)
_CLOSED = _DAYS[4]                # 어느 소스에도 없는 날 (공휴일의 역할)


def _hist(days: list[date]) -> pd.DataFrame:
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in days])
    return pd.DataFrame({"Close": [2600.0] * len(days)}, index=idx)


def _fake_yf(per_source: dict[str, list[date]] | None = None,
             error: Exception | None = None):
    """`yfinance.Ticker` 대역. `per_source` 에 없는 티커는 빈 시계열을 준다."""
    class _T:
        def __init__(self, tkr):
            self.tkr = tkr

        def history(self, *a, **kw):
            if error is not None:
                raise error
            return _hist((per_source or {}).get(self.tkr, []))

    return _T


@pytest.fixture(autouse=True)
def sever_all_three_caches():
    """메모 · DB 캐시 · 소스를 매번 끊는다.

    자동 적용이다. 하나라도 놓치면 그 테스트는 앞 테스트가 남긴 집합을 보고
    통과한다 — 통과했는데 아무것도 검증하지 않은 상태가 된다.
    """
    mc.reset_krx_calendar_memo()
    yield
    mc.reset_krx_calendar_memo()


@pytest.fixture
def no_db_cache():
    """DB 캐시를 항상 미스로 만들고, 저장된 것을 받아 둔다."""
    saved: dict = {}

    def fake_save(key, value, ttl_seconds=None):
        saved[key] = (value, ttl_seconds)

    with mock.patch("backend.db.market_cache.get_common", lambda key: None), \
         mock.patch("backend.db.market_cache.save_common", fake_save):
        yield saved


# ── 1. 폴백이 실제로 실행된다 ──────────────────────────────────────────────────

def test_all_sources_failing_falls_back_to_weekdays(no_db_cache):
    """소스를 하나도 못 받으면 예외가 아니라 '평일이면 개장' 이다.

    이 경로는 `logger.warning` 을 두 군데서 부른다 — 소스별 실패와 전부 실패.
    그 이름이 모듈에 없으면 폴백 대신 `NameError` 가 호출자에게 올라간다.
    작성된 뒤 한 번도 실행된 적 없던 줄이라 실제로 그랬다.
    """
    with mock.patch("yfinance.Ticker", _fake_yf(error=RuntimeError("feed down"))):
        # 예외가 새어 나오면 이 호출에서 바로 터진다.
        monday = date(2026, 9, 7)
        saturday = date(2026, 9, 12)

        assert mc.is_kr_trading_day(monday) is True, (
            "no source available, so the calendar must fall back to weekdays -- "
            "treating a real trading day as a holiday throws that day's prices "
            "away permanently. (폴백이 동작하지 않는다.)"
        )
        assert mc.is_kr_trading_day(saturday) is False, "주말은 폴백에서도 휴장이다"


def test_fallback_path_is_reached_through_the_public_entry_point(no_db_cache):
    """폴백 진입이 `_krx_trading_days()` 가 빈 집합을 주는 형태인지 고정한다.

    빈 집합이 곧 '평일 폴백' 이라는 계약이다. 여기가 어긋나면 위 테스트가
    다른 이유로 통과할 수 있다.
    """
    with mock.patch("yfinance.Ticker", _fake_yf(error=RuntimeError("feed down"))):
        assert mc._krx_trading_days() == frozenset()


# ── 2·3. 합집합은 구멍만 메우고 휴일까지 열지 않는다 (한 쌍) ────────────────────

def test_union_fills_a_hole_in_one_source(no_db_cache):
    """한 소스에만 빠진 날은 다른 소스가 메운다. 09-10 의 실제 형태다."""
    with_hole = [d for d in _DAYS if d not in (_HOLE, _CLOSED)]
    full = [d for d in _DAYS if d != _CLOSED]

    with mock.patch("yfinance.Ticker", _fake_yf({
        "^KS11": with_hole,          # 지수 피드에 구멍
        "005930.KS": full,           # 대형주에는 정상 봉
        "000660.KS": full,
    })):
        assert mc.is_kr_trading_day(_HOLE) is True, (
            f"{_HOLE} is missing only from the index feed but present in the "
            "large caps -- the union must count it as open, or the day's "
            "closes are discarded and never recoverable. (구멍이 휴장이 됐다.)"
        )


def test_a_day_in_no_source_stays_closed(no_db_cache):
    """어느 소스에도 없는 날은 휴장으로 남는다.

    합집합이 이쪽으로 새면 공휴일이 개장일이 되고, 그러면
    `last_completed_kr_session()` 이 존재하지 않는 종가를 가리켜 수집기가 같은
    종목을 무한히 다시 받는다. 위 테스트와 한 쌍이다 — 한쪽만 있으면 "전부
    개장" 이라는 구현으로도 통과한다.
    """
    without_closed = [d for d in _DAYS if d != _CLOSED]

    with mock.patch("yfinance.Ticker", _fake_yf({
        "^KS11": without_closed,
        "005930.KS": without_closed,
        "000660.KS": without_closed,
    })):
        assert mc.is_kr_trading_day(_CLOSED) is False, (
            f"{_CLOSED} is absent from every source yet was reported open -- a "
            "holiday turned into a trading day makes the collector chase a "
            "close that will never exist. (휴일이 개장일이 됐다.)"
        )
        assert mc.is_kr_trading_day(_DAYS[-1]) is True, "정상 개장일까지 닫으면 안 된다"


# ── 4. 캐시 키가 소스 구성을 담는다 ────────────────────────────────────────────

def test_cache_key_changes_with_the_source_list():
    """소스 구성을 바꾸면 옛 키로 저장된 값이 나오지 않는다.

    키에 구성을 넣지 않았을 때 실제로 옛 목록(09-10 이 빠진 것)이 최대 2시간
    동안 계속 반환됐다. 소스를 고치는 것이 곧 캐시 무효화여야 한다.
    """
    stale = [d.isoformat() for d in _DAYS if d != _HOLE]
    store: dict[str, list[str]] = {}

    def fake_get(key):
        return store.get(key)

    def fake_save(key, value, ttl_seconds=None):
        store[key] = value

    with mock.patch("backend.db.market_cache.get_common", fake_get), \
         mock.patch("backend.db.market_cache.save_common", fake_save), \
         mock.patch("yfinance.Ticker", _fake_yf({t: _DAYS for t in mc._KRX_CALENDAR_SOURCES})):

        # 옛 구성으로 한 번 채운다.
        with mock.patch.object(mc, "_KRX_CALENDAR_SOURCES", ("^KS11",)):
            mc.reset_krx_calendar_memo()
            store["krx_trading_days:^KS11"] = stale
            assert mc._krx_trading_days() == frozenset(
                date.fromisoformat(d) for d in stale), "전제: 옛 키가 읽힌다"

        # 구성을 넓히면 옛 값이 재사용되면 안 된다.
        mc.reset_krx_calendar_memo()
        days = mc._krx_trading_days()

    assert _HOLE in days, (
        "widening the source list still returned the value cached under the "
        "old key -- changing the sources has to invalidate the cache, or the "
        "fix stays invisible for up to two hours. (옛 캐시가 그대로 나왔다.)"
    )
    assert len({k for k in store if k.startswith("krx_trading_days:")}) >= 2, (
        f"both configurations shared one cache key: {sorted(store)}"
    )


# ── 5. 메모 TTL 이 DB TTL 보다 짧다 ────────────────────────────────────────────

def test_process_memo_expires_before_the_db_cache(no_db_cache):
    """프로세스 메모가 DB 캐시보다 먼저 만료돼야 한다.

    뒤집히면 DB 캐시가 스스로 고쳐진 뒤에도 이 프로세스만 옛 스냅샷을 든다.
    DB 쪽 TTL 을 숫자로 적지 않고 **실제로 넘긴 값**을 받아 비교한다 — 상수를
    베껴 적으면 한쪽이 바뀔 때 이 관계가 조용히 깨진다.
    """
    with mock.patch("yfinance.Ticker", _fake_yf({t: _DAYS for t in mc._KRX_CALENDAR_SOURCES})):
        mc._krx_trading_days()

    saved = no_db_cache
    assert saved, "캘린더가 DB 캐시에 저장되지 않았다 — 이 비교의 전제가 없다"
    _, db_ttl = next(iter(saved.values()))
    assert db_ttl, f"save_common 에 ttl 이 안 넘어갔다: {db_ttl!r}"

    assert mc._KRX_MEMO_TTL < db_ttl, (
        f"process memo TTL {mc._KRX_MEMO_TTL}s is not shorter than the DB cache "
        f"TTL {db_ttl}s -- once the shared cache heals, this process keeps "
        "serving the stale snapshot. (프로세스만 옛 값을 든다.)"
    )
    assert mc._KRX_MEMO_TTL_FAIL < mc._KRX_MEMO_TTL, (
        "a failed lookup is remembered at least as long as a good one -- there "
        "is no reason to sit in the weekday fallback for the full TTL."
    )

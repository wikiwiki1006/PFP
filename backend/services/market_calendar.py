"""
services/market_calendar.py
───────────────────────────
미국 증시(NYSE) 거래일·장중 상태 판정의 단일 진실 공급원.

이 모듈이 생기기 전에는 `_is_market_open()` 이 3곳에 중복 구현돼 있었고
(routers/portfolio.py, services/market_data.py, services/daily_report.py),
모두 "주말이 아니면 거래일"로 간주해 **공휴일을 전혀 처리하지 못했다**.
그 결과 공휴일에는 장이 열린 것으로 오판하고, 전일 종가를 그대로 복사한
유령 행이 당일 종가로 저장·표시돼 일변동률이 0%가 되는 버그가 발생했다.

pandas 내장 holiday 규칙만 사용한다 (신규 의존성 없음).
2026년 기준 실제 NYSE 휴장일 10일과 정확히 일치함을 검증했다:
  01-01, 01-19, 02-16, 04-03, 05-25, 06-19, 07-03, 09-07, 11-26, 12-25
"""
from __future__ import annotations

from datetime import date, datetime, time as _dtime, timedelta
from functools import lru_cache
from typing import Literal, Optional

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    try:
        import pytz  # type: ignore
        ET = pytz.timezone("America/New_York")
    except Exception:
        ET = None  # type: ignore

_MARKET_OPEN  = _dtime(9, 30)
_MARKET_CLOSE = _dtime(16, 0)

# 미국 증시 캘린더를 따르지 않는 티커 (24/7 또는 해외 거래소)
_NON_US_EXACT = {"^KS11", "^KQ11", "^N225", "^HSI", "^STOXX50E", "^FTSE", "^GDAXI"}
_NON_US_SUFFIX = (
    "-USD",   # 암호화폐 (BTC-USD)
    "=X",     # 환율 (USDKRW=X)
    "=F",     # 선물 (GC=F, CL=F)
    ".KS", ".KQ",          # 한국
    ".T",  ".HK", ".SS", ".SZ",   # 일본·홍콩·중국
    ".L",  ".DE", ".PA", ".AS", ".SW", ".MI",  # 유럽
    ".TO", ".V",           # 캐나다
    ".AX",                 # 호주
)


def uses_us_session_calendar(ticker: str) -> bool:
    """해당 티커가 NYSE 거래일 캘린더를 따르는지.

    False 인 종목(암호화폐·환율·선물·해외 상장)은 주말/미국 공휴일에도
    실제로 거래되므로, 거래일 필터·유령행 제거 대상에서 제외해야 한다.
    """
    t = str(ticker).upper().strip()
    if t in _NON_US_EXACT:
        return False
    return not t.endswith(_NON_US_SUFFIX)


@lru_cache(maxsize=32)
def _holidays_for_year(year: int) -> frozenset[date]:
    """해당 연도의 NYSE 정기 휴장일 집합 (연도별 메모이제이션)."""
    from pandas.tseries.holiday import (
        AbstractHolidayCalendar, Holiday, nearest_workday, sunday_to_monday,
        USMartinLutherKingJr, USPresidentsDay, GoodFriday,
        USMemorialDay, USLaborDay, USThanksgivingDay,
    )

    class _NYSECalendar(AbstractHolidayCalendar):
        rules = [
            Holiday("NewYears", month=1, day=1, observance=sunday_to_monday),
            USMartinLutherKingJr,
            USPresidentsDay,
            GoodFriday,
            USMemorialDay,
            Holiday("Juneteenth", month=6, day=19,
                    start_date="2022-06-20", observance=nearest_workday),
            Holiday("IndependenceDay", month=7, day=4, observance=nearest_workday),
            USLaborDay,
            USThanksgivingDay,
            Holiday("Christmas", month=12, day=25, observance=nearest_workday),
        ]

    try:
        days = _NYSECalendar().holidays(f"{year}-01-01", f"{year}-12-31")
        return frozenset(d.date() for d in days)
    except Exception:
        return frozenset()


def now_et() -> datetime:
    """현재 시각(미 동부). tz 정보가 없으면 UTC-4 근사."""
    if ET is not None:
        return datetime.now(ET)
    from datetime import timezone
    return datetime.now(timezone.utc) + timedelta(hours=-4)


try:
    KST = ZoneInfo("Asia/Seoul")
except Exception:  # pragma: no cover
    KST = None  # type: ignore


def now_kst() -> datetime:
    """현재 시각(한국). 로그·화면 표시용."""
    n = now_et()
    return n.astimezone(KST) if KST is not None else n


def et_to_kst_label(et_hour: int, et_minute: int = 0) -> str:
    """ET 기준 시각을 오늘 날짜로 환산한 KST 표기 (예: '17:00 ET' → '06:00 KST (익일)').

    서머타임 때문에 ET↔KST 시차가 13/14시간으로 바뀌므로 고정 문자열을 쓰면 안 되고,
    실제 날짜로 변환해야 한다. 스케줄 **판정 로직은 ET 를 그대로 쓴다** — KST 로
    고정하면 서머타임 전환 때 장 마감 전에 수집이 돌아 미확정 종가를 저장하게 된다.
    """
    if ET is None or KST is None:
        return f"{et_hour:02d}:{et_minute:02d} ET"
    base = now_et().replace(hour=et_hour, minute=et_minute, second=0, microsecond=0)
    k = base.astimezone(KST)
    day = "" if k.date() == base.date() else " (익일)"
    return f"{k.strftime('%H:%M')} KST{day}"


def is_us_trading_day(d: date) -> bool:
    """해당 날짜가 실제 NYSE 정규 거래일인지 (주말·공휴일 제외)."""
    if hasattr(d, "date") and not isinstance(d, date):
        d = d.date()  # type: ignore[assignment]
    if d.weekday() >= 5:
        return False
    return d not in _holidays_for_year(d.year)


def us_market_status(now: Optional[datetime] = None) -> Literal["pre", "open", "post", "closed"]:
    """현재 미국 증시 상태.

    closed — 오늘이 거래일이 아님 (주말·공휴일)
    pre    — 거래일이지만 09:30 ET 이전
    open   — 09:30 ~ 16:00 ET
    post   — 16:00 ET 이후
    """
    n = now or now_et()
    if not is_us_trading_day(n.date()):
        return "closed"
    t = n.time()
    if t < _MARKET_OPEN:
        return "pre"
    if t < _MARKET_CLOSE:
        return "open"
    return "post"


def is_us_market_open(now: Optional[datetime] = None) -> bool:
    return us_market_status(now) == "open"


# 시간외 거래 시간대 (프리마켓 04:00, 애프터마켓 20:00 ET)
_EXT_OPEN  = _dtime(4, 0)
_EXT_CLOSE = _dtime(20, 0)


def is_us_extended_hours(now: Optional[datetime] = None) -> bool:
    """미국 주식 가격이 **변할 수 있는** 시간대인지 (프리·정규·애프터마켓).

    정규장 마감 후에도 20:00 ET 까지는 시간외 거래로 가격이 실제로 움직인다.
    이 시간대 밖(심야·주말·공휴일)에는 가격이 고정이므로 캐시를 재수집할 필요가 없다.
    """
    n = now or now_et()
    if not is_us_trading_day(n.date()):
        return False
    return _EXT_OPEN <= n.time() < _EXT_CLOSE


def last_completed_session(now: Optional[datetime] = None) -> date:
    """마지막으로 **종가가 확정된** 거래일.

    오늘이 거래일이고 16:00 ET 를 지났으면 오늘, 아니면 직전 거래일.
    장전·장중·주말·공휴일에는 모두 '직전에 끝난 거래일'을 가리킨다.
    """
    n = now or now_et()
    d = n.date()
    if is_us_trading_day(d) and n.time() >= _MARKET_CLOSE:
        return d
    d -= timedelta(days=1)
    for _ in range(15):  # 연휴가 아무리 길어도 15일 내에 거래일 존재
        if is_us_trading_day(d):
            return d
        d -= timedelta(days=1)
    return d


def us_price_cutoff(now: Optional[datetime] = None) -> date:
    """미국 주식 가격 시계열에서 **허용되는 가장 늦은 날짜**.

    09:30 ET 이후(장중·장후)에는 오늘 날짜의 실시간/당일 봉이 유효하므로 오늘.
    그 이전(장전·주말·공휴일)에는 오늘 날짜 행이 존재하더라도 그것은
    아직 거래가 없었다는 뜻이므로 → 마지막 확정 거래일까지만 인정한다.
    이 규칙이 "장전 유령행"을 값 비교가 아닌 **캘린더 기준**으로 잘라낸다.
    """
    n = now or now_et()
    d = n.date()
    if is_us_trading_day(d) and n.time() >= _MARKET_OPEN:
        return d
    return last_completed_session(n)


def next_session_open(now: Optional[datetime] = None) -> date:
    """다음(또는 진행 중인) 정규장이 열리는 거래일.

    장이 닫혀 있는 동안 만든 결과를 "다음 장이 열릴 때까지" 재사용하려면,
    그 사이 내내 같은 값이 나오는 기준이 필요하다. 날짜만 쓰면 월요일 저녁과
    화요일 아침이 서로 다른 키가 되어, 밤새 만들어 둔 결과를 아침에 다시
    만들게 된다. 다음 개장일을 기준으로 삼으면 둘 다 같은 키가 된다.

    장중이거나 개장 전이면 오늘, 마감 후·휴장일이면 다음 거래일을 돌려준다.
    """
    n = now or now_et()
    d = n.date()
    if is_us_trading_day(d) and n.time() < _MARKET_CLOSE:
        return d
    for _ in range(1, 12):          # 연휴가 길어도 이 안에서 끝난다
        d += timedelta(days=1)
        if is_us_trading_day(d):
            return d
    return d

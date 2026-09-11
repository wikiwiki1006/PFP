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

import logging
import time as _time
from datetime import date, datetime, time as _dtime, timedelta
from functools import lru_cache
from typing import Literal, Optional

logger = logging.getLogger(__name__)

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


# maxsize 는 조회하는 서로 다른 연도 수보다 커야 한다. 32 였을 때 41개 연도를
# 훑으면 캐시가 완전히 무너졌다 — 32개 연도 2회 조회 339ms(적중 32) 대 41개 연도
# 2회 조회 798ms(적중 0). LRU 라 매 조회가 직전 것을 밀어내 적중률이 0이 된다.
# 값은 연도당 frozenset 10개짜리라 256개를 들고 있어도 메모리는 무시할 만하다.
@lru_cache(maxsize=256)
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


# ── 한국 증시 (KRX) ──────────────────────────────────────────────────────────
#
# 미국은 공휴일이 규칙으로 정의돼 계산할 수 있지만, 한국은 설·추석이 음력이라
# 규칙만으로는 구할 수 없다. 대체공휴일·임시공휴일도 해마다 바뀐다.
#
# 그래서 규칙을 짜는 대신 **실제 거래가 있었던 날**을 KOSPI 지수 시세에서
# 읽는다. 지수가 존재하는 날이 곧 개장일이고, 음력 명절도 자동으로 반영된다.
# 결과는 DB 에 캐시해 두고 하루 한 번만 갱신한다.

_KRX_OPEN  = _dtime(9, 0)
_KRX_CLOSE = _dtime(15, 30)


# 캘린더를 지수 하나로 만들지 않는다.
#
# ^KS11 은 정규 거래일인데도 행이 통째로 빠질 때가 있다. 2026-09-10(목)이
# ^KS11 과 ^KQ11 양쪽 모두에 없었지만 005930.KS 와 000660.KS 에는 정상 봉이
# 있었다 — KRX 는 열려 있었고 야후의 **지수 피드만** 구멍이 났다.
#
# 이 집합은 "없으면 휴장"으로 읽히므로 그 구멍이 그대로 휴장일이 된다. 그러면
# save_prices_to_db 의 가드가 그날 종가를 영구히 거부하고(가드는 날짜 선후가
# 아니라 개장 여부를 보므로 나중에 다시 받아도 통과하지 못한다),
# last_completed_kr_session 은 그 전날을 가리켜 수집기는 이미 최신이라고 믿는다.
# 아무도 모르는 채로 하루가 영구히 사라진다. 실제로 09-10 종가는 ^KS11 과
# 005930.KS 모두 DB 에 없었다.
#
# 그래서 지수 + 대형주 두 종목의 **합집합**으로 만든다. 어느 하나라도 거래된
# 날은 개장일이다 — 정의상 맞는 판정이고, 세 소스가 같은 날을 동시에 빠뜨려야
# 구멍이 생긴다. 소스끼리 어긋나면 경고로 남긴다: 합집합이 고장 난 소스를
# 조용히 덮어 주면 피드가 망가진 것을 아무도 모른다.
_KRX_CALENDAR_SOURCES = ("^KS11", "005930.KS", "000660.KS")


# 프로세스 안 메모. DB 캐시(2시간)만으로는 부족하다 — save_prices_to_db 는
# 프레임의 **행마다** is_kr_trading_day 를 부르고, 그 한 번이 DB 왕복 1회다.
# 2년 프레임 한 장이면 약 500회, 측정으로 992ms 가 캘린더 재조회에만 쓰였다.
# 2시간 동안 바뀌지 않는 집합을 500번 다시 읽는 것이므로 전부 낭비다.
#
# TTL 을 DB 쪽(7200s)보다 훨씬 짧게 둔다. 길게 잡으면 DB 캐시가 스스로 고쳐진
# 뒤에도 이 프로세스만 옛 스냅샷을 들고 있게 된다. 실패는 더 짧게 기억한다 —
# 소스가 잠깐 죽었을 때 10분씩 평일 폴백에 머물 이유가 없다.
#
# 락은 걸지 않는다. 경합하면 조회가 두 번 날 뿐 결과는 같다.
_KRX_MEMO_TTL      = 600.0
_KRX_MEMO_TTL_FAIL = 60.0
_krx_memo: tuple = (0.0, frozenset())


def reset_krx_calendar_memo() -> None:
    """프로세스 메모를 비운다 (테스트·수동 갱신용). DB 캐시는 건드리지 않는다."""
    global _krx_memo
    _krx_memo = (0.0, frozenset())


def _krx_trading_days() -> frozenset:
    """최근 2년간 KRX 실제 개장일 (프로세스 메모 → DB 캐시 → 소스)."""
    global _krx_memo
    ts, memo = _krx_memo
    if ts and (_time.monotonic() - ts) < (_KRX_MEMO_TTL if memo else _KRX_MEMO_TTL_FAIL):
        return memo
    days = _krx_trading_days_fetch()
    _krx_memo = (_time.monotonic(), days)
    return days


def _krx_trading_days_fetch() -> frozenset:
    """DB 캐시 우선, 없으면 소스에서 새로 만든다.

    **오늘도 들어 있다.** 야후는 장중에도 당일 봉의 Close 를 실시간 값으로
    채워 주므로 첫 체결이 찍힌 뒤로는 오늘이 개장일로 잡힌다. 예전 주석들은
    "오늘은 항상 빠져 있다"고 적혀 있었는데, 그때는 야후가 미확정 종가를 NaN
    으로 내려 줬기 때문이다. 외부 계약이 바뀌었고 그 전제는 더 이상 참이 아니다.
    종가 확정 여부는 이 집합이 아니라 last_completed_kr_session 이 판단한다
    (15:30 KST 를 지났는지 직접 본다).

    TTL 이 24시간이 아니라 2시간인 것은 이 집합이 관측 기반이기 때문이다.
    09:00 이전에 갱신되면 오늘이 아직 없고, 그 스냅샷이 오래 남을수록 그
    상태가 길어진다. 2시간이면 늦어도 오전 중에 스스로 메워진다.
    (사고 기록: 09-07 마감 직후 캐시된 값이 09-08 오전까지 남아 포트폴리오
    화면에 09-04 종가가 최신으로 표시됐다.)
    """
    from backend.db.market_cache import get_common, save_common

    # 캐시 키에 소스 구성을 넣는다. 넣지 않으면 소스를 고쳐도 옛 결과가 최대
    # 2시간 동안 계속 나온다 — 실제로 소스를 하나에서 셋으로 늘린 직후 옛
    # 484일 목록(09-10 이 빠진 것)이 그대로 반환됐다. 키가 구성을 담으면
    # 코드 변경이 곧 캐시 무효화가 된다.
    key = "krx_trading_days:" + ",".join(_KRX_CALENDAR_SOURCES)

    cached = get_common(key)
    if cached:
        return frozenset(date.fromisoformat(d) for d in cached)

    try:
        import yfinance as yf
    except Exception as e:
        logger.warning("KRX 개장일 수집 실패 — yfinance 를 불러올 수 없습니다: %s", e)
        return frozenset()

    per_source: dict[str, set] = {}
    for tkr in _KRX_CALENDAR_SOURCES:
        try:
            hist = yf.Ticker(tkr).history(period="2y")
            # Close 가 있는 날만 센다. 값이 없는 행(거래정지·데이터 공백)은
            # 그 소스에게는 개장일의 증거가 되지 못한다. 다른 소스가 메운다.
            got = {d.date() for d in hist.index[hist["Close"].notna()]}
            if got:
                per_source[tkr] = got
            else:
                logger.warning("KRX 캘린더 소스 %s 가 빈 시계열을 돌려줬습니다", tkr)
        except Exception as e:
            logger.warning("KRX 캘린더 소스 %s 조회 실패: %s", tkr, e)

    if not per_source:
        # 여기서 빈 집합을 돌려주면 is_kr_trading_day 가 '평일이면 개장'으로
        # 내려간다. 그 폴백은 의도된 것이지만, 조용히 일어나면 안 된다.
        logger.warning("KRX 개장일을 한 소스도 받지 못했습니다 — 평일 폴백으로 내려갑니다")
        return frozenset()

    days = set().union(*per_source.values())

    # 합집합이 메워 준 구멍을 남긴다. 최근 30일만 본다 — 2년 전 거래정지까지
    # 전부 찍으면 신호가 아니라 소음이 된다.
    recent = now_kst().date() - timedelta(days=30)
    for tkr, got in per_source.items():
        gaps = sorted(d for d in days - got if d >= recent)
        if gaps:
            logger.warning(
                "KRX 캘린더: %s 에 최근 개장일 %d일이 없습니다 (%s%s) — 다른 소스로 메웠습니다",
                tkr, len(gaps), ", ".join(d.isoformat() for d in gaps[:5]),
                " 외" if len(gaps) > 5 else "",
            )

    if len(per_source) < len(_KRX_CALENDAR_SOURCES):
        logger.warning(
            "KRX 캘린더를 소스 %d/%d 개로만 만들었습니다 (%s) — 구멍 방어력이 낮습니다",
            len(per_source), len(_KRX_CALENDAR_SOURCES), ", ".join(per_source),
        )

    save_common(key, [d.isoformat() for d in sorted(days)], ttl_seconds=7200)
    return frozenset(days)


def is_kr_trading_day(d: date) -> bool:
    """KRX 개장일인지.

    캘린더를 못 받았을 때는 '평일이면 개장'으로 본다. 데이터를 못 읽었다는
    이유로 멀쩡한 거래일을 휴장으로 처리하면 그날 시세가 통째로 버려진다.

    반대로 캘린더 **범위 안**의 빠진 날은 휴장으로 본다. 범위 안에서는 구멍과
    공휴일을 구별할 수 없고, 둘 중 하나를 골라야 한다면 휴장이 맞다: 평일로
    돌리면 설·추석마다 last_completed_kr_session 이 존재하지 않는 종가를
    가리켜 수집기가 같은 종목을 무한히 다시 받는다(연 십수 일). 구멍 쪽은
    _KRX_CALENDAR_SOURCES 합집합으로 막고 어긋나면 경고를 남긴다.
    """
    if hasattr(d, "date") and not isinstance(d, date):
        d = d.date()  # type: ignore[assignment]
    days = _krx_trading_days()
    if not days:
        return d.weekday() < 5
    if d < min(days):
        return d.weekday() < 5     # 캐시 범위 밖의 과거
    return d in days


def kr_market_status(now: Optional[datetime] = None) -> Literal["pre", "open", "post", "closed"]:
    """현재 한국 증시 상태 (정규장 09:00~15:30 KST).

    거래일 판정에 _krx_trading_days() 를 쓰지 않는다. 그 캘린더는 **관측 기반**
    이라 첫 체결이 찍히기 전에는 오늘을 알 수 없다 — 08:50 에 물어보면 '개장일
    아님'이 나오고, 그걸로 판단하면 09:00 직후 화면의 LIVE 배지가 꺼진 채로
    시작한다. 캐시가 어제 갱신됐다면 더 오래 간다.

    평일 여부만 본다. 한국 공휴일에는 'open' 으로 오판하지만(연 십수 일),
    장중에 '마감'이라고 하는 것보다 낫다. is_kr_extended_hours 와 같은 규칙이다.
    과거 날짜의 개장 여부가 필요한 곳(시세 저장 가드·stale 판정)은 그대로
    is_kr_trading_day / last_completed_kr_session 을 쓴다.
    """
    n = now or now_kst()
    if n.weekday() >= 5:
        return "closed"
    t = n.time()
    if t < _KRX_OPEN:
        return "pre"
    if t < _KRX_CLOSE:
        return "open"
    return "post"


def is_kr_market_open(now: Optional[datetime] = None) -> bool:
    return kr_market_status(now) == "open"


_KRX_EXT_OPEN  = _dtime(8, 30)    # 장전 시간외
_KRX_EXT_CLOSE = _dtime(18, 0)    # 장후 시간외 종료


def is_kr_extended_hours(now: Optional[datetime] = None) -> bool:
    """한국 주식 가격이 **변할 수 있는** 시간대인지 (시간외 포함 08:30~18:00 KST).

    거래일 판정에 _krx_trading_days() 를 쓰지 않는다. 그 캘린더는 관측 기반이라
    첫 체결 전에는 오늘을 알 수 없다 — 08:30 시간외 시작 시점에 물어보면 '거래일
    아님'이 나온다. 그걸로 실시간 수집을 막으면 정작 장중에 시세가 멈춘다.

    그래서 평일 여부만 본다. 공휴일에 불필요한 조회가 조금 생기지만(연 십수 일),
    장중에 시세가 멈추는 것보다 훨씬 낫다. is_kr_trading_day 도 캘린더가 없을 때
    같은 판단(평일=개장)으로 내려간다.
    """
    n = now or now_kst()
    if n.weekday() >= 5:
        return False
    return _KRX_EXT_OPEN <= n.time() < _KRX_EXT_CLOSE


def kr_price_cutoff(now: Optional[datetime] = None) -> date:
    """한국 주식 가격 시계열에서 **허용되는 가장 늦은 날짜**.

    09:00 KST 이후(장중·장후)에는 오늘 봉이 유효하므로 오늘. 그 이전이나
    주말이면 직전 평일까지만.

    last_completed_kr_session 을 쓰지 않는다. 그 함수는 15:30 KST 를 지나야
    오늘을 돌려주므로, 장중에 자산곡선을 그걸로 자르면 한국 포트폴리오의
    오늘자 평가액이 사라진다. 여기서 필요한 것은 '종가가 확정된 날'이 아니라
    '값이 존재할 수 있는 가장 늦은 날'이다.
    """
    n = now or now_kst()
    d = n.date()
    if n.weekday() < 5 and n.time() >= _KRX_OPEN:
        return d
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def last_completed_kr_session(now: Optional[datetime] = None) -> date:
    """마지막으로 **종가가 확정된** KRX 거래일.

    오늘이 개장일이고 15:30 KST 를 지났으면 오늘, 아니면 직전 개장일.
    미국의 last_completed_session 과 같은 역할이다.
    """
    n = now or now_kst()
    d = n.date()
    if is_kr_trading_day(d) and n.time() >= _KRX_CLOSE:
        return d
    d -= timedelta(days=1)
    for _ in range(15):   # 설·추석 연휴도 15일을 넘지 않는다
        if is_kr_trading_day(d):
            return d
        d -= timedelta(days=1)
    return d


def uses_kr_session_calendar(ticker: str) -> bool:
    """KRX 거래일 캘린더를 따르는 티커인지 (.KS / .KQ 와 코스피·코스닥 지수)."""
    t = str(ticker).upper().strip()
    return t.endswith((".KS", ".KQ")) or t in {"^KS11", "^KQ11", "^KS200"}

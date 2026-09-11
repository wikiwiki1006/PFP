"""
services/price_series.py
────────────────────────
"일변동률" 계산의 단일 primitive.

이 모듈이 생기기 전에는 일변동률이 5곳에서 제각각 계산됐고
(portfolio_calculator 2곳, scheduler 2곳, routers 1곳), 모두
`iloc[-1]` / `iloc[-2]` 위치 기반이라 **ffill 로 복제된 유령 행**을
전혀 구분하지 못해 0% 를 반환했다.

핵심 규칙 — "마지막 두 개의 **실제 관측치**"
  · 값이 아니라 **날짜** 기준으로 판단한다.
    (마지막 두 '서로 다른 값'을 쓰면, 실제로 보합 마감한 날을 건너뛰고
     그 전날 변동률을 잘못 보고하게 된다. ^TNX·환율에서 흔하다.)
  · 미국 주식은 NYSE 거래일이 아닌 날짜의 행을 **캘린더 기준으로 제거**한다.
    값 비교 휴리스틱이 아니므로 DB에 유령 행이 남아 있어도 올바르게 동작한다.
  · 암호화폐·환율·선물·해외 상장 종목은 자기 캘린더로 실제 거래되므로
    필터를 적용하지 않는다.
"""
from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from typing import NamedTuple, Optional

import numpy as np
import pandas as pd

from backend.services.market_calendar import (
    ET,
    KST,
    _holidays_for_year,
    kr_price_cutoff,
    now_et,
    now_kst,
    us_price_cutoff,
    uses_kr_session_calendar,
    uses_us_session_calendar,
)


class PortfolioChange(NamedTuple):
    """포트폴리오 일변동과 **그 값이 무엇으로 만들어졌는지**.

    앞의 세 필드가 값이고 뒤의 셋은 그 값의 근거다. 근거를 함께 내보내는
    이유는 집계가 부분 정보로도 그럴듯한 숫자를 내기 때문이다 — 10종목이
    전부 +10% 오른 날 1종목만 계산 가능하면 **+0.92%** 가 나온다. 유한하고
    범위도 그럴듯해서 어떤 가드에도 걸리지 않는다.

    `stale` 은 "자기 시장의 마지막 확정 세션보다 뒤처진 종목" 이다. 서로의
    `as_of` 를 비교하지 않는 이유가 있다 — 미국·한국을 함께 보유하면 두 시장의
    마지막 확정 세션이 원래 다르다(KST 오전에 미국은 전날, 한국은 오늘). 서로
    비교하면 혼합 포트폴리오가 항상 "날짜 섞임" 으로 뜬다. 기준은 각 종목이
    속한 시장이어야 한다.

    24시간 자산(암호화폐·환율·선물)은 세션 개념이 없어 이 판정에서 뺀다.
    """
    chg_val:    Optional[float]
    chg_pct:    Optional[float]
    as_of:      Optional[pd.Timestamp]   # 집계에 쓰인 가장 늦은 기준일
    counted:    int                      # 변동률을 구한 종목 수
    holdings_n: int                      # 구해야 했던 종목 수 (CASH 제외, 수량>0)
    stale:      tuple[str, ...]          # 자기 시장 기준 뒤처진 종목


def _market_now(ticker: str, now: Optional[datetime]) -> datetime:
    """티커가 상장된 거래소 기준 현재 시각.

    이 모듈은 '오늘'을 `now_et()` 로만 정의하고 있었다. 한국 종목에는 틀린다.
    KRX 정규장 09:00~15:30 KST 는 ET 로 20:00~02:30 이라, **한국장이 열려
    있는 동안 ET 날짜는 하루 뒤처져 있다.**

    그래서 장중 실시간 가격이 들어오면 `daily_change` 가 '오늘 이전의 마지막
    종가'를 ET 날짜로 잘랐고, 어제(KST) 종가가 '오늘'로 분류돼 전일 종가에서
    빠졌다. 실측 (2026-09-11 10:35 KST, 005930.KS):

        프레임   09-08 269,500 · 09-09 269,500 · 09-10 269,000
        실시간   258,500
        결과     prev_close=269,500 (09-09)  chg=-4.08%   as_of=09-10
        정답     prev_close=269,000 (09-10)  chg=-3.90%   as_of=09-11

    이틀치 변동을 오늘 등락으로 부르고, 실시간 가격에 어제 날짜를 붙였다.
    차이가 작아 보이는 건 이틀이 비슷했기 때문이고, 구조는 매일 틀린다.
    """
    tz = KST if uses_kr_session_calendar(ticker) else ET
    if now is None:
        return now_kst() if tz is KST else now_et()
    if now.tzinfo is None:
        # naive 는 어느 시간대인지 알 수 없다. 임의로 붙이면 조용히 하루를
        # 밀 수 있으므로 그대로 돌려주고 호출자의 의도를 존중한다.
        return now
    return now.astimezone(tz) if tz is not None else now


class DailyChange(NamedTuple):
    price:      float              # 현재가 (장중이면 실시간, 아니면 마지막 확정 종가)
    prev_close: float              # 직전 거래일 종가
    chg_val:    float
    chg_pct:    float
    as_of:      pd.Timestamp       # price 의 기준 날짜
    prev_as_of: Optional[pd.Timestamp]
    is_live:    bool               # 장중 실시간 가격 사용 여부


def _us_trading_day_mask(idx: pd.DatetimeIndex) -> np.ndarray:
    """각 날짜가 NYSE 정규 거래일인지 (주말·공휴일 제외) — 인덱스 단위로 한 번에.

    `is_us_trading_day` 를 날짜마다 부르면 DatetimeIndex 를 파이썬으로 순회하며
    Timestamp 를 하나씩 만든다. 보유 종목 상세는 이걸 종목마다 반복하므로
    60종목 × 2500일 = 호출 15만 번이 되고, 그것이 그 화면 시간의 대부분이었다.

    판정 규칙은 `is_us_trading_day` 와 같다 — 주말이거나 그 해 NYSE 휴장일
    집합에 있으면 거래일이 아니다. 휴장일 집합은 이미 연도별로 캐시돼 있어,
    날짜당 한 번이던 조회가 인덱스에 등장하는 연도당 한 번이 된다.
    """
    if len(idx) == 0:
        return np.zeros(0, dtype=bool)
    # tz-aware 인덱스에서 `Timestamp.date()` 는 그 타임존의 날짜를 준다.
    # tz_localize(None) 이 같은 벽시계 날짜를 주므로 판정이 일치한다.
    days = (idx.tz_localize(None) if idx.tz is not None else idx).normalize()
    years = tuple(sorted(int(y) for y in days.year.unique()))
    return np.asarray((days.dayofweek < 5) & ~days.isin(_holiday_index(years)))


@lru_cache(maxsize=64)
def _holiday_index(years: tuple[int, ...]) -> pd.DatetimeIndex:
    """여러 연도의 NYSE 휴장일을 하나의 DatetimeIndex 로 — 연도 조합 단위 캐시.

    `_holidays_for_year` 자체는 이미 캐시돼 있지만, 그것을 합쳐 DatetimeIndex 를
    만드는 비용은 호출마다 다시 든다. 이 함수는 보유 종목마다 불리고 종목들의
    날짜 범위는 대개 같으므로, 연도 조합으로 캐시하면 그 고정비가 한 번으로 준다.
    DatetimeIndex 는 불변이라 캐시된 객체를 공유해도 안전하다.
    """
    holidays: set = set()
    for year in years:
        holidays |= set(_holidays_for_year(year))
    return pd.DatetimeIndex(sorted(holidays))


def _clean_series(raw_df: pd.DataFrame, ticker: str, now: Optional[datetime]) -> pd.Series:
    """티커의 실제 관측치만 남긴 시계열 반환.

    NaN 제거 → 중복 인덱스 제거 → (미국 주식이면) 비거래일 제거 + 미래 날짜 절단.
    """
    if ticker not in raw_df.columns:
        return pd.Series(dtype=float)

    s = raw_df[ticker].dropna()
    if s.empty:
        return s

    s = s[~s.index.duplicated(keep="last")].sort_index()

    if uses_us_session_calendar(ticker):
        # ① NYSE 비거래일(주말·공휴일) 행 제거 — DB에 유령 행이 남아 있어도 안전
        s = s[_us_trading_day_mask(s.index)]
        # ② 아직 거래가 시작되지 않은 날짜(장전의 오늘) 행 절단
        cutoff = pd.Timestamp(us_price_cutoff(now))
        s = s[s.index.normalize() <= cutoff]
    elif uses_kr_session_calendar(ticker):
        # 한국 종목에는 아무 필터도 없었다 — 미국 캘린더를 못 쓴다는 이유로
        # 분기 전체를 건너뛰었고, 그래서 장전 유령행이 그대로 남았다.
        # KRX 에도 컷오프가 있으니(kr_price_cutoff) 같은 처리를 한다.
        #
        # 휴장일 마스크는 걸지 않는다. KRX 캘린더는 관측 기반이라 소스에
        # 구멍이 나면 멀쩡한 거래일이 빠지는데(2026-09-10 이 실제로 그랬다),
        # 그걸 마스크로 쓰면 있는 종가를 지운다. 쓰기 쪽은 save_prices_to_db
        # 가 이미 막고 있으므로 읽기에서 한 번 더 지울 이유가 없다.
        cutoff = pd.Timestamp(kr_price_cutoff(_market_now(ticker, now)))
        s = s[s.index.normalize() <= cutoff]

    return s


def daily_change(
    raw_df: pd.DataFrame,
    ticker: str,
    live_price: Optional[float] = None,
    now: Optional[datetime] = None,
) -> Optional[DailyChange]:
    """티커 하나의 일변동. 관측치가 2개 미만이면 None.

    live_price 가 주어지면(장중) 그것을 현재가로 쓰고, 직전 종가는
    **오늘 이전의 마지막 실제 종가**를 사용한다.
    """
    s = _clean_series(raw_df, ticker, now)
    if s.empty:
        return None

    n = _market_now(ticker, now)

    if live_price is not None and live_price > 0:
        today = pd.Timestamp(n.date())
        prior = s[s.index.normalize() < today]
        if prior.empty:
            return None
        prev_close = float(prior.iloc[-1])
        if prev_close <= 0:
            return None
        chg_val = float(live_price) - prev_close
        return DailyChange(
            price=float(live_price),
            prev_close=prev_close,
            chg_val=round(chg_val, 4),
            chg_pct=round(chg_val / prev_close * 100, 4),
            as_of=today,
            prev_as_of=prior.index[-1],
            is_live=True,
        )

    if len(s) < 2:
        return None

    price      = float(s.iloc[-1])
    prev_close = float(s.iloc[-2])
    if prev_close <= 0:
        return None

    chg_val = price - prev_close
    return DailyChange(
        price=price,
        prev_close=prev_close,
        chg_val=round(chg_val, 4),
        chg_pct=round(chg_val / prev_close * 100, 4),
        as_of=s.index[-1],
        prev_as_of=s.index[-2],
        is_live=False,
    )


def last_price(
    raw_df: pd.DataFrame,
    ticker: str,
    live_price: Optional[float] = None,
    now: Optional[datetime] = None,
) -> Optional[float]:
    """관측치가 1개뿐이어서 변동률을 못 구하는 경우에도 쓸 수 있는 현재가.

    daily_change() 가 None 을 반환할 때 current_price 를 0 으로 떨어뜨리면
    시가총액·비중·손익이 전부 왜곡되므로, 가격만은 항상 별도로 확보한다.
    """
    if live_price is not None and live_price > 0:
        return float(live_price)
    s = _clean_series(raw_df, ticker, now)
    if s.empty:
        # 캘린더 필터로 전부 잘려나간 경우 원본의 마지막 값이라도 반환
        if ticker in raw_df.columns:
            col = raw_df[ticker].dropna()
            if not col.empty:
                return float(col.iloc[-1])
        return None
    return float(s.iloc[-1])


def _is_behind_own_market(
    ticker: str, as_of: pd.Timestamp, now: Optional[datetime],
) -> bool:
    """이 종목의 기준일이 **자기 시장의** 마지막 확정 세션보다 뒤처졌는지.

    서로의 `as_of` 를 비교하지 않는다. 미국·한국을 함께 보유하면 두 시장의
    마지막 확정 세션이 원래 다르다 — KST 오전이면 미국 종목은 전날, 한국
    종목은 오늘이 정상이다. 서로 비교하면 혼합 포트폴리오가 항상 "섞였다" 로
    뜬다.

    `<` 로 본다. 장중에는 실시간 가격이 주입돼 `as_of` 가 마지막 확정 세션보다
    **앞설** 수 있고 그건 정상이다.

    24시간 자산(암호화폐·환율·선물)은 세션 개념이 없어 판정하지 않는다.
    """
    from backend.services.market_calendar import (
        last_completed_kr_session, last_completed_session,
        uses_kr_session_calendar, uses_us_session_calendar,
    )
    try:
        if uses_kr_session_calendar(ticker):
            expected = last_completed_kr_session(_market_now(ticker, now))
        elif uses_us_session_calendar(ticker):
            expected = last_completed_session(_market_now(ticker, now))
        else:
            return False
        return as_of.date() < expected
    except Exception:
        # 캘린더를 못 읽으면 "뒤처졌다" 고 단정하지 않는다 — 없는 결손을
        # 만드는 것이 빠뜨리는 것보다 나쁘다.
        return False


def portfolio_daily_change(
    holdings: dict,
    raw_df: pd.DataFrame,
    live: Optional[dict] = None,
    now: Optional[datetime] = None,
) -> PortfolioChange:
    """포트폴리오 전체 일변동 (금액, %, 기준일) + 그 값의 근거.

    각 종목의 실제 변동액을 합산한다 — 화면에 보이는 종목별 행의 합과
    정의상 일치한다. 현금은 분모(기준 자산)에만 포함되고 변동에는 기여하지 않는다.
    `chg_val is None` 은 계산 가능한 종목이 하나도 없다는 뜻이다.

    `counted`·`holdings_n`·`stale` 은 **임계값 없이 사실만** 싣는다. "몇 %
    미만이면 못 믿는가" 는 표시 계층의 결정이고, 여기서 정하면 근거 없는 상수가
    하나 더 생긴다.
    """
    live = live or {}
    chg_val = 0.0
    base    = 0.0
    as_of: Optional[pd.Timestamp] = None
    counted = 0
    holdings_n = 0
    stale: list[str] = []

    for t, info in holdings.items():
        if t == "CASH":
            continue
        try:
            qty = float(info.get("q", 0) or 0)
        except (TypeError, ValueError):
            continue
        if qty == 0:
            continue
        holdings_n += 1

        dc = daily_change(raw_df, t, live.get(t), now)
        if dc is None:
            # 변동률을 못 구해도 보유 가치는 분모에 반영해 %가 과장되지 않게 한다
            p = last_price(raw_df, t, live.get(t), now)
            if p:
                base += p * qty
            continue

        chg_val += dc.chg_val * qty
        base    += dc.prev_close * qty
        counted += 1
        if _is_behind_own_market(t, dc.as_of, now):
            stale.append(t)
        if as_of is None or dc.as_of > as_of:
            as_of = dc.as_of

    if counted == 0:
        return PortfolioChange(None, None, None, 0, holdings_n, tuple(stale))

    try:
        cash = float(holdings.get("CASH", {}).get("q", 0) or 0)
    except (TypeError, ValueError):
        cash = 0.0
    base += cash

    if base <= 0:
        return PortfolioChange(None, None, as_of, counted, holdings_n, tuple(stale))
    return PortfolioChange(
        round(chg_val, 2), round(chg_val / base * 100, 4), as_of,
        counted, holdings_n, tuple(stale),
    )

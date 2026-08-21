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
from typing import NamedTuple, Optional

import pandas as pd

from backend.services.market_calendar import (
    is_us_trading_day,
    now_et,
    us_price_cutoff,
    uses_us_session_calendar,
)


class DailyChange(NamedTuple):
    price:      float              # 현재가 (장중이면 실시간, 아니면 마지막 확정 종가)
    prev_close: float              # 직전 거래일 종가
    chg_val:    float
    chg_pct:    float
    as_of:      pd.Timestamp       # price 의 기준 날짜
    prev_as_of: Optional[pd.Timestamp]
    is_live:    bool               # 장중 실시간 가격 사용 여부


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
        mask = pd.Series([is_us_trading_day(ts.date()) for ts in s.index], index=s.index)
        s = s[mask]
        # ② 아직 거래가 시작되지 않은 날짜(장전의 오늘) 행 절단
        cutoff = pd.Timestamp(us_price_cutoff(now))
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

    n = now or now_et()

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


def portfolio_daily_change(
    holdings: dict,
    raw_df: pd.DataFrame,
    live: Optional[dict] = None,
    now: Optional[datetime] = None,
) -> tuple[Optional[float], Optional[float], Optional[pd.Timestamp]]:
    """포트폴리오 전체 일변동 (금액, %, 기준일).

    각 종목의 실제 변동액을 합산한다 — 화면에 보이는 종목별 행의 합과
    정의상 일치한다. 현금은 분모(기준 자산)에만 포함되고 변동에는 기여하지 않는다.
    반환 (None, None, None) 은 계산 가능한 종목이 하나도 없다는 뜻이다.
    """
    live = live or {}
    chg_val = 0.0
    base    = 0.0
    as_of: Optional[pd.Timestamp] = None
    counted = 0

    for t, info in holdings.items():
        if t == "CASH":
            continue
        try:
            qty = float(info.get("q", 0) or 0)
        except (TypeError, ValueError):
            continue
        if qty == 0:
            continue

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
        if as_of is None or dc.as_of > as_of:
            as_of = dc.as_of

    if counted == 0:
        return None, None, None

    try:
        cash = float(holdings.get("CASH", {}).get("q", 0) or 0)
    except (TypeError, ValueError):
        cash = 0.0
    base += cash

    if base <= 0:
        return None, None, as_of
    return round(chg_val, 2), round(chg_val / base * 100, 4), as_of

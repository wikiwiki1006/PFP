"""
services/portfolio_calculator.py
──────────────────────────────────
포트폴리오 계산 로직. alpha_terminal.py에서 추출.
Streamlit / yfinance 의존 없음 — 순수 NumPy/Pandas.
"""
from __future__ import annotations

import logging
import math

from typing import NamedTuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def _safe_or(v, default: float) -> float:
    """NaN/Inf/None → default. JSON-safe 숫자 보장.

    **`default` 에 기본값을 두지 않는다.** 이름이 `_safe` 이고 `default=0.0` 이던
    동안, 호출 33곳 중 30곳이 그 기본값에 의존했다. 그러면 "0 이 참값이라고
    판단한 자리" 와 "그냥 굴러온 자리" 가 구별되지 않는다 — §1.3 위반이 숨는 곳이
    정확히 그 구별의 부재다. 각 호출부가 무엇을 원하는지 적게 한다.

    같은 이름이 `routers/ticker.py` 에는 `default=None` 으로 있었고
    `portfolio_optimizer` 에는 둘째 인자가 **자릿수**인 동명 함수가 있었다
    (지금은 `_round_or_none`). 이름을 `_safe_or` 로 바꾼 것은 그 셋을 구별하기
    위한 것이기도 하다.

    값이 없을 때 **아무 값도 만들고 싶지 않으면** `_num_or_none` 을 쓴다.
    """
    try:
        f = float(v)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def _num_or_none(v) -> "float | None":
    """숫자면 float, 아니면 None. **지어낸 값을 넣지 않는다.**

    퍼센트 자리에 쓴다. `_safe_or(x, 0.0)` 은 거기서 "계산 불가" 를 "보합" 으로
    바꾸는데, 그 둘은 사용자에게 전혀 다른 말이다 (CLAUDE.md §1.3). 응답은
    `SafeJSONResponse` 를 지나 `null` 이 되고 화면은 `—` 를 그린다.

    금액·수량처럼 0 이 참값일 수 있는 자리는 `_safe_or` 를 쓴다 — 어느 쪽이
    맞는지는 자리마다 다르고, 그 판단을 호출부가 하도록 두 함수를 나눠 뒀다.
    """
    f = _safe_or(v, float("nan"))
    return None if math.isnan(f) else f


def _round_keep_none(v, digits: int) -> "float | None":
    """반올림하되 `None` 은 그대로 둔다 — `round(None)` 은 TypeError 다.

    이름을 `_round_or_none` 으로 하지 않은 이유가 있다. `portfolio_optimizer` 에
    같은 이름의 함수가 있고 그쪽은 숫자가 아닌 값도 None 으로 바꾼다. 같은
    이름에 다른 동작을 두는 것이 이 리포에서 반복해 사고를 낸 형태다.
    """
    return None if v is None else round(v, digits)


def _trim_to_session(close_df: pd.DataFrame, market: str = "US") -> pd.DataFrame:
    """가격 프레임을 **그 시장의 거래일 기준**으로 정리한다.

    예전에는 서버 로컬(KST) 의 오늘 날짜로 인덱스를 연장했다.
    KST 오전은 미국 기준 전날 밤이라, 한국 시각 8/5 오전에 8/5 행이 생기고
    거기에 8/4 종가가 ffill 로 복사된다 → 그래프 마지막 점이 하루 앞당겨진
    가짜 보합 구간으로 보인다 (사용자가 본 '하루씩 밀린' 증상).

    규칙
      · 장중 → 오늘까지 인정. 실시간 가격이 주입돼 있다.
      · 그 외 → 마지막으로 종가가 확정된 거래일까지만.
      · 목표 날짜를 넘는 행은 잘라낸다. 목표 날짜가 비어 있어도 새로 만들지 않는다
        (없는 거래일을 ffill 로 채우면 가짜 보합이 다시 생긴다).

    **시장마다 기준이 다르다.** 예전에는 미국 캘린더로만 잘라서, 한국 포트폴리오의
    가장 최근 거래일이 통째로 사라졌다 — KST 오전은 미국 기준 전날 밤이고,
    미국 공휴일(노동절 등)에 한국이 열린 날도 잘려 나갔다.
    """
    if close_df.empty:
        return close_df
    try:
        if market == "KR":
            from backend.services.market_calendar import kr_price_cutoff
            target = pd.Timestamp(kr_price_cutoff())
        else:
            from backend.services.market_calendar import (
                is_us_market_open, last_completed_session, now_et,
            )
            target = pd.Timestamp(
                now_et().date() if is_us_market_open() else last_completed_session()
            )
    except Exception:
        return close_df

    if not isinstance(close_df.index, pd.DatetimeIndex):
        return close_df
    return close_df[close_df.index.normalize() <= target]


def _price_or_cost(price, cost: float) -> float:
    """시세가 없으면 취득원가로 대체 평가.

    보유 중인 종목의 그날 시세가 NaN 이라고 0원으로 평가하면 에쿼티 곡선에
    절벽이 생기고, TWRR 은 곱셈 누적이라 그 왜곡이 곡선 끝까지 남는다.
    상장 직후 종목처럼 이력을 아무리 백필해도 채울 수 없는 구간이 존재하므로
    데이터 수집을 고쳐도 이 방어는 별도로 필요하다.
    """
    p = _safe_or(price, default=0.0)
    if p > 0:
        return p
    return max(0.0, _safe_or(cost, default=0.0))


def _price_matrix(prices: pd.DataFrame, tickers: list[str]) -> np.ndarray:
    """가격 프레임을 (날짜 × 티커) float 행렬로 한 번에 변환한다.

    순방향 워크는 날짜마다 그 날의 가격 행이 필요한데, `prices.iloc[i]` 는 호출마다
    Series 객체(인덱스 포함)를 새로 만들고 이어지는 `.get(ticker)` 는 라벨 조회다.
    날짜 2,500 × 티커 60 이면 그 부대비용이 실제 계산보다 커진다.
    값과 열 순서는 그대로 두고 조회를 위치 기반으로만 바꾼다.

    숫자로 읽을 수 없는 값은 NaN 으로 남긴다 — `_price_or_cost` 가 NaN 과 0 을
    같은 경로(취득원가 대체 평가)로 처리하므로 결과가 달라지지 않는다.
    """
    sub = prices[tickers]
    if all(pd.api.types.is_float_dtype(d) for d in sub.dtypes):
        return sub.to_numpy(dtype=float, copy=False)
    # object 열이 섞여 있으면 to_numpy(dtype=float) 가 예외를 낸다.
    # build_equity_curve 는 예외를 조용히 폴백으로 흡수하므로, 여기서 죽으면
    # 원인이 보이지 않는 성능 회귀가 아니라 값 회귀가 된다.
    return sub.apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)


# ── 에쿼티 커브 ────────────────────────────────────────────────────────────────

def _empty_curve() -> pd.Series:
    """빈 에쿼티 곡선. **인덱스 타입까지 맞춘다.**

    `pd.Series(dtype=float)` 의 기본 인덱스는 `RangeIndex` 다. 이 곡선을 받는
    쪽은 전부 `curve.index.dayofweek` 로 주말 행을 걸러내므로, 거기서
    `AttributeError: 'RangeIndex' object has no attribute 'dayofweek'` 가 난다.

    빈 곡선이 나오는 경로가 실제로 있다 — `_trim_to_session` 이 프레임을 비우면
    (예: DB 에 확정 종가보다 뒤인 잠정 행만 있는 경우) `close_df` 는 비어 있지
    않은데 곡선만 빈다. 그러면 `calculate_metrics` 의 close_df 가드를 통과한
    뒤 곡선 쪽에서 터져 `/metrics` 가 500 이 된다.
    """
    return pd.Series(dtype=float, index=pd.DatetimeIndex([]))


def build_equity_curve(
    holdings: dict,
    trade_log: list,
    close_df: pd.DataFrame,
    market: str = "US",
) -> pd.Series:
    """
    매매 이력(trade_log)을 처음부터 순방향으로 재생해 날짜별 포트폴리오 가치 산출.
    현금 0 / 보유 0에서 시작 → DEPOSIT·WITHDRAW·BUY·SELL 이벤트만 반영, 역산 없음.
    매도·입금 여부와 관계없이 과거 포인트가 변하지 않는다.
    비거래일(주말·공휴일) 이벤트는 다음 거래일에 자동 적용.
    """
    if close_df.empty:
        return _empty_curve()

    # 미국 거래일 기준으로 정리 (KST 오늘로 연장하면 하루 밀린 가짜 행이 생긴다)
    close_df = _trim_to_session(close_df, market)
    if close_df.empty:
        return _empty_curve()

    prices = close_df.ffill()
    idx    = close_df.index

    def _fallback() -> pd.Series:
        tickers = [t for t in holdings if t != "CASH" and t in prices.columns]
        cash    = float(holdings.get("CASH", {}).get("q", 0))
        sv = prices[tickers].multiply(
            [holdings[t]["q"] for t in tickers]
        ).sum(axis=1) if tickers else pd.Series(0.0, index=idx)
        return sv + cash

    if not trade_log:
        return _fallback()

    try:
        log_df = pd.DataFrame(trade_log)
        log_df["date"] = pd.to_datetime(log_df["date"]).dt.normalize()
        sort_keys = ["date", "id"] if "id" in log_df.columns else ["date"]
        log_df = log_df.sort_values(sort_keys).reset_index(drop=True)

        # 티커 열을 한 번만 대문자 문자열로 정규화한다. `iterrows()` 는 행마다
        # Series 를 만들므로 이력 300건이면 그것만으로 객체 300개이고, 아래에서
        # 같은 프레임을 세 번 훑고 있었다.
        tickers_up = log_df["ticker"].astype(str).str.upper()

        # 현재 보유 + 과거 매매 이력에 등장한 모든 종목을 추적
        traded_tickers  = set(tickers_up[~tickers_up.isin(["CASH", ""])])
        current_tickers = {t for t in holdings if t != "CASH"}
        all_tickers     = sorted((traded_tickers | current_tickers) & set(prices.columns))

        # 거래 이력 없는 보유 종목은 최초 시점부터 현재 수량으로 고정 (레거시 대응)
        logged_tickers = set(tickers_up)
        static_qty: dict[str, float] = {
            t: float(holdings[t]["q"])
            for t in all_tickers
            if t not in logged_tickers and holdings.get(t, {}).get("q", 0) > 0
        }

        # DEPOSIT 날짜 보정: 주식 BUY보다 늦게 기록된 DEPOSIT은 첫 BUY 날짜로 당겨서 처리
        # (사용자가 현금을 오늘 입력했으나 과거에 매수한 경우 자산 왜곡 방지)
        stock_rows_mask = ~tickers_up.isin(["CASH", ""])
        if stock_rows_mask.any():
            first_trade_date = log_df.loc[stock_rows_mask, "date"].min()
            # `type` 열이 아예 없는 이력도 있다 — 원래 코드의 row.get("type", "")
            # 가 그 경우 빈 문자열이 되어 아무 행도 당기지 않았다.
            if "type" in log_df.columns:
                pull = (
                    (tickers_up == "CASH")
                    & (log_df["type"].astype(str).str.upper() == "DEPOSIT")
                    & (log_df["date"] > first_trade_date)
                )
                if pull.any():
                    log_df.loc[pull, "date"] = first_trade_date

        # 날짜 보정 후 재정렬
        log_df = log_df.sort_values(sort_keys).reset_index(drop=True)

        # 비거래일 이벤트 → 다음 거래일 포지션에 매핑 (정수 인덱스 키)
        # 행을 Series 로 들고 다니면 뒤의 순방향 워크에서 값 접근이 전부 라벨
        # 조회가 된다. dict 로 바꿔 두면 같은 값에 그냥 해시 조회로 닿는다.
        events_by_pos: dict[int, list] = {}
        positions = idx.searchsorted(log_df["date"].to_numpy(), side="left")
        for pos, row in zip(positions, log_df.to_dict("records")):
            pos = int(pos)
            if pos < len(idx):
                events_by_pos.setdefault(pos, []).append(row)

        # 순방향 워크: 현금 0, 보유 0에서 시작
        running_cash: float = 0.0
        running_qty: dict[str, float] = {t: static_qty.get(t, 0.0) for t in all_tickers}
        # 시세가 없는 날 대체 평가에 쓸 취득원가 (매수 시 갱신)
        running_cost: dict[str, float] = {
            t: _safe_or(holdings.get(t, {}).get("avg", 0), 0.0) for t in all_tickers
        }

        equity_vals = np.zeros(len(idx), dtype=float)
        px_mat = _price_matrix(prices, all_tickers)

        for i in range(len(idx)):
            for row in events_by_pos.get(i, []):
                ticker     = str(row.get("ticker", "")).upper()
                trade_type = str(row.get("type",   "")).upper()
                q          = float(row.get("q",     0))
                pr         = float(row.get("price") or 0)

                if ticker == "CASH":
                    if trade_type == "DEPOSIT":
                        running_cash += q
                    elif trade_type == "WITHDRAW":
                        running_cash -= q
                elif ticker in running_qty:
                    if trade_type in ("ADD", "BUY"):
                        prev_q = running_qty[ticker]
                        prev_c = running_cost.get(ticker, 0.0)
                        new_q  = prev_q + q
                        if new_q > 0:
                            running_cost[ticker] = (prev_q * prev_c + q * pr) / new_q
                        running_cash -= q * pr
                        running_qty[ticker] = new_q
                    elif trade_type in ("SOLD", "SELL"):
                        running_cash += q * pr
                        running_qty[ticker] = max(0.0, running_qty[ticker] - q)
                    elif trade_type == "UPDATE":
                        running_qty[ticker] = max(0.0, q)

            row_prices = px_mat[i]
            # 가격이 없는 날(상장 전·데이터 미수집)에 _safe_or(...)→0 으로 평가하면
            # 보유 중인 포지션이 그날만 0원이 되어 곡선에 절벽이 생긴다.
            # 시세가 없으면 취득원가로 이월해 평가한다.
            stock_val = sum(
                _price_or_cost(row_prices[j], running_cost.get(t, 0.0)) * running_qty[t]
                for j, t in enumerate(all_tickers)
            )
            equity_vals[i] = max(0.0, running_cash) + stock_val

        equity = pd.Series(equity_vals, index=idx, dtype=float)
        if equity.iloc[-1] <= 0 or equity.replace(0, np.nan).dropna().empty:
            raise ValueError("비정상 에쿼티 커브")
        return equity

    except Exception:
        return _fallback()


def _trades_by_chart_date(trade_markers, chart_dates: list[str]) -> dict:
    """거래를 차트에 실제로 존재하는 날짜에 붙인다.

    날짜 문자열이 정확히 같은 점에만 붙이면 거래가 조용히 사라진다. 차트에는
    거래일만 남기 때문이다 — 주말 행은 제거되고(장이 안 열려 값이 평평하다),
    자산이 0 이던 앞구간도 잘려 나간다. 그래서 주말·공휴일에 기록한 거래나
    계좌 초기의 거래가 화면에서 몇 개씩 빠져 보였다.

    없는 날짜는 **그 이후 첫 거래일**로 옮긴다. 마지막 날짜보다 뒤면 마지막
    날에, 첫 날짜보다 앞이면 첫 날에 붙인다. 위치가 하루 이틀 밀릴 수는 있어도
    거래가 통째로 없어지는 것보다 낫다.
    """
    from bisect import bisect_left

    by_date: dict = {}
    if not chart_dates:
        return by_date
    ordered = sorted(chart_dates)

    for tr in (trade_markers or []):
        d = str(tr.get("date", ""))
        if not d:
            continue
        if d not in by_date and d not in ordered:
            i = bisect_left(ordered, d)
            d = ordered[i] if i < len(ordered) else ordered[-1]
        by_date.setdefault(d, []).append({
            "ticker": tr.get("ticker", ""),
            "type":   tr.get("type",   ""),
            "q":      tr.get("q",      0),
            "price":  tr.get("price",  0),
        })
    return by_date


# ── 포트폴리오 베타 ────────────────────────────────────────────────────────────

# ── 베타의 판정 기준 — **단일 출처** ──────────────────────────────────────────
#
# 세 곳이 같은 베타를 계산하면서 기준이 달랐다. 실측으로 드러난 결과:
# 관측치 45일 종목 하나를 포트폴리오 베타는 2.036 으로, 종목 상세는 '—' 로 줬다.
# **같은 데이터에 두 화면이 다르게 답하는 것 자체가 결함**이다 (응답이 자기
# 자신과 모순될 이유가 없다). 그래서 값을 여기 한 곳에 둔다 —
# `markets.benchmark_for` 가 지수 선택 규칙을 한 곳에 둔 것과 같은 이유다.
#
#   portfolio_calculator.portfolio_beta_detail        (포트폴리오 베타)
#   quant_metrics.compute_optimizer_context           (종목 상세 베타)
#   portfolio_optimizer._compute_extended_metrics     (최적화 카드 베타)
#
# 60일: 30일은 **오차가 큰 추정치를 확정된 측정값으로** 화면에 내보낸다.
# 45일 관측으로 만든 베타는 틀린 값이 아니라 신뢰구간이 넓은 값인데, 화면은
# 그걸 숫자 하나로 그린다 — §1.3 이 막으려는 방향과 반대다. 엄격해지는 비용
# (지금 값이 보이던 일부가 '—' 가 된다)은 `beta_counted`·`beta_holdings`·
# `beta_value_share` 가 빠진 양을 응답에 싣게 되면서 사라졌다.
#
# 1e-12: 실데이터에서 두 하한(1e-8·1e-12)은 **출력이 같다** — 200종목 × 창
# 3개 = 600개 표본 전부 분산이 1e-8 을 넘었고 최솟값이 2.902e-05(USDKRW=X)로
# 하한의 약 3,000배다. 차이가 나는 것은 "60일 중 한 번만 1틱 움직인 계열"
# (분산 1.7e-10) 같은 입력인데 실데이터에 없다. 그래서 값의 우열이 아니라
# 일관성으로 정했다 (셋 중 둘이 이미 1e-12).
BETA_MIN_OVERLAP = 60      # 벤치마크와 겹치는 최소 관측일
BETA_MIN_VARIANCE = 1e-12  # 벤치마크 수익률 분산의 하한 (이하면 베타 정의 안 됨)

# 위험조정비율(샤프·소르티노·칼마)의 분모 하한. **표준편차**용이다 —
# `BETA_MIN_VARIANCE` 는 분산용이라 단위가 다르다 (분산 1e-12 ↔ 표준편차 1e-6).
# 그대로 돌려쓰면 1,000,000배 느슨해진다.
#
# 값보다 **한 곳에 두는 것**이 요점이다. 같은 식이 네 자리에 있었고 하한이
# 셋으로 갈려 있었다 (`> 0` · `> 1e-12` · 프론트에도 `> 0`). 실측으로는 셋이
# 같은 답을 낸다: 가격이 완전히 상수면 Ledoit-Wolf 공분산에서 vol 이 **정확히
# 0.0** 이고, 300일 중 한 번 1틱만 움직인 계열은 vol 4.583e-05 로 세 하한을
# 모두 통과한다. 갈리는 입력이 없으므로 값의 우열이 아니라 일관성으로 정한다.
MIN_VOL_FOR_RATIO = 1e-12


class PortfolioBeta(NamedTuple):
    """포트폴리오 베타와 **그 값이 무엇을 덮는지**.

    가중평균은 부분 정보로도 그럴듯한 숫자를 낸다. 근거 없이 값만 내보내면
    "10종목 중 1종목만 측정된 베타" 와 "전 종목이 측정된 베타" 가 응답에서
    구별되지 않는다 — `change_counted` 와 같은 이유로 사실을 함께 싣는다.
    """
    beta:        float | None
    counted:     int            # 실제로 베타를 계산한 종목 수
    holdings_n:  int            # 베타 대상 종목 수 (CASH 제외)
    value_share: float | None   # 측정된 종목이 주식 평가액에서 차지하는 비중


def calculate_portfolio_beta(
    holdings: dict,
    close_df: pd.DataFrame,
    benchmark: str,
) -> "float | None":
    """포트폴리오 베타 (값만). 근거까지 필요하면 `portfolio_beta_detail` 을 쓴다."""
    return portfolio_beta_detail(holdings, close_df, benchmark).beta


def portfolio_beta_detail(
    holdings: dict,
    close_df: pd.DataFrame,
    benchmark: str,
) -> PortfolioBeta:
    """포트폴리오 베타. 계산할 수 없으면 beta=None.

    예전에는 어떤 이유로든 못 구하면 1.0 을 돌려줬다. 1.0 은 '시장과 똑같이
    움직인다'는 뜻이라 '모른다'와 전혀 다른 값인데, 5일치 프레임으로 부른
    AI 피드백에서 **모든 종목이 관측치 부족(<30)으로 1.0** 이 되어 포트폴리오
    베타가 항상 1.00 으로 보고됐다. 화면 상단 지표는 긴 구간을 써서 제대로
    나오는데 피드백만 1.00 이라 서로 어긋났다.

    `benchmark` 에 기본값을 두지 않는다. `"^GSPC"` 가 기본값이던 동안 한국
    포트폴리오의 베타도 **S&P 500 대비**로 계산됐다 (§1.1). 실측값 0.2306 은
    "한국 주식이 S&P 를 안 따라간다" 는 뜻인데 화면 라벨은 "베타" 뿐이라
    사용자는 "내 포트폴리오는 방어적이다" 로 읽는다 — 계산은 맞고 질문이
    틀렸으며, 그게 맞는 답처럼 제시됐다. 빠뜨리면 TypeError 가 나게 둔다.

    **못 구한 종목은 가중평균에서 빼고 남은 것으로 재정규화한다.** 예전에는
    관측치가 30일 미만인 종목에 `beta_t = 1.0` 을 지어내 섞었다. 실측: 다섯
    종목의 진짜 베타가 전부 2.0 인데 넷이 관측치 20일이면 포트폴리오 베타가
    **1.194** 로 나왔다 (측정 가능한 종목만 쓰면 1.995). `measured == 0` 만
    막았으므로 9/10 이 지어내진 값이어도 통과했다.

    현금은 분모에 남는다 — 현금의 시장 노출이 0 인 것은 **지어낸 값이 아니라
    사실**이다.
    """
    empty = PortfolioBeta(None, 0, 0, None)
    try:
        stock_tickers = [t for t in holdings if t != "CASH" and t in close_df.columns]
        if benchmark not in close_df.columns:
            return PortfolioBeta(None, 0, len(stock_tickers), None)
        mkt_ret = close_df[benchmark].pct_change().dropna()
        mkt_var = mkt_ret.var()
        # 빈/1행 프레임에서는 `mkt_var` 가 NaN 이고 `NaN <= x` 는 False 라
        # 가드를 통과했다. 그러면 아래 `iloc[-1]` 이 IndexError 를 내고
        # `except` 가 **경고 트레이스백**을 남긴다 — 프레임이 짧은 것은 이제
        # 정상 상태이므로 조용히 '없음' 으로 끝내야 한다.
        if mkt_ret.empty or not math.isfinite(float(mkt_var)) \
                or mkt_var <= BETA_MIN_VARIANCE:
            return PortfolioBeta(None, 0, len(stock_tickers), None)

        if not stock_tickers:
            return empty

        # ffill 후 마지막 행을 쓴다 — 희소 프레임이 넘어오면 마지막 행이 NaN 일 수 있고,
        # NaN 은 `total_val <= 0` 비교를 통과해(비교 결과가 항상 False) 그대로 전파된다.
        latest = close_df.ffill().iloc[-1]
        values, betas = [], []       # 측정된 종목만
        all_values = []              # 전 종목 (value_share 의 분모)
        measured = 0                 # 실제로 베타를 계산해 낸 종목 수
        for t in stock_tickers:
            s_ret = close_df[t].pct_change().dropna()
            common = s_ret.index.intersection(mkt_ret.index)
            px_t = float(latest.get(t, 0) or 0)
            if not math.isfinite(px_t):
                px_t = 0.0
            all_values.append(px_t * _safe_or(holdings[t]["q"], 0.0))
            beta_t = None
            if len(common) < BETA_MIN_OVERLAP:
                beta_t = None
            else:
                # 공분산과 분산을 같은 표본(common)에서 계산해야 베타가 성립한다.
                # 분모만 전체 벤치마크 구간을 쓰면 표본이 어긋나 베타가 왜곡된다.
                mv = float(mkt_ret.loc[common].var())
                if mv > BETA_MIN_VARIANCE:
                    cov = np.cov(s_ret.loc[common], mkt_ret.loc[common])[0, 1]
                    beta_t = cov / mv
                    if math.isfinite(beta_t):
                        measured += 1
                    else:
                        beta_t = None
            if beta_t is None:
                continue                     # 못 구한 종목은 가중평균에서 뺀다
            values.append(all_values[-1])
            betas.append(beta_t)

        stock_total = sum(all_values)
        value_share = (sum(values) / stock_total
                       if stock_total > 0 and math.isfinite(stock_total) else None)
        holdings_n = len(stock_tickers)

        cash_val = _safe_or(holdings.get("CASH", {}).get("q", 0), 0.0)
        # 분모는 **측정된 주식 + 현금**이다. 현금의 시장 노출 0 은 사실이고,
        # 측정 못 한 주식은 0 이 아니라 **모르는** 값이라 분모에서도 뺀다.
        total_val = sum(values) + cash_val
        if measured == 0 or not math.isfinite(total_val) or total_val <= 0:
            # 예전에는 `total_val <= 0` 에서 1.0 을 돌려줬다 — 값이 하나도
            # 없는데 '시장과 똑같이 움직인다' 를 지어내는 자리였다.
            return PortfolioBeta(None, measured, holdings_n, value_share)

        weighted = sum(v * b for v, b in zip(values, betas)) / total_val
        return PortfolioBeta(float(np.clip(weighted, -2.0, 3.0)),
                             measured, holdings_n, value_share)
    except Exception:
        logger.warning("포트폴리오 베타 계산 실패 — 베타만 빠진다", exc_info=True)
        return empty


# ── 실현손익 (매도로 확정된 손익) ──────────────────────────────────────────────

class RealizedPnL(NamedTuple):
    """매도로 확정된 손익. `pnl is None` 은 **계산 불가**이고 `0.0` 은 '실현한 게 없다'."""
    pnl:    float | None
    cost:   float | None    # 매도된 주식의 취득원가 합 (pnl 의 분모)
    pct:    float | None
    reason: str | None      # pnl 이 None 인 이유 코드. 값이 있으면 None
    sales:  int             # 실제로 계산에 들어간 매도 건수


# 실현손익을 계산할 수 없는 이유. 문장이 아니라 코드로 싣는다 — 문구는 표시
# 계층의 결정이다.
_RPNL_NO_LOG      = "no_trade_log"       # 호출자가 이력을 주지 않았다 (없는 것과 다르다)
_RPNL_NO_BASIS    = "no_cost_basis"      # 매수 기록 없이 매도가 있다 (원가를 모른다)
_RPNL_NO_SELL_PX  = "missing_sale_price" # 매도 단가가 비어 있다
_RPNL_NO_BUY_PX   = "missing_buy_price"  # 매수 단가가 비어 있어 평단을 못 만든다


def realized_pnl_from_log(trade_log: list | None) -> RealizedPnL:
    """매매 이력을 재생해 **확정된** 손익을 낸다.

    `total_return_pct` 는 취득원가 대비 현재 평가액이라 **매도로 실현한 손익이
    들어오지 않는다** (현금에 남고, 현금은 그 계산에서 빠진다). 매도 이력이
    많은 사용자에게는 "누적 수익" 이 과소 표시된다. 그 답이 이 필드다 — 옆의
    두 숫자와 기준이 다른 필드를 만드는 것이 아니라, 빠진 조각을 따로 싣는다.

    평단은 매도 시점마다 달라지므로 이력을 순서대로 재생해야 한다. 관례는
    `cash_ledger._recalc_holding` 과 같은 **이동평균**이다:
      · 매수 — 수량·원가 누적
      · 매도 — 그 시점 평단으로 손익 확정, 원가를 매도 비율만큼 차감
      · UPDATE — 수량 정정. **주당 평단을 보존한다** (`build_equity_curve` ·
        `build_return_pct_curve` 와 같은 관례).

    세 상태를 구별한다 (`calculate_metrics` 의 `trade_log` 와 같은 규약):
        None    호출자가 안 알려줬다 → pnl None + reason `no_trade_log`
        []      이력이 실제로 없다   → pnl 0.0 (실현한 게 없다는 뜻이다)
        [...]   재생한다
    """
    if trade_log is None:
        return RealizedPnL(None, None, None, _RPNL_NO_LOG, 0)

    # 날짜·id 순. 순서가 틀리면 평단이 틀린다 (`cash_ledger` 와 같은 정렬).
    trades = sorted(trade_log, key=lambda t: (str(t.get("date", "")), t.get("id", 0)))

    qty: dict[str, float] = {}
    cost: dict[str, float] = {}
    no_px: set[str] = set()      # 매수 단가가 비어 평단을 못 만든 종목
    realized = 0.0
    realized_cost = 0.0
    sales = 0
    reason: str | None = None

    for tr in trades:
        ticker = str(tr.get("ticker", "")).upper()
        if not ticker or ticker == "CASH":
            continue                      # 입·출금은 실현손익이 아니다
        ttype = str(tr.get("type", "")).upper()
        try:
            q = float(tr.get("q") or 0)
        except (TypeError, ValueError):
            continue
        raw_px = tr.get("price")
        # `or 0` 을 쓰지 않는다 — 단가 없음과 0원이 같아지면 100% 손실을 지어낸다.
        try:
            px = float(raw_px) if raw_px is not None else None
        except (TypeError, ValueError):
            px = None

        if ttype in ("ADD", "BUY"):
            if px is None:
                no_px.add(ticker)
            qty[ticker]  = qty.get(ticker, 0.0) + q
            cost[ticker] = cost.get(ticker, 0.0) + (px or 0.0) * q

        elif ttype in ("SOLD", "SELL"):
            held = qty.get(ticker, 0.0)
            if held <= 0:
                reason = reason or _RPNL_NO_BASIS
                continue
            if ticker in no_px:
                reason = reason or _RPNL_NO_BUY_PX
                continue
            if px is None:
                reason = reason or _RPNL_NO_SELL_PX
                continue
            avg  = cost.get(ticker, 0.0) / held
            sold = min(q, held)
            realized      += (px - avg) * sold
            realized_cost += avg * sold
            cost[ticker] = cost.get(ticker, 0.0) - avg * sold
            qty[ticker]  = held - sold
            sales += 1

        elif ttype == "UPDATE":
            held = qty.get(ticker, 0.0)
            avg  = (cost.get(ticker, 0.0) / held) if held > 0 else None
            qty[ticker]  = max(0.0, q)
            # 평단을 알 때만 원가를 다시 만든다. 모르면 0 으로 두고 종목을
            # 표시해 둔다 — 이후 매도에서 `no_cost_basis` 가 아니라 정확히
            # '평단 없음' 으로 갈린다.
            if avg is None:
                no_px.add(ticker)
                cost[ticker] = 0.0
            else:
                cost[ticker] = avg * qty[ticker]

    if reason is not None:
        # 한 건이라도 계산 못 한 매도가 있으면 합계를 내보내지 않는다. 부분
        # 합계는 유한하고 그럴듯해서 어떤 가드에도 걸리지 않는다.
        return RealizedPnL(None, None, None, reason, sales)

    pnl = round(realized, 2)
    rcost = round(realized_cost, 2)
    # 원가가 0 이면 비율의 기준점이 없다 (전량 무상 취득 등). 0% 는 '본전'
    # 이라는 단정이라 쓰지 않는다.
    pct = _num_or_none(realized / realized_cost * 100) if realized_cost > 0 else None
    return RealizedPnL(pnl, rcost, pct, None, sales)


# ── 현재가 (총자산과 보유 목록이 같은 값을 쓰게 하는 단일 출처) ────────────────

# (현재가, 직전종가, 일변동률, 기준일, 실시간여부)
PricedRow = tuple[float, float, "float | None", object, bool]


def _priced_holdings(
    holdings: dict,
    sparse_df: pd.DataFrame | None,
    live: dict | None = None,
    now=None,
    fallback_df: pd.DataFrame | None = None,
) -> tuple[dict[str, PricedRow], set[str]]:
    """보유 종목의 현재가와, 그중 **종가로 채운** 티커 집합.

    어디서도 값을 못 내는 티커는 결과에서 빼고 돌려준다.

    `/metrics` 의 총자산과 `/holdings-detail` 의 행이 같은 가격을 쓰게 하려고
    한 곳에 둔다. 예전에는 총자산만 종가 프레임(`close_df.ffill().iloc[-1]`)에서
    나왔다. 장중에는 실시간이 붙은 행 합계와 종가로 만든 총자산이 갈라져,
    화면 상단 "총 자산" 과 바로 아래 보유 목록의 합이 달랐다 — KRX 장중 실측
    ₩75,960,000 vs ₩74,175,000, **₩1,785,000(2.4%) 차이**. 사용자가 표를
    더해서 검산하는 자리라 어긋나면 바로 보인다.

    실시간 게이트가 티커별로 갈리기 전에는 양쪽이 다 종가라 **우연히** 맞았다.
    한쪽만 실시간이 되면서 벌어진 것이므로, 값을 한 곳에서 만들어 다시 갈라질
    수 없게 한다. 두 함수가 같은 프레임·같은 `live`·같은 `now` 를 받으면
    티커별 가격이 정의상 동일하다.

    `fallback_df` — 희소 프레임에 관측치가 없는 티커를 채울 **ffill 된 종가
    프레임**. 없으면 그 티커는 결과에서 빠지고, 호출자가 가격 0 으로 떨어뜨린다
    (거래정지·상장폐지·수집 실패 종목이 ₩0 행으로 표시되는 형태). 넘기면
    마지막 확정 종가로 채워져 그 행도 실제 금액을 갖는다.
    """
    from backend.services.price_series import daily_change, last_price

    out: dict[str, PricedRow] = {}
    filled: set[str] = set()
    live = live or {}
    have_sparse = sparse_df is not None and not sparse_df.empty
    have_fallback = fallback_df is not None and not fallback_df.empty
    if not have_sparse and not have_fallback:
        return out, filled

    for t in holdings:
        if t == "CASH":
            continue
        dc = daily_change(sparse_df, t, live.get(t), now) if have_sparse else None
        if dc is not None:
            out[t] = (dc.price, dc.prev_close, dc.chg_pct, dc.as_of, dc.is_live)
            continue
        # 관측치가 1개뿐이라 변동률을 못 구해도 가격은 확보한다 — 여기서
        # 빠뜨리면 시가총액·비중·손익이 전부 왜곡된다.
        p = last_price(sparse_df, t, live.get(t), now) if have_sparse else None
        if p:
            out[t] = (p, p, None, None, bool(live.get(t)))
            continue
        if have_fallback and t in fallback_df.columns:
            # 종가 프레임으로 채운 가격. 일변동률·기준일은 **모른다** —
            # 0.0 을 넣으면 '보합'이 되므로 None 으로 남긴다 (§1.3).
            fv = _safe_or(fallback_df[t].iloc[-1], 0.0)
            if fv:
                out[t] = (fv, fv, None, None, False)
                filled.add(t)
    return out, filled


# ── 핵심 지표 계산 ─────────────────────────────────────────────────────────────

def _market_open_flag(market: str = "US") -> bool:
    """이 시장이 지금 장중인지. 화면의 'LIVE' 배지가 이 값을 쓴다.

    미국 기준으로 고정돼 있어, 한국 포트폴리오에서 한국장이 열려 있을 때는
    배지가 꺼지고 한국이 닫힌 미국장 시간에 켜졌다.
    """
    try:
        if market == "KR":
            from backend.services.market_calendar import is_kr_market_open
            return is_kr_market_open()
        from backend.services.market_calendar import is_us_market_open
        return is_us_market_open()
    except Exception:
        return False


def calculate_metrics(
    holdings: dict,
    close_df: pd.DataFrame,
    equity_curve: pd.Series,
    raw_df: pd.DataFrame | None = None,
    live: dict | None = None,
    now=None,
    market: str = "US",
    trade_log: list | None = None,
) -> dict:
    """포트폴리오 지표.

    close_df — ffill 된 프레임 (에쿼티 곡선·베타·알파용)
    raw_df   — fill=False 희소 프레임 (1일 변동 전용). 없으면 기존 곡선 차분으로 폴백.
    live     — 장중 실시간 가격 {ticker: price}

    total_return_pct 는 **취득원가 대비 현재 평가액**이다. 매도로 실현한
    손익은 들어오지 않는다 — 현금에 남고 현금은 이 계산에서 빠진다. "총
    수익률이 왜 이래" 로 돌아올 자리라 여기 적어 둔다. 실현손익까지 담으려면
    별도 필드가 필요하다.

    trade_log — 매매 이력. **세 상태를 구별한다:**

        None    호출자가 알려주지 않았다. 곡선 기반 값을 그대로 쓴다.
        []      이력이 실제로 없다. 곡선의 시작점은 `build_equity_curve` 의
                `static_qty`(현재 수량을 프레임 첫날부터 적용)로 만든 합성값이다.
        [...]   CASH 아닌 티커의 거래가 있으면 그 시점부터가 실제 이력이다.

    `trade_log or []` 로 넘기면 앞의 두 상태가 합쳐져 구별이 사라진다 —
    그대로 넘겨야 한다.

    지금 이 인자를 읽는 계산은 없다. `total_return_pct` 가 곡선 대신 취득원가
    기준으로 바뀌면서 그 판정이 필요 없어졌기 때문이다(창과 무관하게 성립한다).
    남은 용도는 `perf_1w`·`perf_1m`·`alpha_vs_benchmark` 로, 그 셋은 여전히
    곡선에서 나오므로 곡선이 합성이면 사용자의 성과가 아니라 **바스켓의 창
    수익률**이다. 그 판정을 붙일 때 이 인자를 쓴다.
    """
    # **`{}` 를 돌려주지 않는다.** 예전에는 프레임이 비었거나 행이 2개 미만이면
    # 빈 dict 였고, 그래서 "지표가 없음" 과 "계산 실패" 가 호출자에게 같은
    # 값으로 보였다 (§1.3 의 "실패한 경로의 반환값이 정상 경로와 구별되는가").
    # 그 결과가 화면까지 갔다 — 행이 정확히 1개면 라우터 가드를 통과해 `{}` 가
    # 200 으로 나가고, 프론트는 오류도 "보유 없음" 도 못 띄운 채 조용히 비었다.
    #
    # 행이 1개면 실제로는 **거의 다 계산된다.** 실측: 1행 프레임에서도 26개
    # 키가 180행 대조군과 같은 금액을 냈다 (total_equity 6837.53 · stock_value
    # 1837.53 · total_cost 1654.0 · total_return_pct 11.0961 · today_change_pct
    # 0.6357). 평가액은 `raw_df`/`live` 에서 나오고 곡선 파생값만 못 낸다 —
    # 그 셋(perf_1w·perf_1m·portfolio_beta)은 각자 None 으로 떨어진다.
    # 그걸 통째로 버리고 있었다.
    #
    # 프레임이 아예 비면 `iloc[-1]` 이 IndexError 다. 그 경우도 조기 반환하지
    # 않고 **아래 응답 조립 한 곳으로** 흐르게 한다 (조립을 두 벌 만들면
    # 필드가 늘 때 한쪽만 늘어난다 — 오늘 고친 결함들과 같은 형태다).
    # 주말(토·일) 행 제거 — ffill로 복사된 주말 데이터가 당일 변동률 0%를 만드는 버그 방지
    close_df     = close_df[close_df.index.dayofweek < 5]
    equity_curve = equity_curve[equity_curve.index.dayofweek < 5]

    # 비거래일(주말·공휴일)에 NaN이 생기지 않도록 ffill 적용
    price_df = close_df.ffill()
    # 빈 프레임에서는 '마지막 행' 이 없다. 빈 Series 를 쓰면 `curr.get(t, 0)`
    # 이 0 을 주고 `_price` 가 0.0 으로 떨어진다 — 그 사실은 아래
    # `priced_counted` 가 응답에 싣는다.
    curr = price_df.iloc[-1] if len(price_df) else pd.Series(dtype=float)
    # `prev = price_df.iloc[-2]` 는 지웠다. 1일 변동이 `portfolio_daily_change`
    # primitive 로 옮겨간 뒤 아무도 읽지 않는데, 남겨 두면 다음 사람이 "전일
    # 종가는 여기 있다" 로 읽고 유령(ffill 복제) 행을 전일로 쓰게 된다.

    def _price(t):
        v = curr.get(t, 0)
        return _safe_or(v, 0.0) if t != "CASH" else 1.0

    stock_tickers = [t for t in holdings if t != "CASH" and t in close_df.columns]
    cash_val = holdings.get("CASH", {}).get("q", 0)

    # `total_equity` 의 `0.0` 도 센티넬이다 — 아래 `== 0` 검사가 그걸 받아
    # 에쿼티 곡선의 마지막 값으로 회복한다. 수량이 NaN 이면 합이 NaN 이 되고
    # 그 경로로 넘어간다. (그 회복이 있어서 "수량 NaN → 총수익률 -100%" 는
    # 일어나지 않는다. 의심해서 실측으로 확인했다.)
    # 주식만 본 평가액·원가. 수익률은 이 쌍으로 계산한다 — 현금을 분모에 넣으면
    # "누적 수익" 이 계좌 전체 수익률이 되어 현금 비중만큼 희석된다. 곡선 기준
    # 경로는 TWRR 로 현금흐름을 보정해 현금의 기회비용을 수익률에 반영하지
    # 않으므로, 폴백에 현금을 넣으면 **같은 필드가 경로에 따라 다른 정의**가 된다.
    # (현금 절반을 들고 있던 사용자는 실제 +0.08% 를 +0.04% 로 봤다.)
    #
    # 가격은 `_priced_holdings` 에서 온다 — `/holdings-detail` 의 행과 **같은
    # 출처**다. 종가 프레임(`price_df.iloc[-1]`)으로 내면 장중에 총자산만 어제
    # 종가에 머물러 화면 아래 보유 목록 합계와 어긋난다 (실측 2.4%).
    # `raw_df` 를 안 주는 호출자(analyst-feedback·리포트)는 종가 프레임으로
    # 폴백한다 — 그쪽은 비교할 표가 화면에 없다.
    #
    # 종가 프레임을 `fallback_df` 로 넘긴다 — 희소 프레임에 관측치가 없는
    # 종목까지 총자산에 들어간다. 그걸 빼면 자산이 조용히 줄어든다.
    priced, filled = _priced_holdings(holdings, raw_df, live, now,
                                      fallback_df=price_df)
    if filled and raw_df is not None and not raw_df.empty:
        # `/holdings-detail` 이 종가 프레임을 받지 않으면 그 표는 이 종목을
        # ₩0 행으로 표시하므로, 표 합계와 총자산이 이 금액만큼 어긋난다.
        logger.warning(
            "실시간 프레임에 관측치가 없어 마지막 종가로 채운 보유 종목 %s — "
            "`/holdings-detail` 이 종가 프레임을 받지 않는 동안 그 표는 이 종목을 "
            "0 으로 표시하므로 표 합계와 총자산이 그만큼 어긋난다.",
            sorted(filled),
        )

    def _value_price(t):
        row = priced.get(t)
        return row[0] if row is not None else _price(t)

    # 평가액과 원가는 **같은 종목 집합**을 덮어야 한다. 가격을 못 구한 종목을
    # 분모(원가)에만 넣으면 수익률이 그만큼 손실로 나온다 — 실측: 가격 출처가
    # 하나도 없을 때 `stock_equity 0 / stock_cost 3,154` 로
    # **`total_return_pct: -100.0`** 이 나갔다. "전액 손실" 이라는 단정이다.
    # (예전에는 이 경우가 `{}` → 라우터 400 이라 화면까지 가지 않았다. `{}` 를
    # 없애려면 이 자리를 먼저 고쳐야 한다.)
    #
    # 가격 0 은 이 계산에서 "데이터 없음" 이다 (`_priced_holdings` 의 `or 0.0`
    # 관례). 그래서 0 인 종목은 양쪽에서 빼고, 몇 종목이 빠졌는지를 응답에 싣는다.
    candidates = ([t for t in holdings if t != "CASH" and t in priced]
                  if priced else stock_tickers)
    value_tickers = [t for t in candidates if _value_price(t)]

    # 평가액이 **몇 종목을 덮는가**. 가격을 한 종목도 못 구하면 `total_equity`
    # 는 현금만 담은 값이 되는데, 그 숫자는 유한하고 그럴듯해서 어떤 가드에도
    # 걸리지 않는다 — 400 을 걷어내는 대신 사실을 싣는다
    # (`change_counted` 와 같은 규약).
    held_n = len([t for t in holdings if t != "CASH"])
    priced_n = len(value_tickers)

    stock_equity = _safe_or(sum(_value_price(t) * holdings[t]["q"]
                                for t in value_tickers), 0.0)
    stock_cost   = _safe_or(sum(_safe_or(holdings[t]["avg"], 0.0) * _safe_or(holdings[t]["q"], 0.0)
                                for t in value_tickers), 0.0)

    # 응답의 `total_equity` 는 현금을 포함한 **총자산**이다 (화면 라벨도 그렇다).
    total_equity = _safe_or(stock_equity + cash_val, 0.0)
    total_cost   = stock_cost

    # 보유 종목이 없어도 equity curve 마지막 값을 현재 자산으로 사용
    # (전량 매도 후 현금 보유 또는 CASH 항목 없는 경우 대응)
    eq_last = float(equity_curve.iloc[-1]) if not equity_curve.empty else 0.0
    if total_equity == 0 and eq_last > 0:
        total_equity = eq_last

    # 곡선이 **합성**인지. 매매 이력에 주식 거래가 하나도 없으면
    # `build_equity_curve` 의 `static_qty` 가 현재 수량을 프레임 첫날부터
    # 적용하므로, 곡선은 사용자가 겪지 않은 과거를 그린다. 그 위에서 기간
    # 수익률·알파를 계산하면 **바스켓의 창 수익률**이 사용자의 성과로 나간다.
    #
    # CASH 입금만 있는 이력도 여기 걸린다 — SetupWizard 로 보유만 입력한
    # 사용자가 그 형태다.
    #
    # `trade_log is None` 은 "호출자가 알려주지 않았다" 이므로 판정하지 않는다.
    # 없는 결손을 만드는 것이 빠뜨리는 것보다 나쁘다.
    curve_is_synthetic = False
    if trade_log is not None:
        curve_is_synthetic = not any(
            str(tr.get("ticker", "")).upper() not in ("CASH", "")
            for tr in trade_log
        )

    # 총 수익률: **취득원가 대비 현재 평가액.** 주식만 보고 현금은 양쪽에서 뺀다.
    #
    # 예전에는 에쿼티 곡선의 첫 양수 지점 대비로 계산했다. 그 값은 곡선이
    # 투자 시작 시점을 덮을 때만 총 수익률이고, 넘겨받은 `close_df` 가 짧으면
    # **그 창의 수익률**이 된다. 실측: 평단 165.4 → 현재 155.28 (실제 -6.12%)
    # 인 보유가 5일 창에서 `+55.28%` 로 보고됐다. 창이 100 에서 시작했기 때문이고,
    # 매매 이력이 있든 없든 같았다.
    #
    # 결정적이었던 것은 **같은 응답의 `total_cost`(1654)·`total_equity`(1552.8)
    # 가 -6.12% 를 가리킨다**는 점이다. 창이 짧은 것은 호출자의 정당한 선택이고,
    # 그 창으로 정당화할 수 없는 숫자를 "총 수익률" 이라는 이름으로 내보내는 것이
    # 계산하는 쪽의 문제다. 응답이 자기 자신과 모순될 이유가 없다.
    #
    # 원가 기준은 창과 무관하게 성립하고 옆 두 필드와 정의상 일치한다. 곡선
    # 기준이 맞는 경우(창이 매수 시점에서 시작)에는 두 값이 같다 — 잃는 것이
    # 없다. 다만 **매도로 실현한 손익은 들어오지 않는다** (현금에 남고, 현금은
    # 이 계산에서 빠진다). 그건 별도 필드가 답할 문제다.
    #
    # 원가가 0 이면 기준점이 없다 — 0% 는 "본전" 이라는 단정이다.
    total_rtn = (_num_or_none((stock_equity / stock_cost - 1) * 100)
                 if stock_cost else None)

    # 실현손익. `trade_log` 의 세 상태를 그대로 넘긴다 — `or []` 로 뭉개면
    # "안 알려줬다" 와 "이력이 없다" 가 합쳐져, 이력을 안 받는 호출자
    # (리포트·analyst-feedback)에게 `realized_pnl: 0` 이라는 **단정**이 나간다.
    rpnl = realized_pnl_from_log(trade_log)

    # 1D 변화 — 종목별 '마지막 두 실제 관측치' 합산이 1순위.
    # 에쿼티 커브의 위치 기반 차분(iloc[-1]-iloc[-2])은 마지막 두 행이
    # 유령(ffill 복제) 행이면 정확히 0.0 을 반환하고, 애초에 두 행이
    # 연속된 거래일이라는 보장도 없다. 종목별 합산은 화면의 행 합계와도 일치한다.
    today_chg_val = today_chg_pct = None

    # `as_of` 는 **`total_equity` 의 기준일**이다. 예전에는 일변동 primitive 의
    # 기준일을 그대로 실었는데, 총자산은 어제 종가로 만들면서 일변동은 실시간
    # 가격으로 만들고 있었다 — 09-10 종가로 계산한 총자산에 `as_of: 2026-09-11`
    # 이 붙었다. 지금은 둘이 같은 가격에서 나오므로 한 날짜가 둘 다 설명한다.
    as_of_ts = None
    for _row in priced.values():
        if _row[3] is not None and (as_of_ts is None or _row[3] > as_of_ts):
            as_of_ts = _row[3]
    if as_of_ts is None and len(close_df.index):
        as_of_ts = close_df.index[-1]
    as_of_str = as_of_ts.strftime("%Y-%m-%d") if as_of_ts is not None else None
    # 일변동이 무엇으로 만들어졌는지. 임계값은 두지 않고 사실만 싣는다.
    chg_counted = chg_holdings = 0
    chg_stale: list[str] = []
    if raw_df is not None and not raw_df.empty:
        try:
            from backend.services.price_series import portfolio_daily_change
            pc = portfolio_daily_change(holdings, raw_df, live, now)
            chg_counted, chg_holdings = pc.counted, pc.holdings_n
            chg_stale = list(pc.stale)
            if pc.chg_val is not None:
                today_chg_val = _safe_or(pc.chg_val, 0.0)
                today_chg_pct = _num_or_none(pc.chg_pct)
        except Exception:
            # 실패하면 아래 폴백이 에쿼티 곡선의 마지막 두 점으로 계산한다 —
            # 그 경로는 ffill 로 복제된 유령 행을 구분하지 못해 0% 를 낼 수 있다.
            # 어느 쪽 값이 화면에 떴는지는 이 로그로만 구별된다.
            logger.warning("일변동 primitive 실패 — 에쿼티 곡선 기반 폴백으로 계산",
                           exc_info=True)

    # 폴백: raw_df 를 넘기지 않는 기존 호출자(analyst-feedback, 리포트)는 종전 방식 유지
    if today_chg_val is None:
        if len(equity_curve) >= 2:
            _cur_eq = float(equity_curve.iloc[-1])
            _pre_eq = float(equity_curve.iloc[-2])
            today_chg_val = _safe_or(_cur_eq - _pre_eq, 0.0)
            today_chg_pct = (_num_or_none((_cur_eq / _pre_eq - 1) * 100)
                             if _pre_eq else None)
        else:
            # 관측치가 한 개뿐이면 전일 대비가 정의되지 않는다.
            # today_change_val 은 프론트 타입이 아직 non-nullable 이라 0.0 을
            # 유지한다 (라우팅되는 화면에서는 쓰이지 않는다).
            today_chg_val = 0.0
            today_chg_pct = None

    # 실제 거래일만 추린 인덱스 — 행 개수로 세면 공휴일·합성 today 행이 섞여
    # '5행 전'이 5거래일 전이 아니게 된다 (24/7 자산과 인덱스를 합치면 공휴일 행이 남는다).
    #
    # **그 시장의 캘린더로 센다.** 예전에는 시장과 무관하게 NYSE 로 세서, 한국
    # 포트폴리오의 `perf_1w`·`perf_1m` 기준점이 미국 거래일로 잡혔다. 같은 규칙을
    # 다루는 `_trim_to_session` 은 이미 시장별로 갈려 있었고(그 docstring 이 같은
    # 사고를 기록한다) 이 자리만 안 따라왔다.
    #
    # 실측 (최근 평일 252일, 관측된 KRX 개장일 485일 기준):
    #     두 캘린더가 어긋나는 날 16일 — 한국 공휴일 14일(설·추석·개천절 등)을
    #     미국 캘린더는 거래일로 세고, 미국 공휴일 2일은 한국에서 개장이다.
    #     '5거래일 전' 기준점이 달라지는 날이 1년 중 **80일(31.7%)**.
    try:
        if market == "KR":
            from backend.services.market_calendar import is_kr_trading_day as _is_session
        else:
            from backend.services.market_calendar import is_us_trading_day as _is_session
        _sessions = [d for d in equity_curve.index if _is_session(d.date())]
    except Exception:
        # 캘린더를 못 읽으면 행 전체를 쓴다. 다른 시장 캘린더로 대신 세지 않는다 —
        # 틀린 기준일로 계산한 수익률은 없는 것보다 나쁘다.
        logger.warning("거래일 캘린더를 읽지 못했다 (market=%s) — 곡선 행 전체를 "
                       "기준으로 쓴다. perf_1w·perf_1m 의 기준점이 거래일이 "
                       "아닐 수 있다.", market, exc_info=True)
        _sessions = list(equity_curve.index)

    def _perf(days):
        """N 거래일 수익률. 기준점이 없거나 0이면 None (0.0 으로 위장하지 않는다).

        포트폴리오가 조회 기간보다 짧으면 base 가 0(첫 거래 이전 구간)이라
        예전에는 '이번 주 보합'이라는 잘못된 확신을 표시했다.
        """
        if curve_is_synthetic:
            # 곡선이 합성이면 N 거래일 전 자산은 '그때 이 바스켓의 값' 이고
            # 사용자가 그때 그것을 보유했다는 근거가 없다.
            return None
        if len(_sessions) < days + 1:
            return None
        # 여기서 `0.0` 은 표시값이 아니라 **센티넬**이다 — 바로 아래 `<= 0` 검사가
        # 그걸 받아 None 을 돌려준다. `None` 으로 바꾸면 `None <= 0` 이 TypeError 다.
        # 위 docstring 이 말하는 '위장하지 않는다' 는 이 경로로 구현돼 있다.
        cur = _safe_or(equity_curve.get(_sessions[-1]), 0.0)
        base = _safe_or(equity_curve.get(_sessions[-(days + 1)]), 0.0)
        if base <= 0 or cur <= 0:
            return None
        return (cur / base - 1) * 100

    # 벤치마크는 시장이 정한다 (US `^GSPC` · KR `^KS11`). 어느 지수가 기준인지
    # 아는 코드는 `markets.benchmark_for` 하나다.
    #
    # 지금 KR 에서는 이 값이 None 이 된다 — `/metrics` 프레임을 만드는
    # `routers/portfolio.py` 가 `["^GSPC", "^VIX"]` 를 시장과 무관하게 넣고
    # `include_market=False` 로 불러서, `^KS11` 열이 아예 오지 않는다.
    # 그 라우터가 시장 기준지수를 함께 실어 주면 값이 돌아온다. 그때까지는
    # '—' 가 맞다 — S&P 대비 0.2306 을 "베타" 라고 보여주는 것보다 정직하다.
    from backend.services.markets import benchmark_for, get_market
    bench = benchmark_for(market)
    beta_d = portfolio_beta_detail(holdings, close_df, bench)
    beta = beta_d.beta
    # `.get()` 의 기본값 18.0(VIX 장기 평균)은 **열이 없을 때만** 쓰인다.
    # 열은 있는데 값이 전부 NaN 이면 NaN 이 그대로 나오고, ffill 도 전량 NaN 열은
    # 채우지 못한다 — yfinance 가 ^VIX 를 빈 열로 주는 일이 있다. 그러면 폴백을
    # 두었는데도 화면에는 '—' 가 뜬다 (앱 전역 SafeJSONResponse 가 NaN 을 null 로
    # 바꿔 주므로 요청이 깨지지는 않는다. 그 안전망이 이 누락을 가려 왔다.)
    vix  = _num_or_none(curr.get("^VIX"))

    # 알파도 시장 기준 지수 대비다. 예전에는 `"^GSPC"` 가 하드코딩돼 한국
    # 포트폴리오의 알파가 S&P500 대비로 나갔고, 응답 키 이름까지
    # `alpha_vs_sp500` 이라 계산을 고쳐도 이름이 거짓말을 계속했다.
    #
    # 초기값을 `0.0` 에서 `None` 으로 바꾼다. 기준 지수 열이 없으면 알파는
    # 계산 불가인데 `0.0` 은 "시장과 정확히 같았다" 는 단정이다 (§1.3).
    alpha = None
    if bench in close_df.columns and not curve_is_synthetic:
        try:
            # 에쿼티 곡선은 첫 거래 이전 구간이 0 이므로 iloc[0] 으로 나누면 inf 가 되고,
            # NaN 검사(a_val == a_val)는 inf 를 잡지 못해 alpha 가 항상 null 로 나갔다.
            # 최초 양수 시점(= 실제 투자 시작일)을 기준으로 두 수익률을 맞춘다.
            eq_pos = equity_curve[equity_curve > 0]
            if eq_pos.empty:
                raise ValueError("no positive equity")
            start = eq_pos.index[0]
            eq_w  = equity_curve.loc[start:]
            base  = float(eq_w.iloc[0])

            b_sp = close_df[bench].reindex(equity_curve.index).ffill().bfill().loc[start:]
            b_valid = b_sp.dropna()
            # 창에 점이 하나면 두 수익률이 정의상 0 이라 알파가 **0.0** 으로
            # 나온다 — "시장과 정확히 같았다" 는 단정이다. 실측: 1행 프레임에서
            # `alpha_vs_benchmark: 0.0` 이 나갔다. 창이 없으면 알파도 없다.
            if len(eq_w) < 2 or len(b_valid) < 2:
                raise ValueError("알파를 낼 창이 없다 (점이 2개 미만)")
            if base > 0 and not b_valid.empty and float(b_valid.iloc[0]) != 0:
                p_last = float(eq_w.iloc[-1]) / base - 1
                b_last = float(b_valid.iloc[-1]) / float(b_valid.iloc[0]) - 1
                a_val = (p_last - b_last) * 100
                if math.isfinite(a_val):
                    alpha = round(a_val, 4)
        except Exception:
            # 알파가 비는 정상 경로(기준 지수 열 없음·합성 곡선)는 위에서 이미
            # 갈렸다. 여기까지 온 예외는 **계산이 깨진** 것이므로 남긴다 —
            # 화면의 '—' 만 보고는 둘을 구별할 수 없다.
            logger.debug("알파 계산 실패 — 알파만 빠진다 (market=%s)", market,
                         exc_info=True)

    return {
        # 세 값이 서로 맞아떨어지게 싣는다. 예전에는 `total_equity` 가 현금을
        # 포함하고 `total_cost` 는 제외해서, **`total_return_pct` 를 옆 두
        # 필드로 재현할 수 없었다.** 실측: equity 75,960,000 / cost 12,090,000
        # 으로 계산하면 528.29% 인데 표시값은 429.03% 였다 (표시값이 맞다 —
        # 증권만 본 값이다).
        #
        # `total_equity` 는 그대로 둔다. 화면 라벨이 "총 자산" 이고 현금이
        # 들어가는 것이 사용자 기대이며, `typeof m.total_equity === 'number'`
        # 가 지표 바 전체의 게이트다. 대신 증권·현금을 따로 실어 소비자가
        # 검산할 수 있게 한다:
        #
        #     total_equity == stock_value + cash_value
        #     total_return_pct == stock_value / total_cost - 1
        "total_equity":      round(total_equity, 2),
        "stock_value":       round(stock_equity, 2),
        "cash_value":        round(_safe_or(cash_val, 0.0), 2),
        "total_cost":        round(total_cost, 2),
        # 계산 불가는 null 로 내려간다 — `_round_keep_none` 이 None 을 통과시킨다.
        # 0 으로 바꾸면 '보합'·'본전'·'변동성 낮음' 이라는 단정이 된다 (§1.3).
        "total_return_pct":  _round_keep_none(total_rtn, 4),
        # 실현손익 — `total_return_pct` 에 **없는** 조각이다. 그 값은 취득원가
        # 대비 현재 평가액이라 매도로 확정한 손익이 들어오지 않는다 (현금에
        # 남고 현금은 그 계산에서 빠진다). 둘을 더하면 전체 손익이 된다.
        #
        #     realized_pnl == 0.0   실현한 게 없다 (매도 이력이 없다)
        #     realized_pnl == null  계산 불가 — 이유는 realized_pnl_reason
        #
        # 비율의 분모는 **매도된 주식의 취득원가**(`realized_cost`)다. 투자
        # 원금 전체로 나누면 "닫은 포지션의 수익률" 이 아니라 계좌 전체에
        # 희석된 값이 되어 `total_return_pct` 와 같은 질문에 답하지 못한다.
        "realized_pnl":        rpnl.pnl,
        "realized_cost":      rpnl.cost,
        "realized_pnl_pct":   _round_keep_none(rpnl.pct, 4),
        "realized_pnl_reason": rpnl.reason,
        "realized_sales":     rpnl.sales,
        # `today_change_val` 키는 뺐다. 프론트 계약(types·demoData)에서 develop 이
        # 제거했고 백엔드 소비자도 없는데, 관측치가 한 개뿐이면 `0.0` 을 지어내
        # 내보내고 있었다. 아무도 읽지 않는 값을 위해 위장을 유지할 이유가 없다.
        # 내부 변수는 남는다 — 폴백 분기가 `today_chg_val is None` 으로 갈린다.
        "today_change_pct":  _round_keep_none(today_chg_pct, 4),
        "as_of":             as_of_str,
        # 일변동의 근거. 집계는 부분 정보로도 그럴듯한 숫자를 낸다 — 10종목이
        # 전부 +10% 오른 날 1종목만 계산되면 +0.92% 가 나오고, 유한하고 범위도
        # 그럴듯해서 어떤 가드에도 걸리지 않는다. 그래서 사실을 함께 내보낸다.
        # `change_stale` 은 **자기 시장의** 마지막 확정 세션보다 뒤처진 종목이다
        # (서로의 as_of 를 비교하면 미국·한국 혼합이 항상 섞임으로 뜬다).
        "change_counted":    chg_counted,
        "change_holdings":   chg_holdings,
        "change_stale":      chg_stale,
        "market_open":       _market_open_flag(market),
        # 베타·알파가 무엇에 대비한 값인지 응답에 담는다. 화면이 "베타" 라고만
        # 쓰면 사용자는 벤치마크를 모르고, 한국 포트폴리오에 S&P500 대비 값이
        # 나가도 알아챌 수 없다. 라벨이 이름을 붙일 수 있어야 한다.
        "benchmark":         bench,
        "benchmark_label":   get_market(market).indices.get(bench, bench),
        "portfolio_beta":    _round_keep_none(beta, 4),
        # 베타가 무엇을 덮는 값인지. 가중평균은 부분 정보로도 그럴듯한 숫자를
        # 낸다 — 다섯 종목 중 하나만 측정돼도 값은 유한하고 범위도 그럴듯하다.
        # `beta_value_share` 는 측정된 종목이 주식 평가액에서 차지하는 비중이다
        # (종목 수보다 이 비중이 "이 숫자가 내 포트폴리오를 얼마나 설명하는가"
        # 에 직접 답한다).
        # 평가액이 덮는 종목 수. `priced_counted == 0` 이고 `priced_holdings > 0`
        # 이면 `total_equity` 는 **현금만** 담은 값이다 — 화면이 그걸 총자산으로
        # 그리면 자산이 사라진 것처럼 보인다. 예전에는 이 경우가 `{}` 였다.
        "priced_counted":    priced_n,
        "priced_holdings":   held_n,
        "beta_counted":      beta_d.counted,
        "beta_holdings":     beta_d.holdings_n,
        "beta_value_share":  _round_keep_none(beta_d.value_share, 4),
        "vix":               _round_keep_none(vix, 2),
        "perf_1w":           _round_keep_none(_perf(5), 4),
        "perf_1m":           _round_keep_none(_perf(21), 4),
        # 키 이름에 벤치마크를 박지 않는다 — `alpha_vs_sp500` 은 계산을 고쳐도
        # 이름이 거짓말을 계속했다. 무엇 대비인지는 `benchmark` 가 말한다.
        "alpha_vs_benchmark": _round_keep_none(alpha, 4),
    }


# ── 보유 종목 상세 ─────────────────────────────────────────────────────────────

def get_holdings_detail(
    holdings: dict,
    close_df: pd.DataFrame,
    live: dict | None = None,
    now=None,
    fallback_df: pd.DataFrame | None = None,
) -> list[dict]:
    """보유 종목 상세. close_df 는 fill=False 로 받은 **희소(실제 관측치) 프레임**이어야 한다.

    일변동률은 services/price_series.daily_change 가 계산한다:
      · 장중  — 실시간 가격 vs 직전 거래일 종가
      · 장 외 — 마지막 확정 거래일 종가 vs 그 전 거래일 종가
    주말 행은 여기서 제거하지 않는다 — 암호화폐·환율의 실제 주말 거래를 보존해야 하고,
    미국 주식의 비거래일 필터링은 primitive 가 캘린더 기준으로 수행한다.

    `fallback_df` — 선택. `_portfolio_close_df` 로 만든 **ffill 된 2년 종가
    프레임**을 넘기면, 희소 프레임에 최근 관측치가 없는 종목(거래정지·수집
    실패)도 마지막 확정 종가로 값을 갖는다. 안 넘기면 그 종목은 지금처럼
    가격 0 · 평가액 0 행이 된다 — `calculate_metrics` 는 이 프레임을 항상
    넘기므로, 안 넘기는 동안은 그 종목 금액만큼 표 합계가 총자산보다 작다.
    """
    if close_df.empty and (fallback_df is None or fallback_df.empty):
        return []

    # 가격은 `calculate_metrics` 와 **같은 출처**에서 온다. 한쪽만 실시간이
    # 되면서 "총 자산" 과 이 표의 합계가 갈라진 적이 있다 (실측 2.4%).
    ticker_px, _ = _priced_holdings(holdings, close_df, live, now,
                                    fallback_df=fallback_df)

    total_equity = sum(
        ticker_px.get(t, (0.0,))[0] * _safe_or(info.get("q", 0), 0.0)
        for t, info in holdings.items() if t != "CASH"
    ) + _safe_or(holdings.get("CASH", {}).get("q", 0), 0.0)
    total_equity = _safe_or(total_equity, 0.0)

    rows = []
    for t, info in holdings.items():
        if t == "CASH":
            price, chg_pct, pnl_pct = 1.0, 0.0, 0.0
            as_of, is_live = None, False
        else:
            price, _prev, chg_pct, as_of, is_live = ticker_px.get(t, (0.0, 0.0, None, None, False))
            avg     = _safe_or(info.get("avg", 0), 0.0)
            # 취득원가가 없으면 수익률이 정의되지 않는다 — 0% 로 내려보내면
            # '손익 없음(보합)' 으로 읽힌다. CASH 는 손익 자체가 없어 0.0 이 맞다.
            pnl_pct = _safe_or((price / avg - 1) * 100, 0.0) if avg > 0 else None

        avg_cost = _safe_or(info.get("avg", 0), 0.0)
        qty      = _safe_or(info.get("q", 0), 0.0)
        value    = _safe_or(price * qty, 0.0)
        pnl      = _safe_or((price - avg_cost) * qty, 0.0)

        rows.append({
            "ticker":        t,
            "sector":        info.get("sector", "Other"),
            "qty":           round(qty, 4),
            "avg_cost":      round(avg_cost, 2),
            "current_price": round(price, 2),
            # 변동률을 못 구한 경우 0.0 이 아니라 null — 프론트에서 '—' 로 표시된다.
            # 0.0 으로 내려보내면 '진짜 보합'과 구분되지 않는다.
            "chg_pct":       round(chg_pct, 4) if chg_pct is not None else None,
            "pnl_pct":       round(pnl_pct, 4) if pnl_pct is not None else None,
            "pnl":           round(pnl, 2),
            "market_value":  round(value, 2),
            # 평가액 합이 0 이면 비중이 정의되지 않는다. 이 조건은 전 종목에
            # 동시에 걸리므로 '일부만 null' 인 상태는 생기지 않는다.
            "weight":        round(_safe_or(value / total_equity, 0.0), 4) if total_equity else None,
            "as_of":         as_of.strftime("%Y-%m-%d") if as_of is not None else None,
            "is_live":       bool(is_live),
        })
    return rows


# ── 수익률(%) 커브 ─────────────────────────────────────────────────────────────

def build_return_pct_curve(
    holdings: dict,
    trade_log: list,
    close_df: pd.DataFrame,
    market: str = "US",
) -> "tuple[pd.Series, dict[str, list[dict]], float, dict[str, float], pd.Series]":
    """
    시간가중수익률(TWRR) 누적 커브 반환.
    build_equity_curve 를 재사용하지 않고 직접 순방향 시뮬레이션 (날짜 보정 없음).

    일별 TWRR:  r_t = (V_t - CF_t) / V_{t-1} - 1
        V_t  : 당일 종가 기준 총자산 (입출금 반영 후)
        V_{t-1}: 전날 총자산
        CF_t : 당일 외부 입출금액 (DEPOSIT=양수, WITHDRAW=음수)
    누적:  R_T = ∏(1 + r_t) - 1  (복리 연결)

    이 방식은 외부 현금흐름이 있는 날에도 수익률 왜곡(스파이크) 없이
    순수 운용 성과만 추적한다.
    반환: (return_pct, holdings_by_date, initial_equity, cash_events, equity)

    ## `holdings_by_date` 의 계약

    `holdings[].price is None` 은 **그 종목이 그날 취득원가로 대체 평가됐다**는
    뜻이다 (`_price_or_cost`). 시세를 모르는 날이고, `return_pct` 도 None 이다.

    그래서 `equity` 와 `return_pct` 는 그 날짜에 **관측이 아니라 원가**를
    반영한다. 한 날짜의 모든 종목이 `price is None` 이면 그 날 포트폴리오
    가치는 관측된 적이 없다 — 소비자는 그 사실을 이 필드로만 알 수 있다
    (`return_pct_to_records` 가 그 날짜를 레코드에서 빼는 근거이기도 하다).

    별도 coverage 필드를 두지 않는 이유: 그 사실이 이미 여기 있다. 파생 필드를
    만들면 같은 사실이 두 곳에 생기고 한쪽만 갱신되는 형태가 된다.
    """
    if close_df.empty:
        return _empty_curve(), {}, 0.0, {}, _empty_curve()

    # 미국 거래일 기준으로 정리 (KST 오늘로 연장하면 하루 밀린 가짜 행이 생긴다)
    close_df = _trim_to_session(close_df, market)
    if close_df.empty:
        return _empty_curve(), {}, 0.0, {}, _empty_curve()

    prices = close_df.ffill()
    idx    = close_df.index

    # ── 거래 종목 집합 구성 ───────────────────────────────────────────────────
    current_tickers = {t for t in holdings if t != "CASH"}
    if trade_log:
        log_df = pd.DataFrame(trade_log)
        log_df["date"] = pd.to_datetime(log_df["date"]).dt.normalize()
        sort_keys = ["date", "id"] if "id" in log_df.columns else ["date"]
        log_df = log_df.sort_values(sort_keys).reset_index(drop=True)

        # 주식 거래 이력에서 등장한 티커 수집 (CASH 제외)
        stock_mask = ~log_df["ticker"].str.upper().isin(["CASH", ""])
        traded_tickers = set(log_df.loc[stock_mask, "ticker"].str.upper().tolist())
        all_tickers    = sorted((traded_tickers | current_tickers) & set(prices.columns))

        # 거래 이력 없는 보유 종목 → 최초 시점부터 현재 수량 고정
        logged_tickers = set(log_df.loc[stock_mask, "ticker"].str.upper().tolist())
        static_qty: dict[str, float] = {
            t: float(holdings[t]["q"])
            for t in all_tickers
            if t not in logged_tickers and holdings.get(t, {}).get("q", 0) > 0
        }
        static_avg: dict[str, float] = {t: _safe_or(holdings[t].get("avg", 0), 0.0) for t in static_qty}

        # 모든 이벤트(주식 + 현금)를 날짜 포지션에 매핑 — 날짜 보정 없음
        # 행은 Series 가 아니라 dict 로 들고 간다 (build_equity_curve 와 같은 이유).
        all_events_by_pos: dict[int, list] = {}
        positions = idx.searchsorted(log_df["date"].to_numpy(), side="left")
        for pos, row in zip(positions, log_df.to_dict("records")):
            pos = int(pos)
            if pos < len(idx):
                all_events_by_pos.setdefault(pos, []).append(row)
    else:
        all_tickers       = sorted(current_tickers & set(prices.columns))
        static_qty        = {t: float(holdings[t]["q"]) for t in all_tickers}
        static_avg        = {t: _safe_or(holdings[t].get("avg", 0), 0.0) for t in all_tickers}
        all_events_by_pos = {}

    # ── 순방향 워크: 현금 0, 보유 0에서 시작 ────────────────────────────────
    running_cash: float = 0.0
    running_qty: dict[str, float] = {t: static_qty.get(t, 0.0) for t in all_tickers}
    running_avg: dict[str, float] = {
        t: static_avg.get(t, _safe_or(holdings.get(t, {}).get("avg", 0), 0.0))
        for t in all_tickers
    }

    equity_arr    = np.zeros(len(idx), dtype=float)
    cash_events: dict[str, float] = {}
    holdings_by_date: dict[str, list[dict]] = {}

    px_mat = _price_matrix(prices, all_tickers)
    # 날짜 포맷도 한 번에 — 루프 안에서 Timestamp.strftime 을 날짜마다 부르면
    # 날짜 수만큼 포맷 파싱이 반복된다.
    date_strs = idx.strftime("%Y-%m-%d").tolist()

    for i in range(len(idx)):
        row_prices = px_mat[i]
        date_str   = date_strs[i]

        for row in all_events_by_pos.get(i, []):
            ticker     = str(row.get("ticker", "")).upper()
            trade_type = str(row.get("type",   "")).upper()
            q          = float(row.get("q",     0))
            pr         = float(row.get("price") or 0)

            if ticker == "CASH":
                if trade_type == "DEPOSIT":
                    running_cash += q
                    cash_events[date_str] = cash_events.get(date_str, 0.0) + q
                elif trade_type == "WITHDRAW":
                    running_cash -= q
                    cash_events[date_str] = cash_events.get(date_str, 0.0) - q
            elif ticker in running_qty:
                if trade_type in ("ADD", "BUY"):
                    prev_qty = running_qty[ticker]
                    prev_avg = running_avg[ticker]
                    new_qty  = prev_qty + q
                    running_avg[ticker] = (prev_qty * prev_avg + q * pr) / new_qty if new_qty > 0 else 0.0
                    running_qty[ticker] = new_qty
                    running_cash -= q * pr
                elif trade_type in ("SOLD", "SELL"):
                    running_qty[ticker] = max(0.0, running_qty[ticker] - q)
                    if running_qty[ticker] == 0:
                        running_avg[ticker] = 0.0
                    running_cash += q * pr
                elif trade_type == "UPDATE":
                    running_qty[ticker] = max(0.0, q)

        # 현금이 음수가 되는 경우 = 아직 기록되지 않은 자금 조달(입금 누락·역순 입력).
        # 예전에는 max(0.0, running_cash) 로 잘라냈는데, 그러면 V(t-1)이 부풀려진 상태에서
        # 나중에 실제 DEPOSIT 이 들어오면 현금흐름(CF)에는 잡히고 자산(V)에는 안 잡혀
        # daily_factor 가 붕괴하고, 곱셈 누적이라 그 오차가 곡선 끝까지 영구히 남았다.
        # 그렇다고 음수를 그대로 두면 자산이 음수가 되어 TWRR 이 발산한다.
        # → 부족분을 '암묵적 외부 자금 유입'으로 기록해 V 와 CF 가 같이 움직이게 한다.
        if running_cash < -1e-9:
            shortfall = -running_cash
            cash_events[date_str] = cash_events.get(date_str, 0.0) + shortfall
            running_cash = 0.0

        # 시세 없는 날은 취득원가로 대체 평가 (0원 평가 시 곡선에 절벽 발생)
        stock_val = sum(
            _price_or_cost(row_prices[j], running_avg.get(t, 0.0)) * running_qty[t]
            for j, t in enumerate(all_tickers)
        )
        equity_arr[i] = running_cash + stock_val

        # 시세가 없는 날은 값을 만들지 않는다. 예전에는 가격을 0 으로 채워
        # `(0 / avg - 1) * 100` = **-100%** 를 보고했다 — 유한한 값이라 NaN
        # 가드에 걸리지 않고, 화면에는 "전액 손실"이 빨간 글씨로 떴다.
        # 선행 결측(상장 전·백필 불가 구간)에서 실제로 나온다. 에쿼티 곡선은
        # `_price_or_cost` 가 취득원가로 대체해 보호되는데 이 표시 필드만
        # 그 보호를 받지 않았다.
        rows_today: list[dict] = []
        for j, t in enumerate(all_tickers):
            if running_qty[t] <= 0:
                continue
            px_t = _num_or_none(row_prices[j])
            ret = (_num_or_none((px_t / running_avg[t] - 1) * 100)
                   if px_t is not None and running_avg[t] > 0 else None)
            rows_today.append({
                "ticker":     t,
                "return_pct": _round_keep_none(ret, 2),
                "price":      _round_keep_none(px_t, 2),
            })
        holdings_by_date[date_str] = rows_today

    equity = pd.Series(equity_arr, index=idx, dtype=float)

    # ── 초기 자산: 첫 번째 양수 값 ───────────────────────────────────────────
    meaningful = equity[equity > 0]
    if meaningful.empty:
        return _empty_curve(), {}, 0.0, {}, _empty_curve()
    initial_equity = float(meaningful.iloc[0])
    first_idx      = meaningful.index[0]

    # ── TWRR 누적 수익률 시계열 ───────────────────────────────────────────────
    # r_t = (V_t - CF_t) / V_{t-1} - 1,  R_T = ∏(1+r_t) - 1
    cumulative_factor = 1.0
    prev_e = 0.0
    started = False
    return_pct_arr = np.zeros(len(idx), dtype=float)

    for i, (date, e_val) in enumerate(equity.items()):
        e_f      = float(e_val) if not pd.isna(e_val) else 0.0
        date_str = date.strftime("%Y-%m-%d")
        cf_f     = cash_events.get(date_str, 0.0)

        if date < first_idx:
            return_pct_arr[i] = 0.0
            prev_e = e_f
            continue

        if not started:
            # 첫 보유일: 이 날을 기준점(0%)으로 삼는다
            cumulative_factor = 1.0
            return_pct_arr[i] = 0.0
            prev_e = e_f
            started = True
            continue

        if prev_e > 0:
            # 일별 TWRR 팩터: 입출금을 제거한 순수 가격 변동
            daily_factor = (e_f - cf_f) / prev_e
            cumulative_factor *= daily_factor
            return_pct_arr[i] = (cumulative_factor - 1.0) * 100.0
        else:
            return_pct_arr[i] = 0.0

        prev_e = e_f

    return_pct = pd.Series(return_pct_arr, index=idx, dtype=float)

    return return_pct, holdings_by_date, initial_equity, cash_events, equity


def return_pct_to_records(
    return_pct: pd.Series,
    holdings_by_date: dict,
    close_df: "pd.DataFrame | None" = None,
    trade_markers: "list | None" = None,
    initial_equity: float = 0.0,
    cash_events: "dict[str, float] | None" = None,
    equity: "pd.Series | None" = None,
    # 마지막에 둔다 — 이 함수는 위치 인자로 불린다. 중간에 끼우면 그 뒤 인자가
    # 한 칸씩 밀려 `trade_markers` 가 `market` 자리에 들어간다 (실제로 그랬다).
    market: str = "US",
) -> list[dict]:
    """
    수익률(%) 시계열을 API 응답용 레코드 리스트로 변환.

    현금 입출금 시 포트폴리오 라인에 스파이크가 생기므로,
    S&P 500 벤치마크에도 동일 비율(net_flow / initial_equity × 100)만큼
    누적 편향(offset)을 더해 상대 수익률 비교를 유지한다.
    """
    if return_pct.empty:
        return []

    # 그래프 시작점 = 계좌에 자산이 처음 생긴 날.
    # 예전에는 '첫 **보유 종목**이 생긴 날'만 봤다. holdings_by_date 는 주식만 담으므로
    # 현금 입금으로 시작한 계좌는 첫 매수일까지 구간이 통째로 잘려 나갔다
    # (입금 2025-08 → 첫 매수 2026-01 이면 5개월이 사라진다).
    # 자산이 0 을 벗어나는 시점(입금 포함)을 기준으로 삼는다.
    first_date = None
    if equity is not None and not equity.empty:
        pos_eq = equity[equity > 0]
        if not pos_eq.empty:
            first_date = pos_eq.index[0]

    if first_date is None:
        for date_str in sorted(holdings_by_date.keys()):
            if holdings_by_date[date_str]:
                first_date = pd.Timestamp(date_str)
                break
    if first_date is None and trade_markers:
        sorted_dates = sorted(tr.get("date", "") for tr in trade_markers if tr.get("date"))
        if sorted_dates:
            ts  = pd.Timestamp(sorted_dates[0])
            pos = int(return_pct.index.searchsorted(ts, side="left"))
            if pos < len(return_pct.index):
                first_date = return_pct.index[pos]
    if first_date is None:
        return []

    # 시작 직전 여백은 자산이 생기기 전 구간이라 0% 직선만 그려진다 — 붙이지 않는다.
    curve = return_pct.loc[return_pct.index >= first_date]
    # 주말(토·일) 포인트 제거 — ffill 연장으로 생긴 0% 변동 날짜를 그래프에서 제외
    curve = curve[curve.index.dayofweek < 5]
    if curve.empty:
        return []

    # 비교선은 **시장 기준 지수**다 (US ^GSPC · KR ^KS11). TWRR 포트폴리오는
    # 현금흐름 왜곡이 없으므로 첫날 기준 단순 누적 수익률로 비교한다.
    from backend.services.markets import benchmark_for
    bench = benchmark_for(market)

    b_full: "pd.Series | None" = None
    bench_first_val: "float | None" = None
    if close_df is not None and bench in close_df.columns:
        b_full = close_df[bench].reindex(curve.index).ffill().bfill()
        b_from_first = b_full.loc[b_full.index >= first_date].dropna()
        if not b_from_first.empty:
            bench_first_val = float(b_from_first.iloc[0])

    cash_evts = cash_events or {}

    # 날짜 문자열·벤치마크·자산을 모두 `curve.index` 기준 위치 배열로 맞춘다.
    # 루프 안에서 날짜 라벨로 `.loc` 을 걸면 날짜마다 인덱스 조회가 두 번씩 생겨
    # 이 함수 시간의 절반이 그 조회였다. b_full 은 이미 curve.index 로 reindex 돼
    # 있고, equity 는 여기서 한 번 맞춘다 — 없는 날짜는 NaN 이 되어 원래의
    # `date in equity.index` 검사와 같은 경로로 걸러진다.
    # 루프 안의 `date >= first_date` 검사도 함께 뺐다 — curve 는 위에서 이미
    # `index >= first_date` 로 잘려 있어 모든 날짜가 항상 그 조건을 만족한다.
    date_strs = curve.index.strftime("%Y-%m-%d").tolist()
    b_vals  = None if b_full is None else b_full.to_numpy()
    eq_vals = None if equity is None else equity.reindex(curve.index).to_numpy()

    trade_by_date = _trades_by_chart_date(trade_markers, date_strs)

    records = []
    for i, pct in enumerate(curve.to_numpy()):
        if pd.isna(pct):
            continue
        date_str = date_strs[i]

        # 보유는 있는데 **관측된 가격이 하나도 없는 날**은 레코드로 내보내지
        # 않는다. `_price_or_cost` 가 그 날을 취득원가로 평가하므로 곡선이
        # 평평해지고, TWRR 은 그 평평함을 정직하게 0% 로 읽는다. 결과는
        # "변동 없음" 으로 그려지는 관측 아닌 포인트다.
        #
        # 실측: KR 포트폴리오의 앞 71포인트가 `port=0.0` 으로 그려졌다. 같은
        # 구간에서 벤치마크 선은 움직여서(그쪽은 데이터가 있다) 포트폴리오가
        # 3개월간 정체한 것처럼 보였다 — 실제로는 시세를 모르는 구간이다.
        #
        # 하나라도 관측된 날은 남긴다. 부분 관측을 버리면 실제 관측이 있는 날을
        # 버리게 되고, 그건 같은 오류의 반대 방향이다. 몇 개 미만이면 못 믿는가는
        # 표시 계층이 판단한다 — 종목별 `price` 가 레코드에 그대로 실려 있다.
        rows_today = holdings_by_date.get(date_str) or []
        if rows_today and all(r.get("price") is None for r in rows_today):
            continue

        bench_pct = None
        if b_vals is not None and bench_first_val:
            v = b_vals[i]
            if not pd.isna(v):
                bench_pct = round((float(v) / bench_first_val - 1) * 100, 2)

        eq_val = None
        if eq_vals is not None:
            v = eq_vals[i]
            if not pd.isna(v):
                eq_val = round(float(v), 2)

        # 입출금 이벤트: 양수=입금, 음수=출금 (점 마커용)
        cf_val = cash_evts.get(date_str)

        records.append({
            "date":         date_str,
            "port":         round(float(pct), 2),
            # 키 이름에 벤치마크를 박지 않는다 — `sp` 는 `alpha_vs_sp500` 과
            # 같은 문제였다. 계산을 고쳐도 이름이 거짓말을 계속한다.
            # 무엇 대비인지는 `/metrics` 의 `benchmark`·`benchmark_label` 이
            # 말한다 (같은 화면이 그 응답도 받는다).
            "benchmark_pct": bench_pct,
            "total_equity": eq_val,
            "cash_flow":    cf_val,
            "trades":       trade_by_date.get(date_str, []),
            "holdings":     holdings_by_date.get(date_str, []),
        })
    return records

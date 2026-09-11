"""
services/portfolio_optimizer.py
────────────────────────────────
AI-powered Black-Litterman portfolio optimization.

Pipeline (모든 IO 병렬 실행):
  ① yfinance prices  ‖  Perplexity news  ‖  yfinance Ticker.info (fundamentals)
  ② AI views (Perplexity sonar-pro) — 밸류에이션·성장·애널리스트·모멘텀 종합
     → forward-looking 예상수익
  ③ Ledoit-Wolf cov  →  Black-Litterman (Idzorek confidence)
  ④ EfficientFrontier × 4 modes  +  frontier curve
"""
from __future__ import annotations

import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

logger = logging.getLogger(__name__)

# AI 뷰 생성 모델. 추론 + 실시간 웹 검색이 필요해 sonar-pro 사용
# (뉴스 요약용 _fetch_news 는 더 가벼운 sonar 로 충분).
# API 키는 여기서 읽지 않는다 — services/perplexity 가 호출 시점에 읽으므로
# 키를 교체하거나 테스트에서 바꿔도 재로딩이 필요 없다.
_PPLX_MODEL         = os.getenv("PERPLEXITY_MODEL", "sonar-pro")


# ── helpers ───────────────────────────────────────────────────────────────────

def _round_or_none(v, digits: int = 2) -> Optional[float]:
    """숫자면 반올림해서, 아니면 None. **반올림 함수다 — 기본값 함수가 아니다.**

    이름이 `_safe` 였다. 그런데 같은 이름이 `portfolio_calculator` 에는
    `(v, default=0.0)`, `routers/ticker.py` 에는 `(v, default=None)` 으로 있고
    거기서는 둘째 인자가 **기본값**이다. 그래서 `_safe(x, 1)` 이 이 파일에서는
    "소수 1자리" 이고 저기서는 "없으면 1" 이었다 — 두 파일을 오가며 작업하면
    밟는다. 하는 일을 이름에 적어 그 혼동을 없앤다.
    """
    try:
        return round(float(v), digits) if v is not None else None
    except (TypeError, ValueError):
        return None


def _pct(v, digits: int = 1) -> Optional[float]:
    """소수 → 퍼센트 변환 + 반올림."""
    try:
        return round(float(v) * 100, digits) if v is not None else None
    except (TypeError, ValueError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# 1. 가격 데이터
# ─────────────────────────────────────────────────────────────────────────────

def _select_data_period(holding_period_years: float) -> str:
    """투자기간 기반 역사 데이터 기간 자동 선택.

    규칙: 투자기간 × ~3배 (최소 2년) — 비즈니스 사이클을 충분히 포착.
    yfinance 최대 ~10년, Ledoit-Wolf 안정성을 위해 최소 120거래일 필요.
    """
    y = float(holding_period_years)
    if y <= 0.5:
        return "2y"    # 단기: 최근성 중시, 계절성 포착
    elif y <= 1.0:
        return "3y"    # 1년: 완전한 연간 사이클
    elif y <= 2.0:
        return "5y"    # 2년: 비즈니스 사이클 포함
    elif y <= 5.0:
        return "7y"    # 5년: 여러 경제 사이클
    else:
        return "10y"   # 장기: 전체 시장 사이클


def _compute_extended_metrics(
    weights: dict,
    daily_returns: pd.DataFrame,
    rf_rate: float,
    benchmark_returns: "pd.Series | None" = None,
) -> dict:
    """역사 데이터 기반 확장 리스크 지표.

    Sortino  — 하방 편차(음수 초과수익) 기준 위험조정수익
    MDD      — 최고점 대비 최대 낙폭
    Calmar   — 연환산 수익 / |MDD|
    Beta     — **그 시장 기준 지수** 대비 시장 민감도 (벤치마크 있을 때만).
               무엇 대비인지는 응답의 `beta_benchmark` 가 말한다 — 여기에
               지수 이름을 적으면 계산을 고쳐도 문서가 거짓말을 계속한다
               (`alpha_vs_sp500` 이 그랬다).
               못 구하면 `beta_reason` 에 이유 코드를 싣는다.
    CVaR 95% — 하위 5% 시나리오 평균 연환산 손실
    """
    w = pd.Series(weights).reindex(daily_returns.columns).fillna(0.0)
    if w.sum() < 1e-8:
        return {}
    w /= w.sum()

    port_ret = (daily_returns @ w).dropna()
    n = len(port_ret)
    if n < 30:
        return {}

    ann_ret = float((1 + port_ret).prod() ** (252 / n) - 1)

    # Sortino: 하방 편차 = 음수 초과수익의 RMSE (semi-deviation)
    #
    # 분모가 0 이면 비율이 **정의되지 않는다** (무한). 예전에는 `0.0` 을 줬는데,
    # 0.0 은 화면에서 "위험조정수익이 없다" 즉 **최악**으로 읽힌다 — 실제로는
    # 하방 변동이 전혀 없었다는 뜻이므로 최선이다. 부호가 뒤집힌 위장이다.
    # 실측: 매일 +0.05% 로만 오른 포트폴리오(연 +13.42%, 낙폭 0) → sortino 0.0.
    # 이제 None 으로 둔다 (프론트는 `typeof raw === 'number'` 로 걸러 '—' 를
    # 그린다 — `Optimizer.tsx:318-321`).
    daily_rf   = (1 + rf_rate) ** (1 / 252) - 1
    excess_neg = (port_ret - daily_rf).clip(upper=0.0)
    downside_std = float(np.sqrt((excess_neg ** 2).mean()) * np.sqrt(252))
    sortino: float | None = None
    sortino_reason: str | None = None
    if downside_std > 1e-8:
        sortino = (ann_ret - rf_rate) / downside_std
    else:
        # 위험이 없어서 비율이 없는 것과, 계산이 깨져서 없는 것을 가른다.
        sortino_reason = "no_downside"

    # Max Drawdown
    cum = (1 + port_ret).cumprod()
    mdd = float((cum / cum.cummax() - 1).min())

    # Calmar — 낙폭이 0 이면 같은 이유로 정의되지 않는다.
    calmar: float | None = None
    calmar_reason: str | None = None
    if abs(mdd) > 1e-8:
        calmar = ann_ret / abs(mdd)
    else:
        calmar_reason = "no_drawdown"

    # Beta — 비교 대상은 호출자가 넘긴 벤치마크다 (그 시장의 기준 지수).
    #
    # 못 구한 이유를 코드로 남긴다. 셋 다 `beta: null` 로 끝나서 화면에는
    # 똑같이 "—" 가 뜨는데, 원인이 "지수를 못 받았다" 인지 "구간이 짧다" 인지
    # 응답으로 구별되지 않았다. 문장이 아니라 코드로 싣는다 — 문구는 표시
    # 계층의 결정이다.
    beta: float | None = None
    beta_reason: str | None = None
    if benchmark_returns is None:
        # 이 파이프라인은 벤치마크를 **항상** 요청하므로, 없다는 것은 수집 실패다.
        beta_reason = "no_benchmark"
    else:
        aligned = pd.concat([port_ret, benchmark_returns.rename("bench")], axis=1).dropna()
        if len(aligned) < 30:
            beta_reason = "insufficient_overlap"
        else:
            cov_mat = aligned.cov().values
            if cov_mat[1, 1] > 1e-8:
                beta = float(cov_mat[0, 1] / cov_mat[1, 1])
            else:
                # 지수 수익률의 분산이 0 — 같은 값이 반복되는 프레임이다.
                beta_reason = "zero_variance"

    # CVaR 95%: 하위 5% 일 수익 평균 → 연환산
    q5 = port_ret.quantile(0.05)
    tail = port_ret[port_ret <= q5]
    cvar_daily = float(tail.mean()) if not tail.empty else float(q5)
    cvar_95 = cvar_daily * np.sqrt(252)

    return {
        "sortino_ratio": round(sortino, 3) if sortino is not None else None,
        "sortino_reason": sortino_reason,
        "max_drawdown":  round(mdd, 4),
        "calmar_ratio":  round(calmar, 3) if calmar is not None else None,
        "calmar_reason": calmar_reason,
        "beta":          round(beta, 3) if beta is not None else None,
        # 베타가 있으면 None. 값이 있을 때 이유까지 실으면 소비자가 둘 중
        # 무엇을 믿을지 정해야 한다.
        "beta_reason":   beta_reason,
        "cvar_95":       round(cvar_95, 4),
    }


def hrp_weights(cov: pd.DataFrame, corr: pd.DataFrame) -> pd.Series:
    """Hierarchical Risk Parity (López de Prado 2016) 비중 산출.

    PyPortfolioOpt 의 HRPOpt 를 쓰지 않고 직접 구현한다 — 해당 구현이 scipy 내부
    비공개 API(`_LINKAGE_METHODS`)에 의존해 scipy 1.18 에서 동작하지 않는다.
    알고리즘 자체는 논문에 명확히 정의돼 있어 직접 구현이 더 안정적이다.

    3단계:
      ① 트리 클러스터링 — 상관계수를 거리 d=√(0.5(1−ρ)) 로 바꿔 계층 군집화
      ② 준대각화       — 덴드로그램 잎 순서로 자산을 재배열해
                         상관 높은 자산이 인접하도록 만든다
      ③ 재귀적 이분할   — 좌우 클러스터의 분산에 **반비례**하도록 비중을 나누고
                         각 클러스터 내부에서 같은 과정을 재귀 반복

    공분산 역행렬을 쓰지 않으므로 마코위츠 계열의 추정오차 증폭 문제가 없다.
    """
    from scipy.cluster.hierarchy import linkage, to_tree
    from scipy.spatial.distance import squareform

    tickers = list(cov.columns)
    if len(tickers) == 1:
        return pd.Series([1.0], index=tickers)

    # ① 상관 거리 → 계층 군집화
    d = np.sqrt(np.clip(0.5 * (1.0 - corr.values), 0.0, None))
    np.fill_diagonal(d, 0.0)
    d = (d + d.T) / 2.0                      # 수치오차로 인한 비대칭 제거
    link = linkage(squareform(d, checks=False), method="single")

    # ② 준대각화 — 덴드로그램 잎 순서
    order = [int(i) for i in to_tree(link, rd=False).pre_order()]

    # 이하는 티커 라벨이 아니라 **열 위치**로 다룬다. 재귀 이분할은 클러스터마다
    # 부분행렬을 꺼내므로, 라벨로 하면 `cov.loc[items, items]` 가 단계마다 인덱스를
    # 다시 만든다 (n=30 에서 전체 시간의 3/4 이 그 조회였다). 값과 연산 순서는
    # 그대로 두고 조회 방식만 바꾼다.
    cov_v = cov.to_numpy(dtype=float)
    diag = np.diag(cov_v)

    def _ivp(items: np.ndarray) -> np.ndarray:
        """역분산 비중 — 클러스터 내부 배분 및 클러스터 분산 계산에 사용."""
        var = diag[items]
        var = np.where(var > 1e-16, var, 1e-16)
        inv = 1.0 / var
        return inv / inv.sum()

    def _cluster_var(items: np.ndarray) -> float:
        w = _ivp(items)
        sub = cov_v[np.ix_(items, items)]
        return float(w @ sub @ w)

    # ③ 재귀적 이분할
    w_arr = np.ones(len(tickers), dtype=float)
    clusters = [np.array(order, dtype=np.intp)]
    while clusters:
        nxt: list[np.ndarray] = []
        for c in clusters:
            if len(c) <= 1:
                continue
            mid = len(c) // 2
            left, right = c[:mid], c[mid:]
            v_l, v_r = _cluster_var(left), _cluster_var(right)
            # 분산이 큰 쪽에 더 적게 — 리스크 패리티
            alpha = 1.0 - v_l / (v_l + v_r) if (v_l + v_r) > 0 else 0.5
            w_arr[left]  *= alpha
            w_arr[right] *= (1.0 - alpha)
            nxt += [left, right]
        clusters = nxt

    return pd.Series(w_arr, index=tickers)


def _fetch_prices(tickers: list[str], period: str = "1y") -> pd.DataFrame:
    from backend.services.market_data import get_close_df
    df = get_close_df(tickers, period=period, ttl=300, include_market=False)
    if df is None or df.empty:
        return pd.DataFrame()
    df = df[df.index.dayofweek < 5].ffill().dropna()
    valid = [t for t in tickers if t in df.columns]
    return df[valid]


def _compute_price_stats(prices: pd.DataFrame) -> dict:
    """다기간 수익률 + 52주 고/저 + 변동성 산출."""
    stats = {}
    for t in prices.columns:
        s = prices[t].dropna()
        if len(s) < 10:
            continue
        daily_ret = s.pct_change().dropna()

        def pret(days: int) -> Optional[float]:
            if len(s) >= days:
                return round((float(s.iloc[-1]) / float(s.iloc[-days]) - 1) * 100, 2)
            return None

        n252 = min(252, len(s))
        hi52 = float(s.iloc[-n252:].max())
        lo52 = float(s.iloc[-n252:].min())

        stats[t] = {
            "current_price":    round(float(s.iloc[-1]), 2),
            "ret_1m":           pret(21),
            "ret_3m":           pret(63),
            "ret_6m":           pret(126),
            "ret_1y":           pret(252),
            "annual_vol_1y":    round(float(daily_ret.std() * np.sqrt(252)) * 100, 2),
            "vs_52w_high_pct":  round((float(s.iloc[-1]) / hi52 - 1) * 100, 2) if hi52 else None,
            "vs_52w_low_pct":   round((float(s.iloc[-1]) / lo52 - 1) * 100, 2) if lo52 else None,
        }
    return stats


# ─────────────────────────────────────────────────────────────────────────────
# 2. 펀더멘털 (yfinance Ticker.info) — 병렬 수집
# ─────────────────────────────────────────────────────────────────────────────

def _gather_fundamentals(tickers: list[str]) -> dict:
    """각 티커에 대해 밸류에이션·성장·수익성·애널리스트·리스크 지표 병렬 수집."""
    import yfinance as yf

    def _fetch_one(t: str) -> tuple[str, dict]:
        try:
            info = yf.Ticker(t).info or {}
            cur  = info.get("currentPrice") or info.get("regularMarketPrice") or 0
            tgt  = info.get("targetMeanPrice") or info.get("targetMedianPrice")
            analyst_upside = round((tgt / cur - 1) * 100, 2) if (tgt and cur) else None

            return t, {
                # ── 밸류에이션 ──────────────────────────────────
                "pe_forward":       _round_or_none(info.get("forwardPE")),
                "pe_trailing":      _round_or_none(info.get("trailingPE")),
                "pb_ratio":         _round_or_none(info.get("priceToBook")),
                "peg_ratio":        _round_or_none(info.get("pegRatio")),
                "ev_ebitda":        _round_or_none(info.get("enterpriseToEbitda")),
                "price_to_sales":   _round_or_none(info.get("priceToSalesTrailing12Months")),
                # yfinance 의 dividendYield 는 **이미 퍼센트**다. 실측:
                #   AAPL 0.34 · 005930.KS 0.56
                # `_pct` 로 또 100 을 곱하면 34%·56% 가 됐다. 크기로 단위를
                # 추측하지 않는다 — 같은 info dict 안에서 필드마다 단위가 다르고
                # (payoutRatio 0.1204·profitMargins 0.276 은 분수), 크기 휴리스틱은
                # 저수익률 종목에서 틀린다.
                "div_yield_pct":    _round_or_none(info.get("dividendYield"), 2),
                "market_cap_b":     _round_or_none((info.get("marketCap") or 0) / 1e9, 1),
                # ── 성장 & 수익성 ────────────────────────────────
                "rev_growth_yoy":   _pct(info.get("revenueGrowth")),
                "earnings_growth":  _pct(info.get("earningsGrowth")),
                "roe":              _pct(info.get("returnOnEquity")),
                "roa":              _pct(info.get("returnOnAssets")),
                "profit_margin":    _pct(info.get("profitMargins")),
                "gross_margin":     _pct(info.get("grossMargins")),
                "ebitda_margin":    _pct(info.get("ebitdaMargins")),
                "revenue_per_share":_round_or_none(info.get("revenuePerShare")),
                "eps_trailing":     _round_or_none(info.get("trailingEps")),
                "eps_forward":      _round_or_none(info.get("forwardEps")),
                # ── 재무 건전성 ──────────────────────────────────
                # debtToEquity 도 퍼센트다 (실측: AAPL 78.445 = 0.78배,
                # 005930.KS 3.868 = 0.039배). 값은 그대로 두고 프롬프트에서
                # 단위를 붙인다 — `_build_ticker_section` 참고.
                "debt_to_equity":   _round_or_none(info.get("debtToEquity")),
                "current_ratio":    _round_or_none(info.get("currentRatio")),
                "quick_ratio":      _round_or_none(info.get("quickRatio")),
                "free_cashflow_b":  _round_or_none((info.get("freeCashflow") or 0) / 1e9, 1),
                # ── 시장 리스크 ──────────────────────────────────
                "beta":             _round_or_none(info.get("beta")),
                "short_ratio":      _round_or_none(info.get("shortRatio")),  # 공매도 청산 소요일
                "short_pct_float":  _pct(info.get("shortPercentOfFloat")),
                # ── 애널리스트 컨센서스 ──────────────────────────
                "analyst_target":       _round_or_none(tgt),
                "analyst_upside_pct":   analyst_upside,
                "analyst_rating_mean":  _round_or_none(info.get("recommendationMean")),  # 1=strong buy, 5=sell
                "analyst_rating_key":   info.get("recommendationKey", ""),
                "analyst_count":        info.get("numberOfAnalystOpinions"),
                # ── 메타 ─────────────────────────────────────────
                "sector":   info.get("sector", ""),
                "industry": info.get("industry", ""),
                "country":  info.get("country", ""),
            }
        except Exception as e:
            logger.warning("펀더멘털 수집 실패 (%s) — 그 종목은 값 없이 진행", t,
                           exc_info=True)
            return t, {}

    with ThreadPoolExecutor(max_workers=min(len(tickers), 8)) as ex:
        futures = {ex.submit(_fetch_one, t): t for t in tickers}
        result = {}
        for f in as_completed(futures):
            t, data = f.result()
            result[t] = data
    return result


# ─────────────────────────────────────────────────────────────────────────────
# 3. 뉴스 (Perplexity)
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_news(tickers: list[str], market: str = "US") -> str:
    """종목별 전망 요약. 한국이면 국내 경제지에서 회사명으로 찾는다 —
    '005930.KS' 로 물으면 국내 기사가 거의 걸리지 않는다."""
    from backend.services.news_sources import focus_block
    if market == "KR":
        from backend.services.markets import name_map_for
        _nm = name_map_for(tickers[:12], "KR")
        ticker_str = ", ".join(f"{_nm.get(t, t)}({t})" for t in tickers[:12])
    else:
        ticker_str = ", ".join(tickers[:12])
    prompt = (
        f"Provide a concise forward-looking investment summary for: {ticker_str}. "
        "Cover per ticker: recent earnings surprises, next quarter guidance, "
        "analyst upgrades/downgrades with target price changes, major catalysts (product launch, "
        "regulatory, M&A), and key risks. Include specific numbers and dates where available. "
        "Focus on information that would change forward return expectations vs historical trends."
        + focus_block(market)
    )
    from backend.services.perplexity import search
    # max_tokens 만 올린다 — 종목 12개의 전망을 담아야 한다. 모델·temperature·
    # timeout·출처 범위는 공유 클라이언트의 뉴스 수집 기본값이 그대로 맞다.
    return search(prompt, market=market, max_tokens=2000, label="Optimizer news")


# ─────────────────────────────────────────────────────────────────────────────
# 4. AI View 생성 (Perplexity) — 전향적 분석
# ─────────────────────────────────────────────────────────────────────────────

def _build_ticker_section(t: str, ps: dict, f: dict, market: str) -> str:
    """종목 한 개의 데이터를 LLM 프롬프트용 텍스트로 변환.

    `market` 에 기본값을 두지 않는다. 예전 시그니처가 시장을 아예 받지 않아
    한국 종목이 `$500000.0B` 로 프롬프트에 실렸다 — CLAUDE.md §1.4 가 인용하는
    그 사고(삼성전자 매출 333조원이 `$333605.94B` 로 실려 규모가 1,300배로
    서술됨)가 이 빌더에서는 고쳐지지 않은 채 남아 있었다. 기본값을 두면
    빠뜨린 호출부가 다시 조용히 달러가 되므로, 빠뜨리면 TypeError 가 나게 둔다.

    통화 포맷은 `report_writer._fmt_amount`/`_fmt_price` 에 위임한다. 같은 규칙을
    파일마다 다시 구현했다가 한 곳만 고쳐지는 것이 이 결함의 원인이었다.

    값이 없으면 그 줄·그 항목을 **적지 않는다.** `'?'` 를 값 자리에 넣으면
    모델이 그걸 수치처럼 인용한다.
    """
    from backend.services.markets import get_market
    from backend.services.report_writer import _fmt_amount, _fmt_price

    cur = get_market(market).currency

    def _amt_b(value) -> Optional[str]:
        """10억 단위로 들어온 값을 원래 크기로 되돌려 통화 포맷에 넘긴다."""
        try:
            return _fmt_amount(float(value) * 1e9, cur)
        except (TypeError, ValueError):
            return None

    head = [t]
    cap = _amt_b(f.get("market_cap_b")) if f.get("market_cap_b") is not None else None
    if cap:
        head.append(f"시가총액 {cap}")
    industry = " — ".join(x for x in (f.get("sector"), f.get("industry")) if x)
    if industry:
        head.append(industry)
    lines = [f"■ {t}  ({' | '.join(head[1:])})" if len(head) > 1 else f"■ {t}"]

    # 가격 모멘텀
    rets = []
    for label, key in [("1M", "ret_1m"), ("3M", "ret_3m"), ("6M", "ret_6m"), ("1Y", "ret_1y")]:
        v = ps.get(key)
        if v is not None:
            rets.append(f"{label}: {v:+.1f}%")
    if rets:
        vol = ps.get("annual_vol_1y", 0)
        hi  = ps.get("vs_52w_high_pct", 0)
        lines.append(f"  가격모멘텀: {', '.join(rets)} | 연변동성: {vol:.1f}% | 52주고점대비: {hi:+.1f}%")

    # 밸류에이션
    val = []
    pe = f.get("pe_forward") or f.get("pe_trailing")
    if pe:       val.append(f"{'Fwd' if f.get('pe_forward') else 'Ttm'} P/E {pe:.1f}x")
    if f.get("peg_ratio"):       val.append(f"PEG {f['peg_ratio']:.2f}x")
    if f.get("pb_ratio"):        val.append(f"P/B {f['pb_ratio']:.1f}x")
    if f.get("ev_ebitda"):       val.append(f"EV/EBITDA {f['ev_ebitda']:.1f}x")
    if f.get("price_to_sales"):  val.append(f"P/S {f['price_to_sales']:.1f}x")
    if val:
        lines.append(f"  밸류에이션: {' | '.join(val)}")

    # 성장·수익성
    growth = []
    if f.get("rev_growth_yoy") is not None:  growth.append(f"매출성장 {f['rev_growth_yoy']:+.1f}%")
    if f.get("earnings_growth") is not None: growth.append(f"이익성장 {f['earnings_growth']:+.1f}%")
    if f.get("roe") is not None:             growth.append(f"ROE {f['roe']:.1f}%")
    if f.get("profit_margin") is not None:   growth.append(f"순이익률 {f['profit_margin']:.1f}%")
    if f.get("ebitda_margin") is not None:   growth.append(f"EBITDA마진 {f['ebitda_margin']:.1f}%")
    if f.get("free_cashflow_b") is not None:
        fcf = _amt_b(f["free_cashflow_b"])
        if fcf:
            growth.append(f"FCF {fcf}")
    if growth:
        lines.append(f"  성장·수익성: {' | '.join(growth)}")

    # 재무 건전성
    fin = []
    if f.get("debt_to_equity") is not None:
        # 단위를 붙이지 않으면 모델이 배수로 읽는다 — AAPL 78.4 는 78.4배가 아니라
        # 78.4%(0.78배)이고 삼성전자 3.9 는 0.039배다. 라벨이 없으면 "극단적
        # 레버리지" 로 서술된다.
        fin.append(f"D/E {f['debt_to_equity']:.1f}%")
    if f.get("current_ratio") is not None:  fin.append(f"유동비율 {f['current_ratio']:.1f}x")
    if f.get("short_pct_float") is not None: fin.append(f"공매도 {f['short_pct_float']:.1f}%")
    if f.get("beta") is not None:            fin.append(f"베타 {f['beta']:.2f}")
    if fin:
        lines.append(f"  리스크지표: {' | '.join(fin)}")

    # 애널리스트 컨센서스
    if f.get("analyst_upside_pct") is not None:
        # 예전에는 없는 값을 '?' 로 적었다 — `목표가 $? | 평점 ?/5 | ?명 커버`.
        # 게다가 `f"{'?':.1f}"` 는 ValueError 라, 컨센서스는 있는데 평점만 없는
        # 종목에서 이 줄이 예외를 냈다. 있는 항목만 적는다.
        parts = [f"현재대비 {f['analyst_upside_pct']:+.1f}%"]
        if f.get("analyst_target") is not None:
            parts[0] = f"목표가 {_fmt_price(f['analyst_target'], cur)} ({parts[0]})"
        rating = f.get("analyst_rating_key") or (
            f"평점 {f['analyst_rating_mean']:.1f}/5"
            if f.get("analyst_rating_mean") is not None else None
        )
        if rating:
            parts.append(rating)
        if f.get("analyst_count") is not None:
            parts.append(f"{f['analyst_count']}명 커버")
        lines.append(f"  애널리스트: {' | '.join(parts)}")

    return "\n".join(lines)


def _generate_ai_views(
    tickers: list[str],
    price_stats: dict,
    fundamentals: dict,
    news_ctx: str,
    holding_period_years: float = 1.0,
    market: str = "US",
) -> dict:
    """밸류에이션·성장·애널리스트·모멘텀 종합 → Perplexity → forward-looking AI views.

    Perplexity 를 쓰는 이유: 실시간 웹 검색이 붙어 있어 학습 시점 이후의 실적·가이던스·
    투자의견 변경을 직접 조회할 수 있다. 정량 데이터(가격·펀더멘털)는 우리가 넣어주고,
    최신 정성 정보는 모델이 스스로 찾게 하는 구조.
    """
    horizon = (
        f"{int(holding_period_years * 12)}개월"
        if holding_period_years < 1
        else f"{holding_period_years:.0f}년"
    )

    sections = [_build_ticker_section(t, price_stats.get(t, {}),
                                      fundamentals.get(t, {}), market)
                for t in tickers]

    ticker_list = ", ".join(f'"{t}"' for t in tickers)

    prompt = f"""당신은 CFA를 보유한 글로벌 포트폴리오 매니저입니다.
아래 정밀 데이터를 종합해 향후 {horizon} 각 종목의 예상 연간 수익률과 신뢰도를 냉정하게 산출하세요.

━━━ 종목별 정밀 데이터 ━━━
{chr(10).join(sections)}

━━━ 최신 시장 뉴스·애널리스트 전망 ━━━
{news_ctx[:3000] if news_ctx else "뉴스 데이터 없음"}

━━━ 분석 지침 ━━━
1. 역사적 수익률 단순 외삽 절대 금지 — 평균회귀(mean reversion)와 현재 밸류에이션 수준 반드시 반영
2. PEG < 1.0 → 성장 대비 저평가 / PEG > 2.5 → 고평가 리스크 → expected_return 대폭 하향
3. 애널리스트 목표가는 구조적으로 30% 과낙관이므로 그대로 사용 금지. 실제 괴리율의 절반 이하로 할인해 반영
4. 52주 고점 −5% 이내 → 추가 상승 여력 제한, expected_return 하향
5. 베타 > 1.5 → 고위험, macro 불확실성에 취약 → confidence 하향 + expected_return 보수적 조정
6. 공매도 비율 > 15% → 하락 압력 실재, 반드시 Bearish/음수 반영
7. D/E 높고 금리 상승 환경 → 이자 부담 증가, 하락 위험 반영
8. 이익성장·매출성장 < 0 또는 가이던스 하향 → 반드시 음수 expected_return 부여
9. 뉴스에 실적 서프라이즈·가이던스 변화가 있으면 상향/하향 방향 모두 그대로 반영
10. 고평가(Fwd P/E > 40, P/S > 15) + 성장 둔화 → 음수 expected_return 필수 검토

━━━ 편향 방지 ━━━
- 주식시장 장기 평균 수익률은 연 8~10%. 개별 종목은 이보다 훨씬 낮거나 높을 수 있다.
- 모든 종목이 Bullish/양수라면 근거가 압도적으로 강해야 한다. 그렇지 않으면 하향 조정.
- 데이터가 하락을 시사하는데 양수를 부여하는 것은 분석 오류다.

다음 종목들을 분석해 순수 JSON만 출력하라 (코드블록·설명 없이):
{ticker_list}

출력 스키마 (각 종목에 대해):
{{
  "<ticker>": {{
    "expected_return": <float, -0.50~+0.60, 음수 적극 사용>,
    "confidence": <float, 0.20~0.90>,
    "sentiment": <"Bullish" | "Neutral" | "Bearish">,
    "key_driver": <한국어 1~2문장, 역사적 수익률 단순 반복 금지>
  }}
}}"""

    from backend.services.perplexity import search

    raw = search(
        prompt,
        market=market,
        max_tokens=3000,
        label="Optimizer AI views",
        # 예측용이라 모델·system·temperature·timeout 을 올린다:
        #   sonar-pro  — 뉴스 요약보다 추론이 필요하다
        #   system     — JSON 만 내라는 지시. 이게 없으면 마크다운·각주가 섞인다
        #   0.2        — API 기본값보다 낮춰 예측 편차를 줄인다 (뉴스 수집용
        #                기본값 0.0 을 쓰면 이 경로의 출력이 달라진다)
        #   120s       — 웹 검색이 붙어 뉴스 요약보다 느리다
        model=_PPLX_MODEL,
        system=(
            "You are a CFA-certified portfolio manager with a mandate to provide unbiased, "
            "cold-blooded return forecasts. You do NOT have a bullish bias. "
            "If data signals downside, you assign negative expected returns without hesitation. "
            "Use your web search to verify the latest earnings, guidance and analyst actions. "
            "Output ONLY valid JSON. No markdown, no code blocks, no citations, no explanation."
        ),
        temperature=0.2,
        timeout=120,
    )
    if not raw:
        # 실패 이유는 공유 클라이언트가 이미 로그에 남겼다.
        return {}

    try:
        # Perplexity 는 각주([1] 등)를 붙이는 경우가 있어 JSON 추출 전에 제거
        raw = re.sub(r"\[\d+\]", "", raw.strip())

        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            raw = m.group(0)
        parsed = json.loads(raw)

        result: dict = {}
        for t in tickers:
            v = parsed.get(t) or {}
            er   = float(np.clip(v.get("expected_return", 0.0),  -0.5, 0.6))  # 기본 0 (편향 중립)
            conf = float(np.clip(v.get("confidence", 0.5), 0.20, 0.90))
            sent = v.get("sentiment", "Neutral")
            if sent not in ("Bullish", "Neutral", "Bearish"):
                sent = "Neutral"
            result[t] = {
                "expected_return": er,
                "confidence":      conf,
                "sentiment":       sent,
                "key_driver":      str(v.get("key_driver", ""))[:200],
            }
        return result

    except Exception as e:
        logger.warning("AI 뷰 JSON 파싱 실패 — AI 뷰 없이 진행 (raw 앞부분: %s)",
                       raw[:200], exc_info=True)
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# 5. PyPortfolioOpt 최적화
# ─────────────────────────────────────────────────────────────────────────────

def _run_pypfopt(
    prices: pd.DataFrame,
    ai_views: dict,
    target_return: float = 0.10,
    risk_free_rate: float = 0.04,
    weight_bounds: tuple[float, float] = (0.0, 1.0),
    benchmark: "pd.Series | None" = None,
) -> dict:
    from pypfopt import EfficientFrontier, expected_returns
    from pypfopt.risk_models import CovarianceShrinkage
    from pypfopt.black_litterman import BlackLittermanModel

    tickers = list(prices.columns)
    n = len(tickers)

    # 종목 수에 따라 weight_bounds 자동 조정 (단일해 문제 방지)
    lb, ub = weight_bounds
    if n > 0 and n * ub < 1.0 - 1e-6:
        ub = 1.0 / n + 0.05
    if n > 0 and n * lb > 1.0 + 1e-6:
        lb = (1.0 / n) * 0.5
    effective_bounds = (lb, ub)

    # 일별 수익률 (raw — 확장 지표에 사용: MDD 등 실제 극단값 보존)
    daily_rets_raw = prices.pct_change().dropna()

    # 공분산 추정: 1%~99% 윈저화 → Ledoit-Wolf
    # (플래시 크래시·데이터 오류 등 극단 1일 수익이 공분산을 과장하는 문제 제거)
    q_lo = daily_rets_raw.quantile(0.01)
    q_hi = daily_rets_raw.quantile(0.99)
    daily_rets_clean = daily_rets_raw.clip(lower=q_lo, upper=q_hi, axis=1)
    cs = CovarianceShrinkage(daily_rets_clean, returns_data=True, frequency=252)
    S  = cs.ledoit_wolf()

    # 과거 기대수익 (Prior): 기하평균 — 복리 수익의 과대추정 방지
    mu_hist = expected_returns.mean_historical_return(
        prices, frequency=252, compounding=True
    )

    # 벤치마크 일별 수익
    bench_ret: pd.Series | None = None
    if benchmark is not None:
        bench_ret = benchmark.pct_change().dropna()

    # AI views → BL posterior
    views_dict = {t: ai_views[t]["expected_return"] for t in tickers if t in ai_views}
    conf_list  = [ai_views.get(t, {}).get("confidence", 0.5) for t in views_dict]

    try:
        bl = BlackLittermanModel(
            S,
            pi=mu_hist,
            absolute_views=views_dict,
            omega="idzorek",
            view_confidences=conf_list,
        )
        ret_bl = bl.bl_returns()
        # 폴백 기본값 0.0 (편향 중립)
        posterior_returns = {t: round(float(ret_bl.get(t, mu_hist.get(t, 0.0))), 4) for t in tickers}
        posterior_source = "black_litterman"
    except Exception as e:
        logger.warning("Black-Litterman 실패 — 사후 수익률을 과거 평균으로 대체",
                       exc_info=True)
        ret_bl = mu_hist
        posterior_returns = {t: round(float(mu_hist.get(t, 0.0)), 4) for t in tickers}
        # BL 이 실패하면 `posterior_returns` 는 **사후 분포가 아니라 과거 평균**
        # 이다. 값은 크게 달라진다 (실측: T0 이 +0.0345 → -0.0974). 예전에는
        # 응답에 그 사실이 없어서, AI 뷰가 반영된 값과 과거 평균이 같은 이름으로
        # 나갔다 — 프롬프트도 그걸 'AI 사후 수익률' 로 읽는다.
        posterior_source = "historical_mean"

    def _opt(mu: pd.Series, cov: pd.DataFrame, method: str, target: float | None = None) -> dict | None:
        try:
            ef = EfficientFrontier(mu, cov, weight_bounds=effective_bounds)
            if method == "max_sharpe":
                ef.max_sharpe(risk_free_rate=risk_free_rate)
            elif method == "min_volatility":
                ef.min_volatility()
            elif method == "efficient_return":
                if target is None:
                    return None
                ef.efficient_return(target)

            w = ef.clean_weights()
            # 미미한 비중을 버리고 재정규화. 각 값을 4자리로 반올림하면 합이 1에서
            # 미세하게 벗어나므로(예: 1.0001), 잔차를 최대 비중 종목에 흡수시켜
            # 표시되는 비중의 합이 정확히 1이 되도록 한다.
            raw = {t: float(wt) for t, wt in w.items() if wt > 1e-4}
            total = sum(raw.values())
            weights: dict[str, float] = {}
            if total > 0:
                weights = {t: round(wt / total, 4) for t, wt in raw.items()}
                residual = round(1.0 - sum(weights.values()), 4)
                if abs(residual) >= 1e-4:
                    top = max(weights, key=weights.get)
                    weights[top] = round(weights[top] + residual, 4)

            r, v, sh = ef.portfolio_performance(risk_free_rate=risk_free_rate, verbose=False)
            return {
                "weights":         weights,
                "expected_return": round(float(r), 4),
                "volatility":      round(float(v), 4),
                "sharpe_ratio":    round(float(sh), 4),
            }
        except Exception as ex:
            logger.warning("최적화 실패 (%s) — 그 조합은 결과에서 빠진다", method,
                           exc_info=True)
            return None

    def _attach_ext(result: dict | None) -> dict | None:
        """최적화 결과에 확장 리스크 지표를 인플레이스로 추가."""
        if result is None:
            return None
        ext = _compute_extended_metrics(
            result["weights"], daily_rets_raw, risk_free_rate, bench_ret
        )
        result.update(ext)
        return result

    def _opt_hrp(mu: pd.Series) -> dict | None:
        """HRP (Hierarchical Risk Parity) — 헤지 방어 배분.

        López de Prado 의 계층적 리스크 패리티. 마코위츠 계열이 공분산 **역행렬**을
        요구해 추정오차에 극도로 민감한 반면(종목 수가 늘수록 불안정), HRP 는 역행렬을
        쓰지 않는다.

        ① 상관계수를 거리로 변환해 계층적 클러스터링 → 성격이 비슷한 자산을 묶고
        ② 재귀적 이분할로 클러스터 **간** 리스크 패리티 배분 → 클러스터 **내** 재배분

        효과: 상관관계 높은 자산군(예: 기술주 다수)에 비중이 쏠리지 않고, 성격이
        다른 자산군(헤지 역할)에 자동으로 배분이 돌아간다. min_volatility 가 소수
        저변동 종목에 극단적으로 몰리던 문제를 구조적으로 해소한다.
        """
        try:
            # 공분산은 다른 카드와 동일한 Ledoit-Wolf 축소 추정치(S)를 쓰고,
            # 상관은 윈저화된 수익률에서 계산한다.
            corr = daily_rets_clean.corr().reindex(index=tickers, columns=tickers)
            w = hrp_weights(S.reindex(index=tickers, columns=tickers), corr)

            raw = {t: float(x) for t, x in w.items() if x > 1e-4}
            total = sum(raw.values())
            if total <= 0:
                return None
            weights = {t: round(x / total, 4) for t, x in raw.items()}
            residual = round(1.0 - sum(weights.values()), 4)
            if abs(residual) >= 1e-4:
                top = max(weights, key=weights.get)
                weights[top] = round(weights[top] + residual, 4)

            # 성과 지표는 다른 카드와 동일 기준(BL 사후 mu + 동일 공분산)으로 계산해
            # 카드 간 비교가 성립하도록 한다. HRPOpt.portfolio_performance 는
            # 자체 수익률 평균을 쓰므로 잣대가 달라진다.
            wv = np.array([weights.get(t, 0.0) for t in tickers])
            mu_v = np.array([float(mu.get(t, 0.0)) for t in tickers])
            exp_ret = float(wv @ mu_v)
            vol = float(np.sqrt(wv @ S.values @ wv))
            sharpe = (exp_ret - risk_free_rate) / vol if vol > 1e-12 else 0.0

            return {
                "weights":         weights,
                "expected_return": round(exp_ret, 4),
                "volatility":      round(vol, 4),
                "sharpe_ratio":    round(sharpe, 4),
            }
        except Exception as ex:
            logger.warning("HRP 최적화 실패 — 그 조합은 결과에서 빠진다",
                           exc_info=True)
            return None

    # ── Step 1: max_sharpe 두 가지 ───────────────────────────────────────────
    bl_max_sharpe = _attach_ext(_opt(ret_bl,   S, "max_sharpe"))
    hist_sharpe   = _attach_ext(_opt(mu_hist,  S, "max_sharpe"))

    # ── Step 2: HRP (헤지 방어) ───────────────────────────────────────────────
    # 기존 min_volatility 를 대체. 상관 클러스터 기반 배분이라 특정 종목 쏠림이 없고
    # 성격이 다른 자산군에 자동으로 비중이 돌아간다(자연 헤지).
    hrp = _attach_ext(_opt_hrp(ret_bl))

    # 프론티어 기준점은 여전히 최소분산 지점이어야 한다 (곡선의 좌측 꼭짓점).
    # HRP 는 프론티어 위의 점이 아니므로 별도로 계산한다.
    _mv = _opt(ret_bl, S, "min_volatility")
    r_mv = float(_mv["expected_return"]) if _mv else float(ret_bl.min())

    # ── Step 3: efficient_return (목표 수익률) ─────────────────────────────────
    r_max = float(ret_bl.max())
    r_hi  = r_max - 1e-3
    r_lo  = r_mv  + 5e-3
    if r_lo >= r_hi:
        r_lo = r_hi = (r_mv + r_max) / 2

    effective_target = float(np.clip(target_return, r_lo, r_hi))
    tgt_ret = _attach_ext(_opt(ret_bl, S, "efficient_return", effective_target))

    # ── Step 4: 효율적 프론티어 (30개 점 + 최소분산 꼭짓점) ──────────────────
    #
    # 곡선이 짧아지는 길이 **셋**이고 예전에는 하나만 로그에 남았다:
    #   ① 루프가 예외로 죽는다              → except 가 로그를 남겼다
    #   ② 어떤 목표수익에서 해가 없다        → `if pt:` 가 조용히 건너뛴다
    #   ③ 최소분산~최대수익 구간이 없다      → 루프가 아예 안 돈다 (꼭짓점 1점)
    #
    # 셋 다 **절반짜리 곡선이 화면에서 전체 효율적 프론티어로 그려지는** 결과가
    # 되고, 응답만 보면 짧아진 것이 데이터 한계인지 계산 실패인지 구별되지
    # 않는다. 위장하는 값이 0 이 아니라 **그럴듯한 곡선**이라 의심을 안 받는다.
    # 로그는 사후 추적용이고, 화면이 "일부 구간만 계산됨" 을 말하려면 응답이
    # 그 사실을 들고 있어야 한다 — `posterior_source` 와 같은 방식으로 싣는다.
    FRONTIER_POINTS = 30
    frontier: list[dict] = []
    frontier_requested = (1 if _mv else 0)
    frontier_reason: str | None = None
    try:
        if _mv:
            frontier.append({"return": _mv["expected_return"], "volatility": _mv["volatility"]})
        if r_hi > r_mv + 1e-3:
            frontier_requested += FRONTIER_POINTS
            gaps = 0
            for tr in np.linspace(r_mv + 1e-3, r_hi, FRONTIER_POINTS):
                pt = _opt(ret_bl, S, "efficient_return", tr)
                if pt:
                    frontier.append({"return": pt["expected_return"], "volatility": pt["volatility"]})
                else:
                    gaps += 1
            if gaps:
                # `_opt` 가 점마다 로그를 남기지만, **곡선이 짧아졌다는 사실**은
                # 아무도 세지 않았다. 여기서 세서 응답에 싣는다.
                frontier_reason = "solver_gaps"
                logger.warning("효율적 프론티어 %d/%d 점에서 해를 찾지 못했다 — "
                               "곡선이 그만큼 짧아진다", gaps, FRONTIER_POINTS)
        elif _mv:
            # 최소분산 지점과 최대수익 지점이 사실상 같다 — 그릴 곡선이 없다.
            # 실패가 아니라 입력의 성질이므로 이유를 구별해 싣는다.
            frontier_reason = "no_return_span"
    except Exception:
        # 루프 중간에 죽으면 그때까지 쌓인 점이 남는다. 지우지 않는다 — 점
        # 몇 개라도 있는 것이 없는 것보다 낫다. 대신 절단 사실을 응답에 싣는다.
        frontier_reason = "exception"
        logger.warning("효율적 프론티어 생성 실패 — %d/%d개 점까지만 만들어졌다 "
                       "(곡선이 잘린 채 그려진다)", len(frontier),
                       frontier_requested, exc_info=True)

    # 상관관계
    corr = daily_rets_raw.corr()

    return {
        "optimizations": {
            "black_litterman": bl_max_sharpe,
            "max_sharpe_hist": hist_sharpe,
            "hrp":             hrp,
            "target_return":   tgt_ret,
        },
        "effective_target_return": round(effective_target, 4),
        "posterior_returns": posterior_returns,
        # 어느 수익률 벡터로 만든 값인지 응답이 말한다. BL 이 실패하면
        # `posterior_returns` 가 과거 평균으로 바뀌는데 이름은 그대로였다.
        "posterior_source": posterior_source,
        # `max_sharpe_hist` 는 사후 수익률이 아니라 **과거 평균**으로 최적화한다.
        # 그 벡터가 응답에 없어서 그 모드의 `expected_return` 을 응답 안의
        # 어떤 값으로도 재현할 수 없었다 (실측 차이 15.1pp). 함께 싣는다.
        "historical_returns": {t: round(float(mu_hist.get(t, 0.0)), 4) for t in tickers},
        "frontier":   frontier,
        # 곡선이 요청한 만큼 만들어졌는가. `frontier` 만 보면 짧은 곡선과
        # 온전한 곡선이 구별되지 않는다 — 화면은 둘 다 '효율적 프론티어' 로
        # 그린다. 이유 코드는 `no_return_span`(구간이 없다) ·
        # `solver_gaps`(일부 점에 해가 없다) · `exception`(중간에 죽었다).
        "frontier_complete":  frontier_reason is None,
        "frontier_points":    len(frontier),
        "frontier_requested": frontier_requested,
        "frontier_reason":    frontier_reason,
        "correlation": {
            "tickers": list(corr.index),
            "matrix":  [[round(float(v), 3) for v in row] for row in corr.values],
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def run_ai_optimization(
    tickers:              list[str],
    period:               str   = "1y",
    target_return:        float = 0.10,
    risk_free_rate:       float = 0.04,
    holding_period_years: float = 1.0,
    weight_bounds:        tuple[float, float] = (0.0, 1.0),
    on_stage=None,  # Optional[Callable[[int, str], None]]
    market:               str   = "US",
) -> dict:
    """
    Full pipeline — 모든 IO 병렬:
      prices(yfinance) ‖ news(perplexity) ‖ fundamentals(yfinance)
      → Claude Haiku AI views → BL + EfficientFrontier → result
    """
    def _notify(n: int, text: str) -> None:
        try:
            if on_stage:
                on_stage(n, text)
        except Exception:
            # 진행 알림 실패가 최적화를 막을 이유는 없다. 다만 콜백이 깨져
            # 있으면 단계마다 터지므로 warning 은 소음이 된다 — debug 로 둔다.
            logger.debug("진행 알림 실패 (단계 %d) — 최적화는 계속한다", n,
                         exc_info=True)

    # ① 투자기간 기반 데이터 기간 자동 선택 (user 입력 period 무시)
    auto_period = _select_data_period(holding_period_years)

    # ② 병렬 IO (가격·뉴스·펀더멘털·SPY 벤치마크 동시 수집)
    _notify(1, f"가격·뉴스·펀더멘털 병렬 수집 중... (데이터기간: {auto_period})")

    def _get_prices():
        return _fetch_prices(tickers, period=auto_period)

    def _get_news():
        return _fetch_news(tickers, market)

    def _get_fundamentals():
        return _gather_fundamentals(tickers)

    # 베타의 비교 대상은 **그 시장의 기준 지수**다. `SPY` 로 고정돼 있어서,
    # `/terminal` 이 "베타 (코스피 대비) 1.22" 를 보여주는 동안 `/optimizer` 의
    # 같은 이름 값은 S&P 대비였다. 어느 쪽도 계산이 틀리지 않았는데 나란히
    # 놓으면 둘 다 못 믿게 된다. 지수 선택 규칙을 아는 코드는
    # `markets.benchmark_for` 하나여야 한다.
    from backend.services.markets import benchmark_for, get_market
    bench_ticker = benchmark_for(market)
    bench_label  = get_market(market).indices.get(bench_ticker, bench_ticker)

    def _get_benchmark():
        try:
            import yfinance as yf
            bm = yf.download(
                bench_ticker, period=auto_period, progress=False, auto_adjust=True
            )
            col = "Close" if "Close" in bm.columns else bm.columns[0]
            return bm[col].squeeze()
        except Exception:
            # 벤치마크가 없으면 `_compute_extended_metrics` 가 베타를 비운다.
            # 그쪽이 `beta_reason="no_benchmark"` 를 함께 실으므로, 화면의
            # "베타 —" 가 왜 비었는지 응답만 보고 알 수 있다.
            logger.warning("벤치마크 %s 수집 실패 — 확장 지표의 베타가 빠진다",
                           bench_ticker, exc_info=True)
            return None

    with ThreadPoolExecutor(max_workers=4) as ex:
        f_prices    = ex.submit(_get_prices)
        f_news      = ex.submit(_get_news)
        f_fund      = ex.submit(_get_fundamentals)
        f_benchmark = ex.submit(_get_benchmark)
        prices       = f_prices.result()
        news_ctx     = f_news.result()
        fundamentals = f_fund.result()
        benchmark    = f_benchmark.result()

    if prices.empty:
        raise ValueError("가격 데이터 조회 실패 — 종목 티커를 확인해주세요.")

    valid = [t for t in tickers if t in prices.columns and prices[t].notna().sum() >= 60]
    if len(valid) < 2:
        raise ValueError(f"충분한 데이터를 가진 종목이 2개 미만입니다 (유효: {valid})")

    prices = prices[valid].dropna()

    # ② 가격 통계
    price_stats = _compute_price_stats(prices)

    # ③ AI views (펀더멘털 + 뉴스 기반)
    _notify(2, "AI 밸류에이션·모멘텀 분석 중...")
    ai_views = _generate_ai_views(valid, price_stats, fundamentals, news_ctx,
                                  holding_period_years, market)

    # Fallback: AI 실패 시 과거수익(신뢰도 30%) — or 0.0으로 중립 기본값
    if not ai_views:
        ai_views = {
            t: {
                "expected_return": float(np.clip((price_stats.get(t, {}).get("ret_1y") or 0.0) / 100.0, -0.4, 0.5)),
                "confidence":  0.30,
                "sentiment":   "Neutral",
                "key_driver":  "AI 분석 불가 — 과거 수익률 기반 추정 (신뢰도 낮음)",
            }
            for t in valid
        }

    # ④ 최적화
    _notify(3, "Black-Litterman + 효율적 프론티어 계산 중...")
    opt = _run_pypfopt(
        prices, ai_views, target_return, risk_free_rate, weight_bounds,
        benchmark=benchmark,
    )

    return {
        "tickers":                valid,
        "ai_views":               ai_views,
        "price_stats":            price_stats,
        "fundamentals":           {t: fundamentals.get(t, {}) for t in valid},
        "optimizations":          opt["optimizations"],
        "effective_target_return": opt["effective_target_return"],
        "posterior_returns":      opt["posterior_returns"],
        # `_run_pypfopt` 이 만드는데 이 응답이 안 실어서 **클라이언트에 한 번도
        # 도달하지 않았다.** 라우터는 이 함수만 부른다. BL 이 실패하면
        # `posterior_returns` 가 과거 평균으로 바뀌는데 그 사실을 말하는 필드가
        # 여기서 잘려 나갔고, `max_sharpe_hist` 의 기대수익을 재현할 벡터도 없었다.
        "posterior_source":       opt["posterior_source"],
        "historical_returns":     opt["historical_returns"],
        "frontier":               opt["frontier"],
        # 절단 사실을 파이프라인 응답에도 싣는다 — 라우터는 이 함수만 부르므로
        # 여기서 빠뜨리면 `posterior_source` 처럼 클라이언트에 도달하지 않는다.
        "frontier_complete":      opt["frontier_complete"],
        "frontier_points":        opt["frontier_points"],
        "frontier_requested":     opt["frontier_requested"],
        "frontier_reason":        opt["frontier_reason"],
        "correlation":            opt["correlation"],
        "data_period":            auto_period,
        # 베타가 무엇 대비인지 응답이 말한다. 화면이 "베타" 라고만 쓰면
        # 사용자는 기준을 모르고, 한국 포트폴리오에 S&P 대비 값이 나가도
        # 알아챌 수 없다. 키 이름에 지수를 박지 않는 것도 같은 이유다.
        "beta_benchmark":         bench_ticker,
        "beta_benchmark_label":   bench_label,
    }

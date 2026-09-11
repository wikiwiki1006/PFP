"""
services/quant_metrics.py
─────────────────────────
종목 상세 화면의 퀀트 지표 — 퀀트 스코어 / 패닉 점수 / 포트폴리오 최적화 맥락.

설계 원칙
  · 추가 데이터 소스 없이 이미 받아온 OHLCV + yfinance info 로만 계산한다
    (종목 상세는 이미 두 값을 받으므로 네트워크 호출이 늘지 않는다).
  · 모든 점수는 **구성 요소를 함께 반환**한다. 합성 점수만 보여주면 실제보다
    정밀해 보이므로, 화면에서 근거를 펼쳐볼 수 있어야 한다.
  · 가중치는 학술적으로 확립된 팩터(모멘텀·퀄리티·밸류·추세)를 쓰되,
    배분 자체는 백테스트로 검증된 값이 아니다 — 상수로 분리해 조정 가능하게 둔다.
"""
from __future__ import annotations

import logging
import math
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def _f(v, default: float = 0.0) -> float:
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default


def _clip100(x: float) -> float:
    return float(np.clip(x, 0.0, 100.0))


# ── 퀀트 스코어 가중치 ────────────────────────────────────────────────────────
# 합이 1.0 이어야 한다. 조정 시 각 팩터의 의미를 함께 확인할 것.
QUANT_WEIGHTS = {
    "momentum": 0.35,   # 가격 모멘텀 — 가장 강건한 단일 팩터
    "trend":    0.25,   # 추세 효율(ER) — 방향성 있는 움직임인지
    "quality":  0.20,   # ROE·마진 — 사업의 질
    "value":    0.20,   # PEG·PBR — 성장 대비 가격
}


def compute_quant_score(close: pd.Series, info: dict, er: Optional[float] = None) -> dict:
    """4개 팩터 합성 퀀트 스코어 (0~100).

    각 팩터를 0~100 으로 정규화한 뒤 가중 평균한다.
    반환값에 팩터별 원점수를 포함해 화면에서 근거를 보여줄 수 있게 한다.
    """
    c = close.dropna().astype(float)
    if len(c) < 30:
        return {"score": None, "label": "데이터 부족", "factors": {}}

    # ① 모멘텀 — 1M/3M/6M 수익률 가중 (최근일수록 크게)
    def ret(days: int) -> float:
        return (float(c.iloc[-1]) / float(c.iloc[-days]) - 1) * 100 if len(c) > days else 0.0
    raw_mom = 0.5 * ret(21) + 0.3 * ret(63) + 0.2 * ret(126)
    # ±33% 를 0~100 양끝으로 매핑 (연 33% 는 매우 강한 모멘텀)
    momentum = _clip100(50 + raw_mom * 1.5)

    # ② 추세 효율 — ER 이 없으면 여기서 계산
    if er is None:
        from backend.services.trading_signals import efficiency_ratio
        er = float(efficiency_ratio(c).iloc[-1])
    # ER 0.625 이상이면 만점 (한 방향 직진에 가까움)
    trend = _clip100(er * 160)

    # ③ 퀄리티 — ROE + 순이익률
    roe = _f(info.get("returnOnEquity")) * 100
    pm  = _f(info.get("profitMargins")) * 100
    quality = _clip100(roe * 0.6 + pm * 1.2)

    # ④ 밸류 — PEG 우선, 없으면 Forward P/E 로 대체
    peg = info.get("pegRatio")
    if peg is not None and _f(peg) > 0:
        value = _clip100(100 - _f(peg) * 40)          # PEG 1.0 → 60점, 2.5 → 0점
    else:
        fpe = _f(info.get("forwardPE"), 25.0)
        value = _clip100(100 - max(fpe, 0) * 2.5)     # Fwd P/E 20 → 50점

    factors = {"momentum": round(momentum), "trend": round(trend),
               "quality": round(quality), "value": round(value)}
    score = sum(factors[k] * w for k, w in QUANT_WEIGHTS.items())

    if   score >= 75: label = "STRONG BUY / 다중 팩터 우위"
    elif score >= 60: label = "BULLISH / 모멘텀 우위"
    elif score >= 45: label = "NEUTRAL / 혼조"
    elif score >= 30: label = "BEARISH / 팩터 약세"
    else:             label = "WEAK / 전 팩터 열위"

    return {
        "score":   round(_clip100(score)),
        "label":   label,
        "factors": factors,
        "weights": QUANT_WEIGHTS,
    }


def compute_panic_score(close: pd.Series, volume: Optional[pd.Series] = None) -> dict:
    """패닉/탐욕 점수 (0=극단적 공포, 100=탐욕).

    CNN Fear & Greed 의 구성을 종목 단위로 옮긴 것. 가격·거래량만 쓰므로
    어떤 종목에서도 계산 가능하다.
    낮을수록 과매도 → 역발상 매수 구간, 높을수록 과열.
    """
    c = close.dropna().astype(float)
    if len(c) < 30:
        return {"score": None, "status": "데이터 부족", "components": {}}

    ret = c.pct_change().dropna()

    # ① RSI(14) — 과매수/과매도
    r = ret.tail(14)
    up = float(r[r > 0].mean()) if (r > 0).any() else 0.0
    dn = float(-r[r < 0].mean()) if (r < 0).any() else 1e-9
    rsi = 100 - 100 / (1 + up / max(dn, 1e-9))

    # ② 현재 낙폭 — 고점 대비 -30% 를 공포 바닥으로
    dd = float((c / c.cummax() - 1).iloc[-1])
    dd_score = _clip100((1 + dd / 0.30) * 100)

    # ③ 52주 고점과의 거리 — -25% 를 바닥으로
    hi = float(c.iloc[-1] / c.tail(252).max() - 1)
    hi_score = _clip100((1 + hi / 0.25) * 100)

    # ④ 거래량 급증 — 패닉 매도 때 거래량이 튄다
    if volume is not None and len(volume.dropna()) >= 60:
        v = volume.dropna().astype(float)
        vr = float(v.tail(5).mean() / max(float(v.tail(60).mean()), 1e-9))
    else:
        vr = 1.0
    vol_score = _clip100(100 - (vr - 1) * 100)

    # ⑤ 변동성 확대 — 단기 변동성이 장기 대비 커지면 공포
    lt = float(ret.tail(252).std()) or 1e-9
    vz = float(ret.tail(20).std()) / lt
    vola_score = _clip100(100 - (vz - 1) * 100)

    comps = {
        "rsi":         round(rsi),
        "drawdown":    round(dd_score),
        "vs_52w_high": round(hi_score),
        "volume":      round(vol_score),
        "volatility":  round(vola_score),
    }
    score = (0.35 * comps["rsi"] + 0.25 * comps["drawdown"] + 0.20 * comps["vs_52w_high"]
             + 0.10 * comps["volume"] + 0.10 * comps["volatility"])

    if   score <= 20: status = "Extreme Fear — 역발상 매수 구간"
    elif score <= 40: status = "Fear — 과매도 진입"
    elif score <= 60: status = "Neutral — 중립"
    elif score <= 80: status = "Greed — 과열 주의"
    else:             status = "Extreme Greed — 조정 위험"

    return {
        "score":      round(_clip100(score)),
        "status":     status,
        "components": comps,
        "raw": {"drawdown_pct": round(dd * 100, 2), "vs_52w_high_pct": round(hi * 100, 2),
                "volume_ratio": round(vr, 2)},
    }


def compute_optimizer_context(
    ticker: str,
    holdings: dict,
    close_df: pd.DataFrame,
    ticker_close: pd.Series,
) -> dict:
    """포트폴리오 맥락에서의 최적 비중·리스크 기여도·상관관계·베타.

    종목 하나만으로는 구할 수 없는 값들이다 — 보유 종목 전체와 함께 계산한다.
    목표 비중은 포트폴리오 최적화 화면과 같은 HRP 를 쓴다(공분산 역행렬을 쓰지
    않아 종목 수가 적어도 안정적이고, 상관 높은 자산에 쏠리지 않는다).

    보유 종목이 없으면 최적화가 성립하지 않으므로 None 필드로 반환한다.
    """
    empty = {
        "target_weight": None, "current_weight": None, "risk_contribution": None,
        "correlation": None, "correlation_label": None, "beta_exposure": None,
        "in_portfolio": False, "note": "보유 종목이 없어 포트폴리오 맥락을 계산할 수 없습니다",
    }
    stock = [t for t in (holdings or {}) if t != "CASH"]
    if not stock or close_df is None or close_df.empty:
        return empty

    # 대상 종목을 포함한 유니버스 구성 (미보유 종목이면 '편입 시' 시나리오)
    universe = sorted(set(stock) | {ticker})
    cols = [t for t in universe if t in close_df.columns]
    px = close_df[cols].dropna(how="all")
    if ticker not in px.columns:
        px = px.join(ticker_close.rename(ticker), how="inner")
    px = px.dropna()
    if px.shape[1] < 2 or len(px) < 60:
        return {**empty, "note": "공통 가격 이력이 부족해 계산할 수 없습니다"}

    rets = px.pct_change().dropna()
    if rets.empty:
        return {**empty, "note": "수익률 계산 불가"}

    cov = rets.cov() * 252
    corr = rets.corr()

    # ── 목표 비중 (HRP) ───────────────────────────────────────────────────
    target = None
    try:
        from backend.services.portfolio_optimizer import hrp_weights
        w = hrp_weights(cov, corr)
        target = round(float(w.get(ticker, 0.0)) * 100, 2)
    except Exception:
        # 개별 로그로 남긴다 — 실제 DB 표본 120회에서 예외가 0건이라 소음이
        # 되지 않고, 드문 만큼 났을 때 이유가 필요하다.
        #
        # 도달 경로가 있다: 보유 종목 중 하나가 상수 가격이면(거래정지·상장폐지
        # 대기) 그 열의 분산이 0 이라 `rets.corr()` 에 NaN 이 생기고, scipy 가
        # "condensed distance matrix must contain only finite values" 로 거부한다.
        # 그러면 **목표 비중만** 조용히 사라진다 — 나머지 필드는 다 계산되고
        # note 도 None 이라 화면에는 이유 없이 '—' 만 뜬다.
        # NaN 이 있는 열을 나열하면 전부 나온다 — 한 종목의 NaN 상관이 모든
        # 행으로 퍼지기 때문이다. 원인은 **분산이 0 인 열**이라 그것만 짚는다.
        flat = [c for c in rets.columns if not (float(rets[c].var()) > 0)]
        logger.warning(
            "HRP 목표 비중 계산 실패 (%s, 유니버스 %d종목) — 목표 비중만 빠진다. "
            "가격이 상수인 종목: %s",
            ticker, len(cols), flat or "없음", exc_info=True,
        )

    # ── 현재 비중 (평가금액 기준) ─────────────────────────────────────────
    last = px.iloc[-1]
    values = {t: _f(holdings.get(t, {}).get("q")) * _f(last.get(t)) for t in cols if t in holdings}
    cash = _f((holdings.get("CASH") or {}).get("q"))
    total = sum(values.values()) + cash
    # 평가액 합이 0 이면 비중이 정의되지 않는다 — 0% 로 돌려주면 "보유하지 않음"과
    # 구별되지 않는다. 미보유 종목의 0.0 은 진짜 0% 라서 그대로 둔다.
    current = round(values.get(ticker, 0.0) / total * 100, 2) if total > 0 else None

    # ── 리스크 기여도 ─────────────────────────────────────────────────────
    # RC_i = w_i * (Σw)_i / (wᵀΣw) — 포트폴리오 총위험 중 이 종목이 차지하는 몫.
    # 비중이 작아도 변동성·상관이 높으면 기여도는 클 수 있다.
    risk_contrib = None
    if total > 0 and values:
        wv = np.array([values.get(t, 0.0) / total for t in cols])
        if wv.sum() > 0:
            S = cov.loc[cols, cols].values
            port_var = float(wv @ S @ wv)
            if port_var > 1e-12:
                mrc = S @ wv
                idx = cols.index(ticker)
                risk_contrib = round(float(wv[idx] * mrc[idx] / port_var) * 100, 2)

    # ── 나머지 보유분과의 상관관계 (해당 종목 제외 가중 포트폴리오 대비) ──
    correlation = None
    others = [t for t in cols if t != ticker and values.get(t, 0.0) > 0]
    if others:
        ow = np.array([values[t] for t in others]); ow = ow / ow.sum()
        port_ret = (rets[others] * ow).sum(axis=1)
        correlation = round(float(rets[ticker].corr(port_ret)), 3)
    if correlation is None:
        label = None
    elif correlation >= 0.7:  label = "High — 분산효과 낮음"
    elif correlation >= 0.4:  label = "Moderate"
    else:                     label = "Low — 분산효과 큼"

    # ── 베타 (SPY 대비) ───────────────────────────────────────────────────
    beta = None
    try:
        if "^GSPC" in close_df.columns:
            mkt = close_df["^GSPC"].pct_change().dropna()
            common = rets.index.intersection(mkt.index)
            if len(common) >= 60:
                mv = float(mkt.loc[common].var())
                if mv > 1e-12:
                    beta = round(float(np.cov(rets[ticker].loc[common], mkt.loc[common])[0, 1] / mv), 3)
    except Exception:
        # 이 블록은 예외 없이도 None 이 된다 (^GSPC 없음 · 공통 구간 60일 미만 ·
        # 시장 분산 0). 그 셋은 정상 경로이므로 로그를 남기지 않는다 — 여기는
        # **예외만** 잡으므로 실제로 계산이 깨진 경우다.
        logger.warning("베타 계산 실패 (%s) — 베타만 빠진다", ticker, exc_info=True)

    return {
        "target_weight":     target,
        "current_weight":    current,
        "risk_contribution": risk_contrib,
        "correlation":       correlation,
        "correlation_label": label,
        "beta_exposure":     beta,
        "in_portfolio":      ticker in stock,
        "note":              None if ticker in stock else "미보유 — 편입 시 기준 목표 비중",
    }

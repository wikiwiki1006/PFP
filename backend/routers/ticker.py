"""
routers/ticker.py
──────────────────
종목 상세 분석 API (차트 데이터, 펀더멘털, 성과, 리스크, 기술적 지표)
"""
from __future__ import annotations

import logging
import math
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf
from fastapi import Depends, APIRouter, Header, HTTPException, Query

from backend.services.auth import optional_user
from backend.services.markets import market_param

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ticker", tags=["ticker"])

# ── 헬퍼 ─────────────────────────────────────────────────────────────────────

def _safe(v, default=None):
    if v is None:
        return default
    try:
        f = float(v)
        return default if (math.isnan(f) or math.isinf(f)) else f
    except Exception:
        return default


def _calc_rsi(closes: pd.Series, period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    delta = closes.diff()
    gain = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    rs = gain.iloc[-1] / loss.iloc[-1] if loss.iloc[-1] != 0 else float("inf")
    return round(100 - (100 / (1 + rs)), 1)


def _calc_bb(closes: pd.Series, period: int = 20, n_std: float = 2.0) -> pd.DataFrame:
    mid = closes.rolling(period).mean()
    std = closes.rolling(period).std()
    return pd.DataFrame({"bb_upper": mid + n_std * std, "bb_mid": mid, "bb_lower": mid - n_std * std})


def _calc_stoch(hist: pd.DataFrame, k_period: int = 14, smooth_k: int = 3, smooth_d: int = 3) -> pd.DataFrame:
    low_min  = hist["Low"].rolling(k_period).min()
    high_max = hist["High"].rolling(k_period).max()
    raw_k = 100 * (hist["Close"] - low_min) / (high_max - low_min + 1e-9)
    k = raw_k.rolling(smooth_k).mean()
    d = k.rolling(smooth_d).mean()
    return pd.DataFrame({"stoch_k": k, "stoch_d": d})


_PERIOD_MAP = {
    "1m": "1mo", "3m": "3mo", "6m": "6mo",
    "1y": "1y", "2y": "2y", "5y": "5y",
}


# ── 엔드포인트 ────────────────────────────────────────────────────────────────


def _build_optimizer_block(sym: str, uid: str, closes=None, market: str = "US") -> dict:
    """포트폴리오 맥락 — 사용자 보유 종목에 의존하므로 공용 캐시에 넣지 않는다.

    네트워크 호출은 get_close_df(대부분 캐시 적중) 뿐이라 매 요청 계산해도 가볍다.
    closes=None 이면(공용 본문이 캐시 적중한 경로) 가격 캐시에서 종가를 얻는다.
    """
    optimizer = {"target_weight": None, "current_weight": None, "risk_contribution": None,
                 "correlation": None, "correlation_label": None, "beta_exposure": None,
                 "in_portfolio": False, "note": None}

    # 보유 종목이 바뀌지 않는 한 결과가 같으므로 짧게 캐싱한다.
    # (공용 본문이 DB 캐시에 적중해도 여기서 매번 공분산을 다시 계산하면
    #  응답이 수 초대로 남는다 — 실측 2.8s → 0.3s)
    from backend.services.market_data import _cache_get as _cg, _cache_put as _cp
    _ok = f"opt_ctx_{sym}_{uid}"
    _hit = _cg(_ok, 300)
    if _hit is not None:
        return _hit

    try:
        from backend.services.quant_metrics import compute_optimizer_context
        from backend.db.portfolio_repo import get_holdings
        from backend.services.market_data import get_close_df
        holdings = get_holdings(uid, market=market)
        stock = [t for t in holdings if t != "CASH"]
        if not stock:
            optimizer["note"] = "보유 종목이 없어 포트폴리오 맥락을 계산할 수 없습니다"
            _cp(_ok, optimizer)
            return optimizer
        close_df = get_close_df(sorted(set(stock) | {sym}) + ["^GSPC"],
                                period="1y", ttl=1800, include_market=False)
        if closes is None:
            if sym not in close_df.columns:
                optimizer["note"] = "가격 이력이 없어 포트폴리오 맥락을 계산할 수 없습니다"
                _cp(_ok, optimizer)
                return optimizer
            closes = close_df[sym].dropna()
        res = compute_optimizer_context(sym, holdings, close_df, closes)
        _cp(_ok, res)
        return res
    except Exception as e:
        logger.warning(f"포트폴리오 맥락 계산 실패 (ticker={sym}): {e}")
        optimizer["note"] = "포트폴리오 맥락 계산 실패"
        return optimizer


def _build_quant_block(sym: str, uid: str, closes, hist, info: dict) -> dict:
    """퀀트 스코어 · 시장국면 · 패닉 점수 · 포트폴리오 최적화 맥락.

    예전에는 전부 하드코딩된 자리표시자였다. 실제 계산으로 대체하되,
    일부가 실패해도 나머지는 표시되도록 각 블록을 독립적으로 방어한다.
    """
    from backend.services.quant_metrics import compute_quant_score, compute_panic_score
    from backend.services.trading_signals import detect_regime_er

    # ── 시장 국면 (ER, 소급 보정 적용) ────────────────────────────────
    # 실패를 REGIME_SIDEWAYS 로 떨어뜨리지 않는다. '횡보'는 실제 시장 판단이라,
    # 계산이 안 된 것을 그렇게 표시하면 사용자는 근거 있는 결론으로 읽는다.
    # 바로 아래 quant·panic 은 '계산 불가'를 명시하는데 여기만 값을 지어내고
    # 있었다 — 같은 응답 안에서 한쪽은 모른다고 하고 한쪽은 단정한 셈이다.
    regime, regime_er = None, None
    try:
        r = detect_regime_er(closes)
        regime, regime_er = r["current_regime"], r["current_er"]
    except Exception as e:
        logger.warning(f"시장 국면 계산 실패 (ticker={sym}): {e}")

    _KO = {"Bull": "상승 추세", "Bear": "하락 추세", "Sideways": "횡보"}

    quant = {"score": None, "label": "계산 불가", "factors": {}}
    try:
        quant = compute_quant_score(closes, info or {}, er=regime_er)
    except Exception as e:
        logger.warning(f"퀀트 스코어 계산 실패 (ticker={sym}): {e}")

    panic = {"score": None, "status": "계산 불가", "components": {}}
    try:
        vol = hist["Volume"] if "Volume" in getattr(hist, "columns", []) else None
        panic = compute_panic_score(closes, vol)
    except Exception as e:
        logger.warning(f"패닉 점수 계산 실패 (ticker={sym}): {e}")

    return {
        "score":        quant.get("score"),
        "score_label":  quant.get("label"),
        "factors":      quant.get("factors", {}),
        "regime":       _KO.get(regime, regime) if regime else "계산 불가",
        "regime_code":  regime,
        "regime_er":    regime_er,
        "panic_score":  panic.get("score"),
        "panic_status": panic.get("status"),
        "panic_components": panic.get("components", {}),
    }


@router.get("/{ticker}/detail")
def get_ticker_detail(
    ticker: str,
    period: str = Query("1y", regex="^(1m|3m|6m|1y|2y|5y)$"),
    _auth: Optional[dict] = Depends(optional_user),
    market: str = Depends(market_param),
):
    """종목 상세: OHLCV + 이평선/BB/스토케스틱 + 펀더멘털 + 성과 + 리스크 + VaR

    yfinance history + info 를 매 요청마다 받으면 1초 가까이 걸린다.
    같은 종목·기간 요청은 대부분 반복 조회(모달 재오픈·탭 전환)이므로
    결과 전체를 메모리 캐시에 올린다. 장중에는 짧게, 장외에는 길게 유지한다.
    """
    sym = ticker.upper()
    yf_period = _PERIOD_MAP.get(period, "1y")

    from backend.services.market_data import _cache_get, _cache_put

    # 비로그인도 차트·지표는 볼 수 있다. optimizer 만 로그인 시 계산된다.
    uid = _auth["uid"] if _auth else None

    # ── 캐시 2단 구조 ────────────────────────────────────────────────────
    # 공용 본문(차트·지표·퀀트·패닉·VaR)은 일봉 기반이라 하루 1회만 계산하면 된다.
    #   1) 프로세스 메모리 — 같은 인스턴스 내 반복 조회
    #   2) DB(ticker_analytics) — 인스턴스·사용자를 넘어 24시간 공유
    # optimizer 는 사용자 보유 종목에 의존하므로 캐시하지 않고 매번 계산한다.
    from backend.db import ticker_analytics_repo as ta_repo

    mem_key = f"ticker_detail_{sym}_{period}"
    shared = _cache_get(mem_key, 900)
    if shared is None:
        shared = ta_repo.get_cached(sym, period)
        if shared is not None:
            _cache_put(mem_key, shared)

    if shared is not None:
        out = dict(shared)
        out["quant"] = {**out.get("quant", {}),
                        "optimizer": _build_optimizer_block(sym, uid, None, market)}
        return out

    try:
        t = yf.Ticker(sym)
        hist = t.history(period=yf_period, auto_adjust=True)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"yfinance 오류: {e}")

    # 장중 부분 데이터(오늘 행) NaN Close 제거 — yfinance는 장중에 Close=NaN 행을 반환할 수 있음
    hist = hist[hist["Close"].notna()]

    if hist.empty or len(hist) < 5:
        raise HTTPException(status_code=404, detail=f"{sym} 데이터 없음")

    # ── 종가 Series ──────────────────────────────────────────────────────────
    closes = hist["Close"]
    current = float(closes.iloc[-1])
    prev    = float(closes.iloc[-2]) if len(closes) > 1 else current

    # ── 이평선 ──────────────────────────────────────────────────────────────
    ma20  = closes.rolling(20).mean()
    ma50  = closes.rolling(50).mean()
    ma200 = closes.rolling(200).mean()

    # ── BB ──────────────────────────────────────────────────────────────────
    bb = _calc_bb(closes)

    # ── 스토케스틱 ──────────────────────────────────────────────────────────
    stoch = _calc_stoch(hist)

    # ── OHLCV + 지표 합산 ───────────────────────────────────────────────────
    def _fv(s: pd.Series, i: int) -> Optional[float]:
        v = s.iloc[i]
        return None if (pd.isna(v) or math.isinf(float(v))) else round(float(v), 4)

    ohlcv = []
    for i, (dt, row) in enumerate(hist.iterrows()):
        ohlcv.append({
            "date":     dt.strftime("%Y-%m-%d"),
            "open":     round(float(row["Open"]),   4),
            "high":     round(float(row["High"]),   4),
            "low":      round(float(row["Low"]),    4),
            "close":    round(float(row["Close"]),  4),
            "volume":   int(row["Volume"]),
            "ma20":     _fv(ma20,  i),
            "ma50":     _fv(ma50,  i),
            "ma200":    _fv(ma200, i),
            "bb_upper": _fv(bb["bb_upper"], i),
            "bb_mid":   _fv(bb["bb_mid"],   i),
            "bb_lower": _fv(bb["bb_lower"], i),
            "stoch_k":  _fv(stoch["stoch_k"], i),
            "stoch_d":  _fv(stoch["stoch_d"], i),
        })

    # ── 펀더멘털 ────────────────────────────────────────────────────────────
    info: dict = {}
    try:
        info = t.info or {}
    except Exception:
        pass

    def _fmt_cap(v) -> str:
        """시가총액. 통화는 **응답에서** 읽는다 (§1.4).

        'USD' 가 하드코딩돼 있었다. 삼성전자 시가총액 1,709조원이
        `1705.7T USD` 로 표시됐다 — 1,709조 달러다. info 안에 이미
        currency='KRW' 가 있는데 보지 않았다.

        시장이 아니라 응답에서 읽는 이유는 해외 상장·ADR 때문이다. 시장이
        KR 이어도 통화가 USD 인 종목이 있다.

        구간·자릿수를 여기서 다시 구현하지 않는다. report_writer._fmt_amount
        가 단일 출처이고, 원화를 조·억으로 끊는 규칙도 거기 있다 — 'B'(십억)
        는 원화에 쓰지 않는 단위라 숫자가 맞아도 달러로 오해된다.
        """
        if not v:
            return "N/A"
        from backend.services.report_writer import _fmt_amount
        currency = (info.get("financialCurrency")
                    or info.get("currency")
                    or "USD")
        return _fmt_amount(v, currency)

    # ── 수익률 ──────────────────────────────────────────────────────────────
    def _perf(n: int) -> Optional[float]:
        if len(closes) <= n:
            return None
        return round((current / float(closes.iloc[-n - 1]) - 1) * 100, 2)

    # YTD: 올해 첫 거래일 대비
    this_year = hist.index[-1].year
    ytd_hist  = hist[hist.index.year == this_year]["Close"]
    ytd = round((current / float(ytd_hist.iloc[0]) - 1) * 100, 2) if len(ytd_hist) > 1 else None

    # 52주 고/저가 (1y 이상 데이터가 없으면 수집된 전체 범위)
    s52 = hist["Close"].tail(252) if len(hist) >= 252 else hist["Close"]

    # ── 리스크 지표 ─────────────────────────────────────────────────────────
    returns = closes.pct_change().dropna()
    vol_ann = round(float(returns.std() * math.sqrt(252) * 100), 1) if len(returns) >= 10 else None
    rsi14   = _calc_rsi(closes)
    beta    = _safe(info.get("beta"), 1.0)

    # ── VaR (Historical Simulation, 95%) ────────────────────────────────────
    ret_1y = returns.tail(252)
    var95: Optional[float] = None
    return_dist: list[dict] = []
    if len(ret_1y) >= 30:
        var95 = round(float(np.percentile(ret_1y.values, 5) * 100), 2)
        counts, bins = np.histogram(ret_1y.values * 100, bins=40)
        return_dist = [
            {"x": round(float(b), 3), "count": int(c)}
            for b, c in zip(bins[:-1], counts)
        ]

    # 배당수익률이 없는 것과 0% 인 것은 다르다. yfinance 가 필드를 안 주면
    # '무배당' 이 아니라 '모름' 이다 — 0.0 으로 채우면 배당주를 무배당으로
    # 오해하고 후보에서 빼게 된다. 바로 옆 pe 는 이미 None 을 쓴다.
    # yfinance 의 dividendYield 는 **이미 퍼센트**다. 실측:
    #   AAPL      0.34  (실제 약 0.4%)
    #   005930.KS 0.56  (실제 약 1.5%)
    # 여기서 *100 을 하면 애플이 배당수익률 34% 로 화면에 뜬다. 예전
    # yfinance 는 분수를 줬고 그때는 *100 이 맞았다 — 라이브러리 계약이
    # 바뀌었는데 코드가 안 따라갔다.
    #
    # 주의: 같은 info dict 안에서도 필드마다 단위가 다르다. payoutRatio
    # (0.1204 = 12%)·profitMargins(0.276 = 27.6%)·returnOnEquity 는 여전히
    # 분수다. "yfinance 비율은 전부 퍼센트" 로 일반화하면 안 된다.
    div_yield = _safe(info.get("dividendYield"), None)

    result = {
        "ticker":  sym,
        "period":  period,
        "ohlcv":   ohlcv,
        "info": {
            "name":       info.get("longName") or sym,
            "sector":     info.get("sector")   or "N/A",
            "industry":   info.get("industry") or "N/A",
            "market_cap": _fmt_cap(info.get("marketCap")),
            "pe":         _safe(info.get("trailingPE"),   None),
            "div_yield":  div_yield,
        },
        "performance": {
            "1w":        _perf(5),
            "1m":        _perf(21),
            "6m":        _perf(126),
            "ytd":       ytd,
            "1y":        _perf(252),
            "5y":        _perf(1260),
            "s52w_high": round(float(s52.max()), 2),
            "s52w_low":  round(float(s52.min()), 2),
        },
        "risk": {
            "beta":         beta,
            "volatility":   vol_ann,
            "avg_volume":   int(hist["Volume"].tail(20).mean()),
            "rsi14":        rsi14,
            "current_price": round(current, 2),
            "change_pct":   round((current / prev - 1) * 100, 2),
        },
        "var": {
            "var95":       var95,
            "return_dist": return_dist,
        },
        # ── 향후 개발 예정 (placeholder) ──────────────────────────────────
        "quant": _build_quant_block(sym, uid, closes, hist, info),
    }

    # 공용 본문을 메모리 + DB 양쪽에 저장 (다음 호출자는 즉시 사용)
    _cache_put(mem_key, result)
    ta_repo.save(sym, period, result)

    # 사용자별 optimizer 는 캐시에 넣지 않고 응답에만 덧붙인다
    out = dict(result)
    out["quant"] = {**out.get("quant", {}),
                    "optimizer": _build_optimizer_block(sym, uid, closes, market)}
    return out

"""
routers/signals.py
───────────────────
매매 타이밍 신호 API (전수 스캔 / 개별 전략)
"""
from __future__ import annotations

import logging
from typing import Optional

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query

logger = logging.getLogger(__name__)

from backend.services.auth import optional_user
from backend.db.portfolio_repo import get_holdings as db_get_holdings
from backend.db.market_cache import get_common, save_common
from backend.services.market_data import get_close_df, _cached
from backend.services.markets import market_param
from backend.services.trading_signals import (
    SP500_NASDAQ_UNIVERSE,
    scan_universe_with_targets,
    pairs_trading_signal,
    mean_reversion_signal,
    momentum_breakout_signal,
    detect_regime_er,
    get_sp500_universe,
    sma_macd_rsi_scan,
    compute_macro_spread_levels,
    technical_chart_detail,
    pairs_auto_detail,
)

router = APIRouter(prefix="/api/signals", tags=["signals"])


def _none_or(v, cast):
    """값이 None 이면 None 을 그대로, 아니면 cast 를 적용.

    `bool(None)` 은 False 고 `float(None)` 은 TypeError 다. 둘 다 '모른다' 를
    지운다 — 앞은 조용히 '아니다' 로 바꾸고, 뒤는 500 을 낸다. 실제로 둘 다
    일어났다: momentum_breakout_signal 이 거래량 미확인 시 None 을 돌려주도록
    바뀌자 volume_surge 는 false 로 표시되고 volume_ratio 는 500 이 됐다.
    """
    return None if v is None else cast(v)

_scan_cache: dict = {}




# ══════════════════════════════════════════════════════════════════════════════
# 엔드포인트
# ══════════════════════════════════════════════════════════════════════════════


@router.post("/scan")
def scan_universe(
    top_n:             int  = Query(default=10, ge=1, le=30),
    include_portfolio: bool = Query(default=True),
    _auth: Optional[dict] = Depends(optional_user),
    market: str = Depends(market_param),
):
    """
    S&P500 + 나스닥 전수 스캔 — 롱/숏 타점 반환.
    30~60초 소요. 결과는 인메모리 캐시.
    """
    import yfinance as yf
    from backend.db.market_cache import _yf_sem

    # 비로그인 사용자는 표준 유니버스만 스캔한다 (남의 보유 종목이 섞이면 안 된다).
    extra    = []
    if include_portfolio and _auth:
        extra = [t for t in db_get_holdings(_auth["uid"], market=market) if t != "CASH"]

    universe = sorted(set(SP500_NASDAQ_UNIVERSE + extra))

    import logging as _logging
    _logger = _logging.getLogger(__name__)

    BATCH = 50
    try:
        _yf_log = _logging.getLogger("yfinance")
        _prev = _yf_log.level
        _yf_log.setLevel(_logging.CRITICAL)
        close_frames:  list[pd.DataFrame] = []
        volume_frames: list[pd.DataFrame] = []
        try:
            for i in range(0, len(universe), BATCH):
                batch = universe[i : i + BATCH]
                with _yf_sem:
                    data = yf.download(
                        batch, period="6mo", progress=False,
                        auto_adjust=True, threads=False
                    )
                if data is None or data.empty:
                    continue
                if isinstance(data.columns, pd.MultiIndex):
                    lvl0 = data.columns.get_level_values(0)
                    if "Close"  in lvl0: close_frames.append(data["Close"])
                    if "Volume" in lvl0: volume_frames.append(data["Volume"])
                else:
                    if "Close"  in data.columns: close_frames.append(data[["Close"]])
                    if "Volume" in data.columns: volume_frames.append(data[["Volume"]])
        finally:
            _yf_log.setLevel(_prev)

        if not close_frames:
            raise HTTPException(status_code=503, detail="시장 데이터를 가져올 수 없습니다. 잠시 후 다시 시도하세요.")

        close_raw  = pd.concat(close_frames,  axis=1)
        volume_raw = pd.concat(volume_frames, axis=1) if volume_frames else None

        if close_raw.empty:
            raise HTTPException(status_code=503, detail="종가 데이터를 가져올 수 없습니다.")

        price_df  = close_raw.ffill().dropna(axis=1, how="all")
        volume_df = volume_raw.ffill() if volume_raw is not None else None

        _logger.info(f"스캔 데이터 로드 완료: {price_df.shape[1]}개 티커 × {len(price_df)}일")

        raw = scan_universe_with_targets(price_df, volume_df, top_n=top_n, market=market)

        def _clean(picks: list[dict]) -> list[dict]:
            out = []
            for p in picks:
                def _f(v):
                    try:
                        r = float(v)
                        return r if (r == r and abs(r) != float('inf')) else None  # NaN/Inf → None
                    except Exception:
                        return None
                out.append({
                    "ticker":   p.get("ticker", ""),
                    "method":   p.get("method", ""),
                    "score":    round(_f(p.get("score", 0)) or 0, 2),
                    "entry":    round(_f(p.get("entry", 0)) or 0, 2),
                    "target":   round(_f(p.get("target", 0)) or 0, 2),
                    "stop":     round(_f(p.get("stop", 0)) or 0, 2),
                    "upside":   round(_f(p["upside"]), 2)   if p.get("upside")   is not None else None,
                    "downside": round(_f(p["downside"]), 2) if p.get("downside") is not None else None,
                    "reason":   p.get("reason", ""),
                })
            return out

        result = {
            "long_picks":  _clean(raw.get("long_picks", [])),
            "short_picks": _clean(raw.get("short_picks", [])),
            "scanned":     raw.get("scanned", len(universe)),
        }
        _scan_cache["last"] = result
        return result
    except HTTPException:
        raise
    except Exception as e:
        _logger.error(f"스캔 실패 상세: {type(e).__name__}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"스캔 실패: {type(e).__name__}: {e}")


@router.get("/scan/cached")
def get_cached_scan():
    """마지막 스캔 결과 반환. 스캔 전이면 빈 결과 반환."""
    return _scan_cache.get("last") or {"long_picks": [], "short_picks": [], "scanned": 0}


@router.get("/pairs")
def pairs_signal(
    ticker_a: str = Query(..., description="첫 번째 티커. 예: NVDA"),
    ticker_b: str = Query(..., description="두 번째 티커. 예: AMD"),
    lookback: int = Query(default=60, ge=20, le=252),
    period:   str = Query(default="1y"),
):
    """두 종목의 페어 트레이딩 Z-score 신호."""
    ticker_a = ticker_a.upper()
    ticker_b = ticker_b.upper()

    close_df = get_close_df([ticker_a, ticker_b], period=period, ttl=300)

    if ticker_a not in close_df.columns or ticker_b not in close_df.columns:
        raise HTTPException(status_code=400, detail=f"{ticker_a} 또는 {ticker_b} 데이터 없음")

    result = pairs_trading_signal(
        close_df[ticker_a].dropna(),
        close_df[ticker_b].dropna(),
        lookback=lookback,
    )

    return {
        "current_z":      round(result["current_z"], 4),
        "current_signal": _signal_or_none(result["current_signal"]),
        "beta":           round(result["beta"], 4),
        "correlation":    round(result["correlation"], 4),
        "is_valid_pair":  result["is_valid_pair"],
        "lock_message":   result.get("lock_message"),
    }


@router.get("/mean-reversion")
def mean_reversion(
    ticker: str   = Query(..., description="티커. 예: TSLA"),
    window: int   = Query(default=20, ge=5, le=60),
    n_std:  float = Query(default=2.0, ge=1.0, le=3.0),
    period: str   = Query(default="6mo"),
):
    """볼린저 밴드 기반 평균 회귀 신호."""
    ticker   = ticker.upper()
    close_df = get_close_df([ticker], period=period, ttl=300)

    if ticker not in close_df.columns:
        raise HTTPException(status_code=400, detail=f"{ticker} 데이터 없음")

    result = mean_reversion_signal(close_df[ticker].dropna(), window=window, n_std=n_std)

    return {
        "current_signal": _signal_or_none(result["current_signal"]),
        "current_price":  round(float(result["current_price"]), 2),
        "upper_band":     round(float(result["upper_band"].iloc[-1]), 2),
        "lower_band":     round(float(result["lower_band"].iloc[-1]), 2),
        "mid_band":       round(float(result["mid_band"].iloc[-1]), 2),
        "pct_b":          round(float(result.get("pct_b", 0.5)), 4),
        "current_z":      round(float(result.get("current_z", 0.0)), 4),
    }


def _volume_for(ticker: str) -> Optional[pd.Series]:
    """DB 의 일별 거래량. 없으면 None.

    예전에는 호출부가 `volume=None` 을 하드코딩했다. 그러면 서비스 쪽이
    `breakout_vol = pd.Series(True, ...)` 로 채워 **거래량 조건이 항상 참**이
    되고, 응답에는 `volume_surge: true` · `volume_ratio: 1.0` 이 측정값인 척
    나갔다 — docstring 은 "거래량 급증" 을 조건으로 내걸고 있는데도.

    데이터는 있었다. 같은 순간 /signals/signal-score 는 같은 종목에
    volume_ratio 0.18 을 준다. 이 경로만 안 쓰고 있었다.
    """
    from backend.db.market_cache import get_volume_from_db
    vol_df = get_volume_from_db([ticker], period="1y")
    if vol_df is None or ticker not in getattr(vol_df, "columns", []):
        return None
    s = vol_df[ticker].dropna()
    return s if not s.empty else None


def _signal_or_none(v) -> Optional[str]:
    """신호 이름. 값이 없거나 NaN 이면 None.

    `str(v) if v else None` 이었다. **float('nan') 은 truthy** 라 그 가드를
    통과하고 `str(nan)` = "nan" 이 프론트로 나갔다 — 화면이 그걸 신호 이름으로
    받는다.
    """
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return str(v) or None


@router.get("/momentum")
def momentum_breakout(
    ticker:   str = Query(..., description="티커. 예: NVDA"),
    lookback: int = Query(default=20, ge=5, le=60),
    period:   str = Query(default="6mo"),
):
    """N일 고가 돌파 + 거래량 급증 모멘텀 신호."""
    ticker   = ticker.upper()
    close_df = get_close_df([ticker], period=period, ttl=300)

    if ticker not in close_df.columns:
        raise HTTPException(status_code=400, detail=f"{ticker} 데이터 없음")

    price  = close_df[ticker].dropna()
    result = momentum_breakout_signal(price, volume=_volume_for(ticker), lookback=lookback)

    resistance = result["resistance"].iloc[-1]

    return {
        "current_signal":    _signal_or_none(result["current_signal"]),
        "current_price":     round(float(result["current_price"]), 2),
        "resistance":        round(float(resistance), 2) if not pd.isna(resistance) else None,
        # 거래량을 못 받으면 이 셋은 None 이다. bool()/float() 로 감싸면
        # '모름' 이 '아님'·0 으로 바뀐다 — 서비스가 위장을 그만둔 의미가 없어진다.
        #
        # `.get(k, 1.0)` 은 여기서 아무것도 막지 못했다. 키는 있고 값이 None
        # 이라 기본값이 쓰이지 않고 float(None) 이 500 을 냈다. 기본값 인자는
        # '키 없음' 만 처리한다.
        "is_breakout_today": _none_or(result["is_breakout_today"], bool),
        "volume_surge":      _none_or(result["volume_surge"], bool),
        "volume_ratio":      _none_or(result["volume_ratio"], lambda v: round(float(v), 2)),
        "volume_known":      bool(result["volume_known"]),
    }


@router.get("/multi")
def multi_signal(
    ticker: str = Query(..., description="분석할 티커"),
    period: str = Query(default="6mo"),
):
    """단일 종목에 대해 평균회귀 + 모멘텀 신호를 동시에 반환."""
    ticker   = ticker.upper()
    close_df = get_close_df([ticker], period=period, ttl=300)

    if ticker not in close_df.columns:
        raise HTTPException(status_code=400, detail=f"{ticker} 데이터 없음")

    price = close_df[ticker].dropna()
    mr    = mean_reversion_signal(price)
    mb    = momentum_breakout_signal(price, volume=_volume_for(ticker))

    mr_signal = mr["current_signal"]
    mb_signal = mb["current_signal"]
    agreement = (
        (mr_signal == "BUY"  and mb_signal == "BREAKOUT") or
        (mr_signal == "SELL" and mb_signal is None)
    )

    return {
        "ticker": ticker,
        "mean_reversion": {
            "current_signal": _signal_or_none(mr_signal),
            "current_z":      round(float(mr["current_z"]), 4),
            "pct_b":          round(float(mr.get("pct_b", 0.5)), 4),
        },
        "momentum": {
            "current_signal":    _signal_or_none(mb_signal),
            "is_breakout_today": _none_or(mb["is_breakout_today"], bool),
        },
        "signals_agree": agreement,
        "combined_view": (
            "STRONG BUY"  if mr_signal == "BUY"  and mb_signal == "BREAKOUT" else
            "STRONG SELL" if mr_signal == "SELL" and mb_signal is None else
            "MIXED"
        ),
    }


@router.get("/regime")
def market_regime(
    ticker:    Optional[str] = Query(default=None,
                                     description="분석 티커. 생략하면 시장의 대표 지수"),
    years:     int   = Query(default=1, ge=1, le=5, description="표시 기간 (1-5년)"),
    window:    int   = Query(default=20, ge=5, le=120, description="ER 계산 기간(거래일)"),
    threshold: float = Query(default=0.30, ge=0.05, le=0.90, description="추세 판정 임계값"),
    market:    str   = Depends(market_param),
):
    """효율성 비율(ER) 기반 시장 국면 분류 (상승/횡보/하락).

    카우프만 효율성 비율(ER) = |기간 순변동| / 기간 내 일별 절대변동 합.
    한 방향으로 곧게 가면 1 에 가깝고, 요동치며 제자리면 0 에 가깝다.

        ER < threshold           → 횡보
        ER ≥ threshold, 순변동>0 → 상승
        ER ≥ threshold, 순변동<0 → 하락

    종가만 있으면 되므로 가격 캐시(get_close_df)를 그대로 쓴다 — OHLCV 를 매번
    내려받던 K-Means 방식과 달리 네트워크 호출이 없다. 계산도 결정적이라
    같은 입력이면 항상 같은 결과가 나온다.
    """
    # 티커를 안 주면 그 시장의 대표 지수를 쓴다. 예전에는 기본값이 ^GSPC
    # 하드코딩이라, `?market=KR` 만 준 호출자가 S&P500 국면을 받으면서
    # 시장이 반영됐다고 믿었다 — 무시되는데 무시된다는 신호가 없었다.
    # (화면은 RegimePanel 이 ^KS11 을 명시적으로 넘겨서 영향이 없었다.)
    from backend.services.markets import benchmark_for
    ticker = (ticker or benchmark_for(market)).upper()

    from backend.services.market_data import _cache_get, _cache_put
    from backend.services.market_calendar import is_us_extended_hours
    _ck  = f"regime_er_{ticker}_{years}_{window}_{threshold}"
    _ttl = 300 if is_us_extended_hours() else 3600
    _hit = _cache_get(_ck, _ttl)
    if _hit is not None:
        return _hit

    close_df = get_close_df([ticker], period="5y", ttl=300)
    if ticker not in close_df.columns:
        raise HTTPException(status_code=400, detail=f"{ticker} 데이터 없음")
    price_all = close_df[ticker].dropna()

    try:
        result = detect_regime_er(price_all, window=window, threshold=threshold)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    rl = result.get("regime_labels")
    if rl is None or len(rl) == 0:
        return {"ticker": ticker, "current_regime": "Unknown",
                "regime_pct": {}, "n_regimes": 3, "chart_data": [],
                "method": "efficiency_ratio", "current_er": None,
                "window": window, "threshold": threshold}

    rl_series    = pd.Series(rl)
    cutoff       = rl_series.index.max() - pd.Timedelta(days=365 * years)
    rl_window    = rl_series[rl_series.index >= cutoff]
    price_window = price_all[price_all.index >= cutoff]

    counts = rl_window.value_counts()
    total  = len(rl_window)
    regime_counts = {
        r: round(counts.get(r, 0) / total * 100, 1) if total > 0 else 0.0
        for r in ["Bull", "Sideways", "Bear"]
    }

    chart_data = [
        {
            "date":   date.strftime("%Y-%m-%d"),
            "price":  round(float(price_window.loc[date]), 2),
            "regime": regime,
        }
        for date, regime in rl_window.items()
        if date in price_window.index
    ]

    _payload = {
        "ticker":         ticker,
        "current_regime": result.get("current_regime", "Unknown"),
        "regime_pct":     regime_counts,
        "n_regimes":      result.get("n_regimes", 3),
        "chart_data":     chart_data,
        "method":         "efficiency_ratio",
        # 판정 근거를 화면에서 확인할 수 있도록 현재 ER 값과 설정을 함께 내려보낸다
        "current_er":     result.get("current_er"),
        "window":         result.get("window", window),
        "threshold":      result.get("threshold", threshold),
    }
    _cache_put(_ck, _payload)
    return _payload


# ══════════════════════════════════════════════════════════════════════════════
# Timing Engine 신규 엔드포인트
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/market-situation")
def market_situation(market: str = Depends(market_param)):
    """금리차(10Y-2Y) / 하이일드 스프레드의 과거 백분위 기반 Low/Normal/High 분류.

    **미국 지표다.** 한국은 대응물을 만들 수 없어 `available: false` 를 준다.

    왜 못 만드는가:
      · 하이일드 스프레드 — 한국에 FRED 의 BAMLH0A0HYM2 같은 일별 공개
        시계열이 없다.
      · 금리차 — 국고채 10년·3년이 ECOS 에 있지만 **일별 조회가 90일까지만**
        온다 (korea_macro._ecos_series 가 cycle="D" 에서 months_back 을 무시
        하고 90일로 고정한다). 백분위 분류는 미국 쪽이 10년 이력으로 내는데,
        3개월 표본으로 같은 Low/Normal/High 를 내면 그건 분류가 아니라 최근
        변동의 재표현이고 **화면에서는 구분되지 않는다.**

    없는 것을 다른 시장 값으로 채우지 않는다. 대신 왜 없는지 내려보내
    화면이 "원래 없는 기능" 과 "오늘 고장" 을 구분할 수 있게 한다.
    """
    if market == "KR":
        return {
            "available": False,
            "reason": "한국 국고채 일별 시리즈는 90일까지만 제공돼 "
                      "장기 백분위를 낼 수 없습니다. 하이일드 스프레드는 "
                      "대응 지표가 없습니다.",
        }

    # 캐시 키에 시장을 넣는다. 하나로 두면 먼저 조회한 시장의 값이 다른
    # 시장에 그대로 나간다 (§1.1).
    cache_key = f"market_situation:{market}"
    cached = get_common(cache_key)
    if cached:
        return cached

    result = {**compute_macro_spread_levels(), "available": True}

    # 폴백 응답을 하루 동안 박지 않는다.
    #
    # compute_macro_spread_levels 는 FRED 조회에 실패하면 값을 전부 None 으로
    # 두고 `source: "fallback"` 을 붙인다. 그 응답이 86400초 캐시에 들어가면
    # **하루 종일 재시도 없이** 빈 값이 나간다. Cloud Run 은 프로세스 내
    # 스케줄러가 돌지 않아 이 라우터가 유일한 조회 경로이므로, 폴백이 한 번
    # 뜬 날은 그날이 끝날 때까지 복구되지 않는다.
    #
    # 저장을 아예 안 하는 쪽은 택하지 않았다. 그러면 FRED 가 느리거나 죽어
    # 있는 **바로 그 상황에서** 매 요청이 그 지연을 탄다 — 실패를 처리하려던
    # 코드가 실패를 증폭시킨다. 있는 값은 쓰되 다음 요청이 이어받게 한다.
    ttl = 86400 if result.get("source") == "FRED" else 600
    if result.get("source") != "FRED":
        logger.warning(
            f"매크로 스프레드가 폴백으로 내려간다 — {ttl}초만 캐시한다 "
            f"(market={market}, source={result.get('source')!r})"
        )
    save_common(cache_key, result, ttl_seconds=ttl)
    return result


def _universe_for(market: str) -> list[str]:
    """스캔 대상 종목. 미국은 S&P500, 한국은 시총 상위(KOSPI200·KOSDAQ150 대응).

    한국 유니버스는 수집 작업이 미리 만들어 둔 것을 읽기만 한다 — 시총 조회가
    종목당 1초를 넘어 요청 처리 중에 만들면 그대로 타임아웃이다.
    """
    if market == "KR":
        from backend.services.korea_universe import get_scan_universe
        return get_scan_universe()
    return get_sp500_universe()


@router.get("/signal-scan")
def signal_scan(top_n: int = Query(default=10, ge=1, le=30),
                market: str = Depends(market_param)):
    """
    S&P500 매매신호 스캔 — SMA 1차 필터 → 통과 종목만 MACD/RSI 스코어링 → 매수/매도 상위 N개.

    스케줄러가 일별 가격·거래량 수집 직후 계산해 common_cache 에 저장한다.
    캐시 미스일 때만 DB(market_prices)의 종가·거래량으로 즉석 계산한다 — yfinance 호출 없음.
    """
    cache_key = f"signal_scan:{market}"
    cached = get_common(cache_key)
    if not cached:
        from backend.db.market_cache import get_prices_from_db, get_volume_from_db

        universe = _universe_for(market)
        if not universe:
            # 유니버스가 비어 있는 것과 시세가 아직 없는 것은 원인이 다르다.
            # 뭉뚱그리면 무엇을 기다려야 하는지 알 수 없다.
            raise HTTPException(
                status_code=503,
                detail="종목 목록을 준비하는 중입니다. 잠시 후 다시 시도하세요.",
            )
        close_df = get_prices_from_db(universe, "1y", fill=True)
        if close_df is None or close_df.empty:
            raise HTTPException(
                status_code=503,
                detail="가격 데이터가 아직 준비되지 않았습니다. 잠시 후 다시 시도하세요.",
            )
        volume_df = get_volume_from_db(universe, "1y")
        valid = [c for c in universe if c in close_df.columns]
        cached = sma_macd_rsi_scan(close_df[valid], volume_df, top_n=10)
        save_common(cache_key, cached, ttl_seconds=21600)

    long_picks  = cached.get("long_picks", [])[:top_n]
    short_picks = cached.get("short_picks", [])[:top_n]

    # 한국 종목은 코드만 보여 주면 무슨 회사인지 알 수 없다. 미국은 티커가
    # 곧 이름 역할을 하지만 '044490.KQ' 는 아무것도 알려 주지 않는다.
    if market == "KR":
        from backend.services.korea_universe import name_map
        names = name_map()
        long_picks  = [{**p, "name": names.get(p.get("ticker"), "")} for p in long_picks]
        short_picks = [{**p, "name": names.get(p.get("ticker"), "")} for p in short_picks]

    return {**cached, "long_picks": long_picks, "short_picks": short_picks}


@router.get("/signal-score")
def signal_score(ticker: str = Query(..., description="점수를 조회할 티커. 예: AAPL")):
    """
    단일 종목의 매수/매도 통합 점수 — Signal Scan 상위 N개 리스트에 없어도(순위 밖,
    또는 S&P500 유니버스 밖 종목이어도) 검색하면 참고 점수를 볼 수 있게 한다.
    `sma_macd_rsi_scan` 과 완전히 동일한 스코어링 공식을 쓴다.

    종가는 DB 우선(없으면 즉석 수집)이고, 거래량은 DB(S&P500 백필분)에 없으면
    이 요청 한정으로만 짧게 온디맨드 조회한다 — 결과를 DB에 저장하지 않는다.
    """
    from backend.db.market_cache import get_volume_from_db
    from backend.services.trading_signals import score_ticker_both_sides

    sym = ticker.upper().strip()
    if not sym:
        raise HTTPException(status_code=400, detail="티커를 입력하세요")

    close_df = get_close_df([sym], period="1y", ttl=300)
    if sym not in close_df.columns:
        raise HTTPException(status_code=404, detail=f"{sym} 데이터를 찾을 수 없습니다")

    def _volume_series():
        vdf = get_volume_from_db([sym], "1y")
        if vdf is not None and sym in vdf.columns:
            return vdf[sym]
        # S&P500 밖 종목 등 DB에 거래량이 없는 경우 — 이 조회 한정으로만 온디맨드 수집
        try:
            import yfinance as yf
            from backend.db.market_cache import _yf_sem
            with _yf_sem:
                hist = yf.Ticker(sym).history(period="6mo")
            if hist.empty or "Volume" not in hist.columns:
                return None
            vol = hist["Volume"]
            if getattr(vol.index, "tz", None) is not None:
                vol.index = vol.index.tz_localize(None)
            return vol
        except Exception as e:
            logger.warning(f"[signal-score] {sym} 거래량 온디맨드 조회 실패: {e}")
            return None

    volume = _cached(f"signal_score_volume::{sym}", 900, _volume_series)
    result = score_ticker_both_sides(sym, close_df[sym], volume)
    if result.get("insufficient_history"):
        raise HTTPException(status_code=400, detail=f"{sym}의 가격 이력이 부족해 점수를 계산할 수 없습니다")
    return result


def _fetch_ohlc(ticker: str) -> "pd.DataFrame | None":
    """OHLC 데이터: DB common_cache(12h) → 인메모리(1h) → yfinance(3y) 순으로 조회."""
    def _do():
        from backend.db.market_cache import get_common, save_common

        # 1. DB 캐시 우선 확인
        db_key = f"ohlc_df::{ticker}"
        cached = get_common(db_key)
        if cached and isinstance(cached, list) and len(cached) > 0:
            try:
                df = pd.DataFrame(cached)
                df["date"] = pd.to_datetime(df["date"])
                df = df.set_index("date")
                df.index.name = None
                cols = [c for c in ["Open", "High", "Low", "Close"] if c in df.columns]
                if len(cols) == 4:
                    return df[cols].apply(pd.to_numeric, errors="coerce").dropna()
            except Exception as e:
                logger.warning(f"OHLC DB 캐시 역직렬화 실패 ({ticker}): {e}")

        # 2. yfinance 다운로드 (3y — technical-chart는 최근 3년만 표시)
        try:
            import yfinance as yf
            from backend.db.market_cache import _yf_sem
            with _yf_sem:
                raw = yf.download(ticker, period="3y", progress=False, auto_adjust=True, threads=False)
            if raw.empty:
                return None
            if isinstance(raw.columns, pd.MultiIndex):
                lvl = raw.columns.get_level_values(1)
                raw = raw.xs(ticker, level=1, axis=1) if ticker in lvl else raw.droplevel(1, axis=1)
            cols = [c for c in ["Open", "High", "Low", "Close"] if c in raw.columns]
            if len(cols) != 4:
                return None
            result = raw[cols].dropna()

            # 3. DB에 저장 (12h TTL — 서버 재시작해도 재다운로드 불필요)
            try:
                rows = [
                    {"date": str(dt.date() if hasattr(dt, "date") else dt),
                     "Open": round(float(r["Open"]), 4), "High": round(float(r["High"]), 4),
                     "Low":  round(float(r["Low"]),  4), "Close": round(float(r["Close"]), 4)}
                    for dt, r in result.iterrows()
                ]
                save_common(db_key, rows, ttl_seconds=43200)
            except Exception as e:
                logger.warning(f"OHLC DB 저장 실패 ({ticker}): {e}")

            return result
        except Exception as e:
            logger.warning(f"OHLC yfinance 실패 ({ticker}): {e}")
            return None
    return _cached(f"ohlc::{ticker}", 3600, _do)


@router.get("/technical-chart")
def technical_chart(
    ticker: str   = Query(...,            description="티커. 예: AAPL"),
    period: str   = Query(default="3y",   description="조회 기간"),
    bb_period: int   = Query(default=20,  ge=5,  le=100, description="볼린저밴드 기간"),
    bb_std:    float = Query(default=2.0, ge=0.5, le=5.0, description="볼린저밴드 표준편차 배수"),
    resistance_lookback: int = Query(default=55, ge=10, le=200, description="저항선 롤링 기간"),
):
    """가격(OHLC) + 볼린저밴드 + 저항선 + 키포인트. 매매신호/평균회귀 패널 공용."""
    ticker = ticker.upper()
    cache_key = f"technical_chart::{ticker}::{bb_period}::{bb_std}::{resistance_lookback}"

    def _compute():
        ohlc = _fetch_ohlc(ticker)

        if ohlc is not None and "Close" in ohlc.columns:
            close_raw = ohlc["Close"].dropna()
            if hasattr(close_raw, "squeeze"):
                close_raw = close_raw.squeeze()
        else:
            close_df = get_close_df([ticker], period="3y", ttl=300)
            if ticker not in close_df.columns:
                return None
            close_raw = close_df[ticker].dropna()

        # _fetch_ohlc가 이미 3y 다운로드이므로 별도 트림 불필요하나 안전하게 유지
        cutoff = close_raw.index.max() - pd.Timedelta(days=365 * 3)
        price  = close_raw[close_raw.index >= cutoff]
        if price.empty:
            return None

        ohlc_trimmed = ohlc[ohlc.index >= cutoff] if ohlc is not None else None

        return technical_chart_detail(
            price,
            window=bb_period,
            n_std=bb_std,
            resistance_lookback=resistance_lookback,
            ohlc_df=ohlc_trimmed,
        )

    result = _cached(cache_key, 600, _compute)
    if result is None:
        raise HTTPException(status_code=400, detail=f"{ticker} 데이터 없음")

    return {"ticker": ticker, **result}


@router.get("/pairs-auto")
def pairs_auto(
    ticker:        str   = Query(..., description="기준 티커. 예: KO"),
    threshold_pct: float = Query(default=5.0, ge=0.1, le=100.0),
    top_n:         int   = Query(default=5, ge=1, le=20),
    market:        str   = Depends(market_param),
):
    """기준 종목과 가장 유사한 페어를 **같은 시장 안에서** 자동 탐색.

    후보를 항상 S&P500 에서 뽑고 있었다. 그래서 삼성바이오로직스의 페어로
    AMAT·CME 같은 미국 종목이 나왔다 — 통화도 거래시간도 다른 종목과의 상관은
    허수이고, 그걸 근거로 스프레드 매매를 하면 그대로 손실이다.

    기본 파라미터(threshold=5%, top_n=5)는 사전 계산 캐시를 우선 반환해 응답이 빠름.
    """
    ticker = ticker.upper()

    # 기준 종목이 이 시장 것인지 먼저 본다. 한국 화면에서 AAPL 을 조회하면
    # 후보는 한국 종목이라 의미 없는 결과가 나온다.
    from backend.services.markets import belongs_to
    if not belongs_to(ticker, market):
        raise HTTPException(
            status_code=400,
            detail=f"{ticker} 는 현재 선택한 시장의 종목이 아닙니다.",
        )

    # 기본 파라미터이면 사전 계산 캐시 우선 조회 (scheduler._precompute_pairs 가 저장)
    if threshold_pct == 5.0 and top_n == 5:
        precomputed = get_common(f"pairs_precomputed::{ticker}::5.0::5")
        if precomputed and precomputed.get("best"):
            return {"ticker": ticker, **precomputed}

    def _compute():
        import yfinance as yf
        from backend.db.market_cache import _yf_sem

        universe   = _universe_for(market)
        candidates = [t for t in universe if t != ticker][:200]
        close_df   = get_close_df([ticker] + candidates, period="2y", ttl=300)

        # 기준 종목이 S&P500 유니버스에 없어서 DB에 없는 경우 yfinance로 직접 취득
        if ticker not in close_df.columns:
            try:
                with _yf_sem:
                    raw = yf.download(ticker, period="2y", progress=False,
                                      auto_adjust=True, threads=False)
                if not raw.empty:
                    col = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw.get("Close", raw)
                    if isinstance(col, pd.DataFrame):
                        col = col.squeeze()
                    col = col.rename(ticker)
                    close_df = pd.concat([close_df, col], axis=1)
            except Exception as _e:
                logger.warning(f"pairs-auto yfinance fallback 실패 ({ticker}): {_e}")

        return pairs_auto_detail(ticker, close_df, candidates, threshold_pct=threshold_pct, top_n=top_n)

    result = _cached(f"pairs_auto::{market}::{ticker}::{threshold_pct}::{top_n}", 600, _compute)
    if not result.get("best"):
        raise HTTPException(status_code=400, detail=f"{ticker}에 대한 유사 종목을 찾을 수 없습니다.")

    # 섹터 정보: 보유 종목 DB → common_cache → yfinance 순 조회 (TTL 7일)
    def _get_sector(t: str) -> str:
        # 섹터는 공개 정보다. 예전엔 'default' 사용자의 보유 종목에서 먼저 찾았는데,
        # 남의 포트폴리오를 읽을 이유가 없고 공용 캐시로 충분하므로 제거했다.
        cached_s = get_common(f"sector_info::{t}")
        if cached_s:
            return str(cached_s)
        try:
            import yfinance as yf
            from backend.db.market_cache import _yf_sem, save_common as _save
            with _yf_sem:
                info = yf.Ticker(t).fast_info
            sector = getattr(info, "sector", None) or ""
            if not sector:
                with _yf_sem:
                    full_info = yf.Ticker(t).info
                sector = full_info.get("sector", "Unknown")
            if sector:
                save_common(f"sector_info::{t}", sector, ttl_seconds=7 * 86400)
            return sector or "Unknown"
        except Exception:
            return "Unknown"

    base_sector      = _get_sector(ticker)
    match_tickers    = [m["ticker"] for m in result.get("matches", [])]
    sector_map       = {t: _get_sector(t) for t in match_tickers}
    matches_sectored = [
        {**m, "sector": sector_map.get(m["ticker"], "Unknown")}
        for m in result.get("matches", [])
    ]

    return {
        "ticker":       ticker,
        "base_sector":  base_sector,
        "matches":      matches_sectored,
        **{k: v for k, v in result.items() if k != "matches"},
    }

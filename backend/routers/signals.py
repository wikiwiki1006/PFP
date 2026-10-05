"""
routers/signals.py
───────────────────
매매 타이밍 신호 API (전수 스캔 / 개별 전략)
"""
from __future__ import annotations

import logging
import re
from typing import Optional

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query

from backend.routers._errors import hidden_http_error, user_sentence

logger = logging.getLogger(__name__)

# trading_signals.detect_regime_er 가 사용자용 문장으로 올리는 ValueError.
_REGIME_USER_REASONS = (re.compile(r"ER 계산에 필요한 데이터 부족"),)

from backend.services.auth import optional_user
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
    top_n:  int = Query(default=10, ge=1, le=30),
    market: str = Depends(market_param),
):
    """
    표준 유니버스 전수 스캔 — 롱/숏 타점 반환.
    30~60초 소요. 결과는 인메모리 캐시.

    **보유 종목을 섞지 않는다.** 예전에는 `include_portfolio`(기본 True)로
    로그인 사용자의 보유 종목을 유니버스에 더했다. 그러면 같은 요청이
    사용자마다 다른 결과를 내고, 그 순간 '불특정 다수에게 동질적인 조언'
    이라는 전제가 깨진다(자본시장법 제101조 → 제6조 제7항). 스캔 대상은
    이제 누구에게나 같다.
    """
    import yfinance as yf
    from backend.db.market_cache import _yf_sem

    universe = sorted(set(SP500_NASDAQ_UNIVERSE))

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
        # 원인은 로그로만 — 예전에는 예외 이름과 문구를 detail 에 그대로 실었다.
        _logger.error(f"스캔 실패 상세: {type(e).__name__}: {e}", exc_info=True)
        raise HTTPException(status_code=500,
                            detail="매매신호 스캔에 실패했습니다. 잠시 후 다시 시도해 주세요.")


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
        # 데이터가 짧다는 사유는 detect_regime_er 가 사용자용 문장으로 올린다
        # ("ER 계산에 필요한 데이터 부족 (N행, 최소 M행)"). 그 밖의 ValueError 는
        # numpy·pandas 의 내부 문구라 로그로만 남긴다.
        reason = user_sentence(e, _REGIME_USER_REASONS)
        if reason is not None:
            raise HTTPException(status_code=400, detail=reason)
        raise hidden_http_error(logger, f"시장 국면 계산 ({ticker}, {market})", e, status_code=500,
                                message="시장 국면을 계산하지 못했습니다. 잠시 후 다시 시도해 주세요.")

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


def _universe_for(market: str) -> list[str]:
    """스캔 대상 종목. 미국은 S&P500, 한국은 시총 상위(KOSPI200·KOSDAQ150 대응).

    한국 유니버스는 수집 작업이 미리 만들어 둔 것을 읽기만 한다 — 시총 조회가
    종목당 1초를 넘어 요청 처리 중에 만들면 그대로 타임아웃이다.
    """
    if market == "KR":
        from backend.services.korea_universe import get_scan_universe
        return get_scan_universe()
    return get_sp500_universe()


# 화면에 적는 스캔 대상 이름. **위 `_universe_for` 와 같은 자리에 둔다** — 화면이
# 시장을 보고 따로 이름을 지으면 유니버스를 바꿀 때 문구만 옛 이름으로 남는다
# (실제로 한국 화면이 "S&P500 350개 종목" 이라고 적고 있었다).
_UNIVERSE_LABEL = {"US": "S&P500", "KR": "KOSPI200·KOSDAQ150"}


# 스캔 결과 캐시 키. scheduler._update_signal_scan 과 **같은 키**여야 한다.
# 버전은 저장 내용이 바뀔 때 올린다 — v3 는 `scores`(유니버스 전 종목의 매수·매도
# 점수)를 함께 담는다. 키가 같으면 옛 캐시가 TTL 동안 계속 읽힌다.
def _scan_cache_key(market: str) -> str:
    return f"signal_scan:v3:{market}"


def _load_or_compute_scan(market: str) -> dict:
    """스캔 결과(캐시 우선). 캐시 미스면 DB 종가·거래량으로 즉석 계산해 저장한다."""
    cache_key = _scan_cache_key(market)
    cached = get_common(cache_key)
    if cached:
        return cached
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
    cached = sma_macd_rsi_scan(close_df[valid], volume_df, top_n=10, score_all=True)
    save_common(cache_key, cached, ttl_seconds=21600)
    return cached


@router.get("/signal-scan")
def signal_scan(top_n: int = Query(default=10, ge=1, le=30),
                market: str = Depends(market_param)):
    """
    매매신호 스캔 (미국 S&P500 · 한국 KOSPI200·KOSDAQ150) — SMA 1차 필터 → 통과 종목만
    MACD/RSI 스코어링 → 매수/매도 **점수 상위 N개(섹터 무관)**. 응답의
    `universe_label` 이 스캔 대상 이름이다.

    잠시 섹터별 상위 5개로 바꿨다가 되돌렸다(사용자 요청). 섹터 조회 의존도
    함께 없앴다.

    **개인 데이터를 읽지 않는다.** 같은 시장·같은 날이면 누가 부르든 같은 응답이다.

    스케줄러가 일별 가격·거래량 수집 직후 계산해 common_cache 에 저장한다.
    캐시 미스일 때만 DB(market_prices)의 종가·거래량으로 즉석 계산한다 — yfinance 호출 없음.
    """
    cached = _load_or_compute_scan(market)

    long_picks  = (cached.get("long_picks")  or [])[:top_n]
    short_picks = (cached.get("short_picks") or [])[:top_n]

    # 한국 종목은 코드만 보여 주면 무슨 회사인지 알 수 없다. 미국은 티커가
    # 곧 이름 역할을 하지만 '044490.KQ' 는 아무것도 알려 주지 않는다.
    if market == "KR":
        from backend.services.korea_universe import name_map
        names = name_map()
        long_picks  = [{**p, "name": names.get(p.get("ticker"), "")} for p in long_picks]
        short_picks = [{**p, "name": names.get(p.get("ticker"), "")} for p in short_picks]

    # `scores` 는 응답에 싣지 않는다 — 유니버스 전 종목이라 수백 KB 다. 상세 점수는
    # /signal-score 가 종목 하나씩 꺼내 준다.
    body = {k: v for k, v in cached.items() if k != "scores"}
    return {**body, "long_picks": long_picks, "short_picks": short_picks,
            "universe_label": _UNIVERSE_LABEL.get(market)}


@router.get("/signal-score")
def signal_score(ticker: str = Query(..., description="점수를 조회할 티커. 예: AAPL"),
                 market: str = Depends(market_param)):
    """
    단일 종목의 매수/매도 점수. 응답의 `as_of` 가 점수의 기준일, `basis` 가 출처다.

    · basis="scan"      — 스캔 유니버스 종목. 스캔이 **같은 프레임에서** 미리 계산해 둔
                          값을 그대로 돌려준다. 그래서 목록에 오른 종목은 목록 점수와
                          상세 점수가 정확히 같다.
    · basis="on_demand" — 유니버스 밖(검색한 임의 종목). 같은 공식으로 즉석 계산하되,
                          **종가가 확정된 마지막 세션까지만**, 가격·거래량을 **같은
                          마지막 날짜로 맞춰** 쓴다.
    · basis="scan_unavailable" — 스캔을 만들 수 없는 상태(유니버스·시세 준비 중)라
                          즉석 계산했다. 이 종목이 스캔 대상인지 아닌지는 모른다.

    예전에는 모든 종목을 즉석 계산했고, 가격은 `get_close_df`(장중 실시간 봉 포함,
    오늘까지)·거래량은 DB(스캔 기준일까지)였다. 목록과 상세가 다른 날짜의 데이터로
    점수를 냈다 — 실측 HPE 목록 80 / 상세 50, FICO 79 / 39 (sma_macd_rsi_scan docstring).
    """
    from backend.db.market_cache import get_volume_from_db
    from backend.services.trading_signals import score_ticker_both_sides

    sym = ticker.upper().strip()
    if not sym:
        raise HTTPException(status_code=400, detail="티커를 입력하세요")

    # ── 1) 스캔 유니버스 종목: 스캔이 계산해 둔 값 ──────────────────────
    try:
        scan = _load_or_compute_scan(market)
    except HTTPException:
        # 스캔을 못 만드는 상황(유니버스·시세 준비 중)이어도 단일 종목 조회는
        # 아래 즉석 계산으로 할 수 있다. 503 을 그대로 올리지 않는다.
        scan = None
    scored = ((scan or {}).get("scores") or {}).get(sym)
    if scored:
        return {**scored, "as_of": scan.get("as_of"), "basis": "scan"}
    # 스캔이 없어서 즉석 계산하는 것과, 스캔 대상 밖이라 즉석 계산하는 것은 다르다.
    # 앞의 경우를 '스캔 대상 밖' 이라고 말하면 사실이 아니다.
    basis = "on_demand" if scan is not None else "scan_unavailable"

    # ── 2) 유니버스 밖: 같은 공식, 같은 날짜의 가격·거래량으로 즉석 계산 ──
    # include_market=False · fill=False — 이 종목이 실제로 거래된 날만 쓴다. 기본값
    # (시장 지수·환율·암호화폐를 같이 받아 ffill)으로 받으면 이 종목이 쉬던 날
    # (다른 시장 개장일·주말 암호화폐 행)에 전날 종가가 복사돼 들어가 SMA·RSI 가
    # 가짜 봉으로 계산된다.
    close_df = get_close_df([sym], period="1y", ttl=300, include_market=False, fill=False)
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
                hist = yf.Ticker(sym).history(period="1y")
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
    close = close_df[sym].dropna()
    if getattr(close.index, "tz", None) is not None:
        close.index = close.index.tz_localize(None)

    # 종가가 확정된 마지막 세션까지만 쓴다. 가격 캐시는 장중 실시간 봉을, yfinance
    # 거래량 폴백은 장중 부분 거래량을 오늘 행으로 준다 — 그대로 쓰면 '오늘 종가 기준'
    # 이라고 표시하면서 실제로는 장중 값으로 점수를 낸다. 시장은 요청이 아니라 티커로
    # 정한다 (한국 화면에서 미국 티커를 검색해도 미국 세션 기준이어야 한다).
    from backend.services.market_calendar import last_completed_kr_session, last_completed_session
    from backend.services.markets import market_of_ticker
    cutoff = pd.Timestamp(last_completed_kr_session() if market_of_ticker(sym) == "KR"
                          else last_completed_session())
    close = close[close.index.normalize() <= cutoff]

    vol = None
    if volume is not None:
        vol = volume.dropna()
        if getattr(vol.index, "tz", None) is not None:
            vol.index = vol.index.tz_localize(None)
        vol = vol[vol.index.normalize() <= cutoff]
        if not len(vol):
            vol = None

    # 가격과 거래량을 **같은 마지막 날짜**로 맞춘다. 한쪽만 늦게 끝나면 MACD·RSI 와
    # 거래량 배율이 서로 다른 날의 값이 된다 — 어느 날의 점수도 아니다. 예전 이 경로가
    # 가격은 오늘(장중), 거래량은 며칠 전이었다.
    if len(close):
        last_day = close.index.max().normalize()
        if vol is not None:
            last_day = min(last_day, vol.index.max().normalize())
            vol = vol[vol.index.normalize() <= last_day]
        close = close[close.index.normalize() <= last_day]
        as_of = last_day.strftime("%Y-%m-%d")
    else:
        as_of = None

    result = score_ticker_both_sides(sym, close, vol)
    if result.get("insufficient_history"):
        raise HTTPException(status_code=400, detail=f"{sym}의 가격 이력이 부족해 점수를 계산할 수 없습니다")
    return {**result, "as_of": as_of, "basis": basis}



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

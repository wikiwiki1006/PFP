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

from backend.routers._errors import hidden_http_error
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
    optimizer = {"current_weight": None, "risk_contribution": None,
                 "correlation": None, "correlation_label": None, "beta_exposure": None,
                 "in_portfolio": False, "note": None}

    # 보유 종목이 바뀌지 않는 한 결과가 같으므로 짧게 캐싱한다.
    # (공용 본문이 DB 캐시에 적중해도 여기서 매번 공분산을 다시 계산하면
    #  응답이 수 초대로 남는다 — 실측 2.8s → 0.3s)
    # 키에 시장을 넣는다 (§1.1). 이 블록의 결과는 get_holdings(uid, market=market)
    # 에 의존하는데 키에는 market 이 없었다 — 먼저 조회한 시장의 답이 300초 동안
    # 다른 시장에 그대로 나갔다. 실측으로 000660.KS 가 `?market=US` 에서
    # in_portfolio=True · weight=48.79 를 받았다(미국에 없는 종목이다).
    #
    # MarketSwitch 의 전체 새로고침으로도 안 막힌다. 서버 메모리 캐시라
    # 브라우저를 새로 고쳐도 남는다.
    from backend.services.market_data import _cache_get as _cg, _cache_put as _cp
    _ok = f"opt_ctx_{sym}_{uid}_{market}"
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

    quant = {"score": None, "label": "계산 불가", "factors": {},
             "weights_used": {}, "momentum_windows": []}
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
        # 점수를 **실제로** 만든 가중치와 모멘텀 창. compute_quant_score 가
        # 돌려주고 있었는데 여기서 버리고 있었다.
        #
        # 이게 없으면 화면이 2팩터 점수와 4팩터 점수를 구별할 수 없다. ETF 는
        # 펀더멘털이 없어 quality·value 가 통째로 빠지는데(실측: SPY·JEPQ 가
        # 2/4), 라벨 뒷부분은 '전 팩터 열위'·'다중 팩터 우위' 처럼 **팩터
        # 집합에 대한 주장**이다. 재지 않은 것을 잰 것처럼 말하게 된다.
        "weights_used":     quant.get("weights_used", {}),
        "momentum_windows": quant.get("momentum_windows", []),
        "regime":       _KO.get(regime, regime) if regime else "계산 불가",
        "regime_code":  regime,
        "regime_er":    regime_er,
        "panic_score":  panic.get("score"),
        "panic_status": panic.get("status"),
        "panic_components": panic.get("components", {}),
    }



# ── 성과표 (긴 기간) ──────────────────────────────────────────────────────────
#
# 성과(1W~5Y·YTD·52주 고저)는 **차트 기간과 따로** 약 5년치 종가로 계산한다.
# 예전에는 차트 기간(기본 1y = 약 250행)의 종가로 계산해서 1Y(252거래일 전)·
# 5Y(1260거래일 전)가 언제나 N/A 였고, 1M 기간을 고르면 YTD·52주 저가가 1개월치로
# 계산됐다. 기간 버튼은 차트를 바꾸는 것이지 '1년 수익률' 의 뜻을 바꾸면 안 된다.

_PERF_SPAN_DAYS = 5 * 366 + 20     # 5년 + 여유 (5년 전 그날이 휴장일이어도 직전 거래일이 들어오게)
_PERF_TTL = 6 * 3600               # 과거 종가는 거의 안 바뀐다. 최근 봉은 아래서 덮어쓴다.


def _long_closes(sym: str) -> "pd.Series | None":
    """약 5년치 종가 (날짜 인덱스, tz 없음). 실패하면 None — 화면은 N/A 로 남는다."""
    from datetime import date, timedelta
    from backend.services.market_data import _cache_get, _cache_put
    from backend.db.market_cache import _yf_sem

    key = f"ticker_long_closes_{sym}"
    hit = _cache_get(key, _PERF_TTL)
    if hit is not None:
        return hit
    try:
        with _yf_sem:
            h = yf.Ticker(sym).history(start=date.today() - timedelta(days=_PERF_SPAN_DAYS),
                                       auto_adjust=True)
        c = h["Close"].dropna() if h is not None and not h.empty else None
    except Exception:
        logger.warning("성과표용 장기 종가 조회 실패 (%s) — 차트 기간 종가로 계산한다", sym, exc_info=True)
        return None
    if c is None or c.empty:
        logger.warning("성과표용 장기 종가가 비어 있다 (%s) — 차트 기간 종가로 계산한다", sym)
        return None
    c.index = pd.to_datetime(c.index.strftime("%Y-%m-%d"))
    _cache_put(key, c)
    return c


def _performance(period_closes: "pd.Series", long: "pd.Series | None") -> dict:
    """성과표. 장기 종가(`_long_closes`)에 차트 기간 종가(최근 봉 포함)를 덮어 계산한다.

    장기 종가가 없으면(None) 차트 기간 종가만으로 계산한다 — 그 기간이 덮지 못하는
    항목은 **전부** None(화면 N/A)이다. YTD·52주도 같다: 1개월치로 낸 'YTD' 와
    '52주 저가' 는 그럴듯한 오답이다 (§1.3b).
    """
    pc = period_closes.astype(float).copy()
    pc.index = pd.to_datetime(pd.Index(pc.index).strftime("%Y-%m-%d"))
    if long is not None:
        # 장기 종가는 6시간 메모다. 차트 마지막 날보다 뒤의 행은 그 시점의 장중가일 수
        # 있으므로 섞지 않는다 — 섞으면 낡은 장중가가 '현재가' 가 되어 같은 응답의
        # risk.current_price 와 어긋난다.
        long = long[long.index <= pc.index[-1]]
    closes = pc.combine_first(long) if long is not None else pc
    closes = closes[~closes.index.duplicated(keep="last")].sort_index().dropna()

    current = float(closes.iloc[-1])
    last = closes.index[-1]

    # **달력 기준**으로 잰다 — N거래일 전이 아니라 'N개월 전 그날(없으면 직전 거래일)'.
    # 거래일로 세면 시장마다 뜻이 달라진다: 한국은 연 거래일이 약 245일이라 252거래일
    # 전이 1년보다 길고, 5년치(1260거래일)를 채우지 못해 5Y 가 비었다.
    def _perf(offset: "pd.DateOffset") -> Optional[float]:
        target = last - offset
        if closes.index[0] > target:
            return None          # 그만큼 오래된 시세가 없다 — 지어내지 않는다
        base = float(closes[closes.index <= target].iloc[-1])
        return round((current / base - 1) * 100, 2)

    # YTD: 올해 첫 거래일 대비 (예전 정의 그대로). 데이터가 연초를 덮지 못하면 None —
    # 첫 거래일이 1월 첫 주 안에 있어야 한다 (신년 연휴를 넘는 여유 7일).
    first = closes.index[0]
    covers_year_start = first < pd.Timestamp(year=last.year, month=1, day=8)
    ytd_c = closes[closes.index.year == last.year]
    ytd = (round((current / float(ytd_c.iloc[0]) - 1) * 100, 2)
           if covers_year_start and len(ytd_c) > 1 else None)
    # 52주 고저도 1년을 덮을 때만 (여유 7일)
    covers_52w = first <= last - pd.DateOffset(years=1) + pd.Timedelta(days=7)
    s52 = closes[closes.index > last - pd.DateOffset(years=1)]
    return {
        "1w": _perf(pd.DateOffset(weeks=1)), "1m": _perf(pd.DateOffset(months=1)),
        "6m": _perf(pd.DateOffset(months=6)),
        "ytd": ytd,
        "1y": _perf(pd.DateOffset(years=1)), "5y": _perf(pd.DateOffset(years=5)),
        "s52w_high": round(float(s52.max()), 2) if covers_52w else None,
        "s52w_low": round(float(s52.min()), 2) if covers_52w else None,
    }


# ── 최근 일봉 덧씌우기 ────────────────────────────────────────────────────────
#
# 상세 본문은 ticker_analytics 에 24시간 캐시되고 장중에 다시 계산하는 경로가
# 없다. 그래서 차트의 마지막 봉이 '처음 계산한 시점' 에 하루 동안 멈춰 있었다
# — 오늘 봉은 물론, 계산 시점에 아직 없던 어제 봉까지 빠졌다 (2026-10-02
# 00:26 KST 에 계산된 005930.KS 가 09-30 에서 끝났고 market_prices 에는 10-01
# 종가가 있었다).
#
# 본문 전체를 다시 계산하지 않고 **최근 5일 일봉만** 따로 받아 덮어쓴다.
# 야후 일봉은 장중에도 오늘 행의 Close 를 현재가로 채워 준다 (CLAUDE.md §1.5)
# — 분봉 없이 '오늘 봉' 하나를 얻는 가장 싼 방법이다.
#
# 이 값은 응답에만 싣고 캐시·market_prices 에는 쓰지 않는다. 장중 부분 봉을
# 저장하면 위조 종가가 남는다 (§1.6 — 저장 가드는 save_prices_to_db 에 있다).

_RECENT_TTL_OPEN   = 60       # 가격이 움직일 수 있는 시간대
_RECENT_TTL_CLOSED = 1800     # 장외 — 어제 봉이 늦게 확정되는 경우만 잡으면 된다


def _session_open_now(sym: str) -> bool:
    """정규장이 지금 열려 있는가 — 마지막 봉이 '장중 부분 봉' 인지 가르는 기준.

    price_can_move 가 아니다. 그건 시간외(한국 18:00 까지)를 포함해 '재조회할
    가치가 있는가' 를 묻는다. 15:30 이후의 한국 봉은 확정이므로 '장중' 이 아니다.
    """
    from backend.services.market_calendar import (
        uses_kr_session_calendar, uses_us_session_calendar,
        is_kr_market_open, is_us_market_open,
    )
    if uses_kr_session_calendar(sym):
        return is_kr_market_open()
    if uses_us_session_calendar(sym):
        return is_us_market_open()
    return False


def _exchange_today(sym: str) -> str:
    from backend.services.market_calendar import uses_kr_session_calendar, now_kst, now_et
    return (now_kst() if uses_kr_session_calendar(sym) else now_et()).strftime("%Y-%m-%d")


def _bar_from_meta(sym: str, t: "yf.Ticker", meta: dict) -> "dict | None":
    """야후 시세 메타데이터로 마지막 세션의 일봉을 만든다. 못 만들면 None.

    한국 종목은 일봉의 마지막 날이 비어 있다 — 2026-10-02 01:12 KST 에
    005930.KS 의 10-01 행이 Open/High/Low/Close 전부 NaN 이고 Volume 만 있었다
    (장 마감 9시간 뒤). 5분봉에는 그날이 있지만 14:55 에서 끝나 종가 단일가
    (15:20~15:30)가 빠진다 — 마지막 5분봉 275,500 vs 확정 종가 276,000.
    메타데이터(regularMarket*)는 같은 시각에 276,000 · 고 276,000 · 저 264,500 ·
    거래량 · 체결시각 15:30:02 를 정확히 줬다. 시가만 없어서 5분봉 첫 봉에서 읽는다.

    시가를 못 구하면 봉을 만들지 않는다. 전일 종가나 현재가로 채우면 그럴듯한
    가짜 캔들이 그려진다 (CLAUDE.md §1.3(b)).
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    price, hi, lo = (_safe(meta.get(k)) for k in
                     ("regularMarketPrice", "regularMarketDayHigh", "regularMarketDayLow"))
    ts = meta.get("regularMarketTime")
    if price is None or hi is None or lo is None or not ts:
        logger.warning("시세 메타데이터에 당일 값이 없다 (%s) — 오늘 봉을 붙이지 않는다", sym)
        return None
    try:
        tz = ZoneInfo(meta.get("exchangeTimezoneName") or "UTC")
    except Exception:
        tz = ZoneInfo("UTC")
    day = datetime.fromtimestamp(int(ts), tz).strftime("%Y-%m-%d")

    try:
        from backend.db.market_cache import _yf_sem
        with _yf_sem:
            intra = t.history(period="1d", interval="5m", auto_adjust=True)
        intra = intra[[ix.strftime("%Y-%m-%d") == day for ix in intra.index]]
        opens = intra["Open"].dropna()
        open_ = float(opens.iloc[0]) if not opens.empty else None
    except Exception:
        logger.warning("당일 시가 조회 실패 (%s)", sym, exc_info=True)
        open_ = None
    if open_ is None:
        logger.warning("당일 시가를 구하지 못했다 (%s, %s) — 오늘 봉을 붙이지 않는다", sym, day)
        return None

    vol = _safe(meta.get("regularMarketVolume"))
    return {
        "date": day, "open": round(open_, 4),
        # 시가·종가가 메타의 고저 밖에 있으면 고저를 넓힌다 (출처가 둘이라 어긋날 수 있다).
        "high": round(max(hi, open_, price), 4), "low": round(min(lo, open_, price), 4),
        "close": round(price, 4), "volume": int(vol) if vol is not None else 0,
    }


def _recent_bars(sym: str) -> "list[dict] | None":
    """최근 며칠의 일봉 [{date, open, high, low, close, volume}]. 실패하면 None.

    빈 목록과 실패를 구별한다 (§1.3) — 실패하면 화면이 '최신 시세를 받지 못함' 을 적는다.
    """
    from backend.services.market_calendar import price_can_move
    from backend.services.market_data import _cache_get, _cache_put
    from backend.db.market_cache import _yf_sem

    key = f"ticker_recent_bars_{sym}"
    ttl = _RECENT_TTL_OPEN if price_can_move(sym) else _RECENT_TTL_CLOSED
    hit = _cache_get(key, ttl)
    if hit is not None:
        return hit
    try:
        t = yf.Ticker(sym)
        with _yf_sem:
            df = t.history(period="5d", interval="1d", auto_adjust=True)
        meta = dict(t.history_metadata or {})
    except Exception:
        logger.warning("최근 일봉 조회 실패 (%s) — 캐시된 차트를 그대로 준다", sym, exc_info=True)
        return None

    bars: dict[str, dict] = {}
    for dt, r in (df.iterrows() if df is not None else []):
        if any(pd.isna(r[c]) for c in ("Open", "High", "Low", "Close")):
            continue          # 한국의 마지막 날처럼 OHLC 가 빈 행 — 아래 메타로 채운다
        d = dt.strftime("%Y-%m-%d")
        bars[d] = {"date": d,
                   "open": round(float(r["Open"]), 4), "high": round(float(r["High"]), 4),
                   "low": round(float(r["Low"]), 4), "close": round(float(r["Close"]), 4),
                   "volume": int(r["Volume"]) if pd.notna(r["Volume"]) else 0}

    # 일봉에 마지막 세션이 없을 때만 메타로 만든다 (요청이 하나 더 나가므로).
    ts = meta.get("regularMarketTime")
    if ts:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        try:
            tz = ZoneInfo(meta.get("exchangeTimezoneName") or "UTC")
        except Exception:
            tz = ZoneInfo("UTC")
        if datetime.fromtimestamp(int(ts), tz).strftime("%Y-%m-%d") not in bars:
            b = _bar_from_meta(sym, t, meta)
            if b is not None:
                bars[b["date"]] = b

    if not bars:
        # yfinance 는 실패해도 예외 대신 빈 프레임을 주는 일이 잦다. 캐시하지 않는다.
        logger.warning("최근 일봉이 비어 있다 (%s) — 캐시된 차트를 그대로 준다", sym)
        return None
    out = [bars[d] for d in sorted(bars)]
    _cache_put(key, out)
    return out


def _overlay_recent_bars(body: dict, sym: str) -> dict:
    """캐시된 상세 본문에 최근 일봉을 덮어쓴 **새** dict 를 돌려준다.

    받은 dict 는 메모리 캐시가 들고 있는 객체라 고치면 안 된다 — 다음 요청이
    덧씌운 값을 원본으로 읽는다. 바꾸는 부분(ohlcv·performance·risk)만 새로 만든다.

    덧씌운 뒤 지표(이평선·BB·스토캐스틱)와 수익률을 **같은 공식으로 다시
    계산한다.** 봉만 바꾸면 오늘 봉 위에 어제 기준 이평선이 그려진다.

    응답의 `bars_refresh` 가 결과를 말한다:
      ok=False  → 최근 일봉을 받지 못해 캐시 그대로다 (화면이 그 사실을 적는다)
      live=True → 마지막 봉이 정규장 진행 중인 부분 봉이다
    """
    rows = list(body.get("ohlcv") or [])
    recent = _recent_bars(sym)
    last = rows[-1]["date"] if rows else None
    if recent is None or not rows:
        return {**body, "bars_refresh": {"ok": False, "last_date": last, "live": False}}

    by_date = {r["date"]: dict(r) for r in rows}
    for b in recent:
        # 캐시 범위보다 앞선 날은 붙이지 않는다 — 기간(1m/3m…)의 앞쪽을 늘리지 않는다.
        if b["date"] < rows[0]["date"]:
            continue
        by_date[b["date"]] = {**by_date.get(b["date"], {}), **b}
    merged = [by_date[d] for d in sorted(by_date)]

    hist = pd.DataFrame(merged).set_index(pd.to_datetime([m["date"] for m in merged]))
    hist = hist.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close"})
    closes = hist["Close"].astype(float)
    ind = pd.DataFrame({
        "ma20": closes.rolling(20).mean(), "ma50": closes.rolling(50).mean(),
        "ma200": closes.rolling(200).mean(),
    })
    ind = ind.join(_calc_bb(closes)).join(_calc_stoch(hist))

    def _fv(v) -> Optional[float]:
        return None if (pd.isna(v) or math.isinf(float(v))) else round(float(v), 4)

    out_rows = []
    for i, m in enumerate(merged):
        row = ind.iloc[i]
        out_rows.append({**m, **{k: _fv(row[k]) for k in (
            "ma20", "ma50", "ma200", "bb_upper", "bb_mid", "bb_lower", "stoch_k", "stoch_d")}})

    current = float(closes.iloc[-1])
    prev = float(closes.iloc[-2]) if len(closes) > 1 else current

    long = _long_closes(sym)
    if long is not None:
        perf = {**(body.get("performance") or {}), **_performance(closes, long)}
    else:
        # 장기 종가를 못 받았으면 캐시된 성과표를 그대로 둔다. 짧은 기간으로 다시 계산해
        # 덮으면 한 번의 조회 실패가 멀쩡한 1Y·5Y 를 N/A 로, YTD 를 오답으로 바꾼다.
        perf = body.get("performance") or {}
    risk = {**(body.get("risk") or {}),
            "current_price": round(current, 2),
            "change_pct": round((current / prev - 1) * 100, 2)}

    last = out_rows[-1]["date"]
    live = last == _exchange_today(sym) and _session_open_now(sym)
    return {**body, "ohlcv": out_rows, "performance": perf, "risk": risk,
            "bars_refresh": {"ok": True, "last_date": last, "live": live}}

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
        out = _overlay_recent_bars(shared, sym)
        out["quant"] = {**out.get("quant", {}),
                        "optimizer": _build_optimizer_block(sym, uid, None, market)}
        return out

    try:
        t = yf.Ticker(sym)
        hist = t.history(period=yf_period, auto_adjust=True)
    except Exception as e:
        # 종목 상세 모달이 detail 을 그대로 보여 준다. 라이브러리 예외 문구(접속
        # 주소·HTTP 오류 원문)는 로그로만 남긴다.
        raise hidden_http_error(logger, f"종목 상세 시세 조회 ({sym}, {period})", e, status_code=503,
                                message="시세를 받아오지 못했습니다. 잠시 후 다시 시도해 주세요.")

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

    # ── 수익률 — 차트 기간과 따로 장기 종가로 (_performance 주석) ─────────────
    performance = _performance(closes, _long_closes(sym))

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
        "performance": performance,
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

    # 사용자별 optimizer 는 캐시에 넣지 않고 응답에만 덧붙인다.
    # 방금 받은 시세라 덧씌울 것이 없다 — 마지막 봉의 성격만 적는다.
    last_date = ohlcv[-1]["date"]
    out = {**result, "bars_refresh": {
        "ok": True, "last_date": last_date,
        "live": last_date == _exchange_today(sym) and _session_open_now(sym)}}
    out["quant"] = {**out.get("quant", {}),
                    "optimizer": _build_optimizer_block(sym, uid, closes, market)}
    return out

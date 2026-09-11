"""
backend/services/trading_signals.py
────────────────────────────────────
pfp/trading_signals.py의 계산 로직을 백엔드 서비스로 직접 이식.
plot_* 함수는 Streamlit용이므로 pfp/에 유지하고 여기선 제외.
sys.path 조작 없이 독립 실행 가능.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── S&P500 + 나스닥 전수 스캔 유니버스 ──────────────────────────────────────────
SP500_NASDAQ_UNIVERSE = [
    "AAPL","MSFT","NVDA","AMZN","META","GOOGL","GOOG","TSLA","AVGO","COST",
    "NFLX","AMD","ADBE","QCOM","INTC","TXN","MU","AMAT","LRCX","KLAC",
    "SNPS","CDNS","MRVL","ORCL","CRM","PANW","CRWD","FTNT","DDOG","ZS",
    "TEAM","WDAY","SNOW","MDB","NET","OKTA","HUBS","VEEV","IDXX",
    "ISRG","REGN","VRTX","BIIB","GILD","AMGN","ILMN","MRNA","DXCM","EW",
    "JPM","BAC","WFC","GS","MS","BLK","SPGI","MCO","ICE","CME",
    "BRK-B","V","MA","AXP","PYPL","XYZ","COF","AIG","MET",
    "UNH","CVS","HUM","CI","ELV","MCK","CAH","DHR","TMO",
    "ABT","MDT","SYK","BSX","BDX","ZBH","BAX","HCA","IQV","A",
    "XOM","CVX","COP","EOG","SLB","PSX","MPC","VLO","HAL","OXY",
    "LLY","PFE","MRK","BMY","JNJ","ABBV","ZTS","ALNY","INCY","JAZZ",
    "HD","LOW","TGT","WMT","EBAY","ETSY","CHWY",
    "BA","LMT","RTX","NOC","GD","TDG","HEI","KTOS",
    "GM","F","RIVN","ON","TE","APH","GLW","MPWR",
    "ANET","CSCO","NTAP","NTNX","HPE","DELL","WDC","STX",
]


# ══════════════════════════════════════════════════════════════════════════════
# 1. 평균 회귀 (볼린저 밴드)
# ══════════════════════════════════════════════════════════════════════════════

def mean_reversion_signal(
    price: pd.Series,
    window: int = 20,
    n_std: float = 2.0,
) -> dict:
    mid    = price.rolling(window).mean()
    std    = price.rolling(window).std()
    upper  = mid + n_std * std
    lower  = mid - n_std * std
    zscore = (price - mid) / std

    signals = pd.Series(None, index=price.index, dtype=object)
    signals[price <= lower] = "BUY"
    signals[price >= upper] = "SELL"

    current_z      = float(zscore.iloc[-1]) if not pd.isna(zscore.iloc[-1]) else 0.0
    current_signal = _signal_at(signals)

    return {
        "mid_band": mid, "upper_band": upper, "lower_band": lower,
        "zscore": zscore, "signals": signals,
        "current_z": current_z, "current_signal": current_signal,
        "current_price": float(price.iloc[-1]),
        "pct_b": float((price.iloc[-1] - lower.iloc[-1]) / (upper.iloc[-1] - lower.iloc[-1]))
                 if (upper.iloc[-1] - lower.iloc[-1]) > 0 else 0.5,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 3. 모멘텀 돌파
# ══════════════════════════════════════════════════════════════════════════════

def _signal_at(series: pd.Series, i: int = -1):
    """신호 시계열의 한 시점 값. **신호 없음은 None 이다.**

    `pd.Series(None, index=..., dtype=object)` 는 pandas 3.x 에서 None 이 아니라
    `float('nan')` 으로 채워진다. NaN 은 truthy 라서 `str(x) if x else None`
    같은 가드를 그대로 통과하고, 화면에는 신호 이름 자리에 **문자열 "nan"**
    이 나갔다 (`/api/signals/momentum`, `/api/signals/mean-reversion` 에서 실측).

    외부 계약이 바뀐 것을 코드가 못 따라간 형태다 — 야후가 미확정 종가를
    NaN 대신 실시간 값으로 주기 시작한 것, dividendYield 의 단위가 바뀐 것과
    같은 계열이고, 여기서는 pandas 쪽이었다. 판정을 한 곳으로 모은다.
    """
    if len(series) == 0:
        return None
    v = series.iloc[i]
    return None if pd.isna(v) else v


def momentum_breakout_signal(
    price: pd.Series,
    volume: pd.Series | None,
    lookback: int = 55,
    volume_mult: float = 1.5,
) -> dict:
    resistance = price.rolling(lookback).max().shift(1)

    volume_known = volume is not None and len(volume) > 0
    signals = pd.Series(None, index=price.index, dtype=object)

    if volume_known:
        volume_avg   = volume.rolling(lookback).mean().shift(1)
        breakout_vol = volume > volume_avg * volume_mult
        last_avg     = volume_avg.iloc[-1]
        volume_ratio = (float(volume.iloc[-1] / last_avg)
                        if not pd.isna(last_avg) and last_avg > 0 else None)

        breakout = (price > resistance) & breakout_vol
        signals[breakout] = "BREAKOUT"
        is_breakout_today = bool(breakout.iloc[-1]) if len(breakout) > 0 else False
        volume_surge      = bool(breakout_vol.iloc[-1]) if len(breakout_vol) > 0 else False
    else:
        # 거래량을 모를 때 breakout_vol 을 True 로 채우면 이 신호의 정의
        # ("고가 돌파 + 거래량 급증") 에서 뒤쪽 조건이 공짜로 성립한다. 가격만
        # 뚫어도 '거래량이 확인된 돌파' 가 되고, volume_surge=True 와
        # volume_ratio=1.0 이 측정값인 얼굴로 함께 실린다.
        #
        # 실제로 그랬다. /api/signals/momentum 은 volume=None 을 하드코딩해서
        # 부르는데(routers/signals.py) 응답은 volume_surge:true, volume_ratio:1.0
        # 이었다. 같은 순간 /api/signals/signal-score 는 같은 종목에 0.18 을
        # 줬다 — 거래량 데이터는 있고 이 경로만 안 쓴 것이다.
        #
        # 모르는 것은 모른다고 내보낸다. False 로 채우는 것도 답이 아니다.
        # 그건 '거래량 급증 없음' 이라는 또 다른 단정이다.
        volume_avg        = pd.Series(np.nan, index=price.index)
        volume_ratio      = None
        is_breakout_today = None
        volume_surge      = None

    return {
        "resistance":        resistance,
        "volume_avg":        volume_avg,
        "signals":           signals,
        "current_signal":    _signal_at(signals),
        "current_price":     float(price.iloc[-1]),
        "is_breakout_today": is_breakout_today,
        "volume_surge":      volume_surge,
        "volume_ratio":      round(volume_ratio, 2) if volume_ratio is not None else None,
        "volume_known":      volume_known,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 4. 페어 트레이딩
# ══════════════════════════════════════════════════════════════════════════════

def pairs_trading_signal(
    price_a: pd.Series,
    price_b: pd.Series,
    lookback: int = 60,
    entry_z: float = 2.0,
    exit_z: float = 0.5,
    min_correlation: float = 0.70,
) -> dict:
    correlation   = float(price_a.pct_change().corr(price_b.pct_change()))
    is_valid_pair = correlation >= min_correlation
    lock_message  = (
        f"⚠️ 낮은 상관계수 경고: {correlation:.2f} (권장 기준 {min_correlation:.2f} 미만)."
        if not is_valid_pair else None
    )

    log_a = np.log(price_a)
    log_b = np.log(price_b)
    X     = np.column_stack([np.ones(len(log_b)), log_b.values])
    coeffs, *_ = np.linalg.lstsq(X, log_a.values, rcond=None)
    beta  = coeffs[1]

    spread    = log_a - beta * log_b
    roll_mean = spread.rolling(lookback).mean()
    roll_std  = spread.rolling(lookback).std()
    zscore    = (spread - roll_mean) / roll_std

    signals  = []
    position = None
    for z in zscore:
        if pd.isna(z):
            signals.append(None)
            continue
        if position is None:
            if z > entry_z:
                position = "LONG_B_SHORT_A"
                signals.append(position)
            elif z < -entry_z:
                position = "LONG_A_SHORT_B"
                signals.append(position)
            else:
                signals.append(None)
        else:
            if abs(z) < exit_z:
                signals.append("EXIT")
                position = None
            else:
                signals.append(position)

    signal_series  = pd.Series(signals, index=zscore.index)
    current_z      = float(zscore.iloc[-1]) if not pd.isna(zscore.iloc[-1]) else 0.0
    current_signal = _signal_at(signal_series)

    return {
        "spread": spread, "zscore": zscore, "beta": float(beta),
        "signals": signal_series,
        "current_z": current_z, "current_signal": current_signal,
        "correlation": correlation, "is_valid_pair": is_valid_pair,
        "lock_message": lock_message,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 5. 시장 국면 감지 (200MA 기반 — 실제 시장 판단 기준)
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
# 6. 최적 페어 탐색
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
# 7. 전수 스캔 (매수가 / 목표가 / 손절가 포함)
# ══════════════════════════════════════════════════════════════════════════════

def scan_universe_with_targets(
    price_df: pd.DataFrame,
    volume_df: pd.DataFrame | None,
    top_n: int = 10,
    bb_window: int = 20,
    stop_pct: float = 0.04,
    *,
    market: str,
) -> dict:
    """유니버스 스캔 → 매수·매도 후보와 목표가.

    `market` 은 기본값 없는 키워드 전용 인자다. `reason` 문자열이 금액을 담고
    **그대로 화면에 보이므로**, 시장을 모르면 한국 종목에 `중앙선 $71900.00` 이
    뜬다 (§1.4 — 원화는 `$` 도 소수점도 쓰지 않는다). 기본값을 두면 빠뜨린
    호출부가 조용히 달러가 되고, 위치 인자로 두면 실수로 다른 값이 들어갈 수
    있어 둘 다 막는다.
    """
    from backend.services.markets import get_market
    from backend.services.report_writer import _fmt_price

    # 이 함수는 루프 안에서 `cur` 을 '현재가'로 쓴다 — 통화 변수에 그 이름을
    # 쓰면 첫 반복에서 덮어써져 _fmt_price 가 통화 자리에 가격을 받는다.
    currency = get_market(market).currency

    long_picks:  list[dict] = []
    short_picks: list[dict] = []
    scanned = 0

    # 실패를 종목마다 로그로 남기면 500종목 스캔에서 수백 줄이 되어 로그가
    # 쓸모없어진다. 아무것도 남기지 않으면 후보가 왜 적은지 알 수 없다 (§1.3).
    # 루프에서는 모으고 끝에서 한 줄로 낸다 — 실패가 한 원인에 몰렸는지
    # 흩어졌는지가 그 줄로 구별된다.
    failures: dict[str, list[str]] = {"평균회귀": [], "모멘텀 돌파": []}

    tickers = [t for t in price_df.columns
               if t in set(SP500_NASDAQ_UNIVERSE + list(price_df.columns))]

    for ticker in tickers:
        price = price_df[ticker].dropna()
        if len(price) < 60:
            continue
        scanned += 1
        cur = float(price.iloc[-1])

        # ── 볼린저밴드 평균회귀 ────────────────────────────────────────────
        try:
            mr     = mean_reversion_signal(price, window=bb_window)
            mid    = float(mr["mid_band"].iloc[-1])
            upper  = float(mr["upper_band"].iloc[-1])
            lower  = float(mr["lower_band"].iloc[-1])
            z      = mr["current_z"]
            signal = mr["current_signal"]

            if signal == "BUY":
                long_picks.append({
                    "ticker": ticker, "method": "볼린저밴드 반등",
                    "entry":  round(cur, 2), "target": round(mid, 2),
                    "stop":   round(cur * (1 - stop_pct), 2),
                    "upside": round((mid - cur) / cur * 100, 1),
                    "score":  abs(z),
                    "reason": f"하단밴드 이탈 (Z={z:.2f}) → 중앙선 {_fmt_price(mid, currency)} 회귀 기대",
                })
            elif signal == "SELL":
                short_picks.append({
                    "ticker":   ticker, "method": "볼린저밴드 하락",
                    "entry":    round(cur, 2), "target": round(mid, 2),
                    "stop":     round(cur * (1 + stop_pct), 2),
                    "downside": round((cur - mid) / cur * 100, 1),
                    "score":    abs(z),
                    "reason":   f"상단밴드 이탈 (Z={z:.2f}) → 중앙선 {_fmt_price(mid, currency)} 하락 기대",
                })
        except Exception as e:
            failures["평균회귀"].append(f"{ticker}({type(e).__name__}: {e})")

        # ── 모멘텀 돌파 ────────────────────────────────────────────────────
        try:
            vol = volume_df[ticker].dropna() if (
                volume_df is not None and ticker in volume_df.columns
            ) else None
            if vol is not None:
                common = price.index.intersection(vol.index)
                if len(common) >= 60:
                    mb = momentum_breakout_signal(price.loc[common], vol.loc[common])
                    if mb["is_breakout_today"]:
                        resistance = float(mb["resistance"].iloc[-1]) if not pd.isna(mb["resistance"].iloc[-1]) else cur
                        long_picks.append({
                            "ticker": ticker, "method": "모멘텀 돌파",
                            "entry":  round(cur, 2),
                            "target": round(cur * 1.08, 2),
                            "stop":   round(cur * (1 - stop_pct), 2),
                            "upside": 8.0,
                            "score":  3.5,
                            "reason": f"N일 고점 {_fmt_price(resistance, currency)} 돌파 + 거래량 급증",
                        })
        except Exception as e:
            failures["모멘텀 돌파"].append(f"{ticker}({type(e).__name__}: {e})")

    for stage, msgs in failures.items():
        if msgs:
            logger.warning(
                "%s 계산 실패 %d/%d 종목 — 그 종목은 후보에서 빠진다. 앞 3건: %s",
                stage, len(msgs), scanned, " · ".join(msgs[:3]),
            )

    def _dedup_top(lst: list[dict]) -> list[dict]:
        seen: dict = {}
        for c in sorted(lst, key=lambda x: x["score"], reverse=True):
            if c["ticker"] not in seen:
                seen[c["ticker"]] = c
        return list(seen.values())[:top_n]

    return {
        "long_picks":  _dedup_top(long_picks),
        "short_picks": _dedup_top(short_picks),
        "scanned":     scanned,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 8. S&P500 전체 유니버스 (Timing Engine)
# ══════════════════════════════════════════════════════════════════════════════

def get_sp500_universe() -> list[str]:
    """
    S&P500 구성종목 전체 티커. common_cache(30일 TTL) 우선 조회 →
    없으면 위키피디아에서 1회 수집해 저장. 실패 시 SP500_NASDAQ_UNIVERSE로 폴백.
    """
    from backend.db.market_cache import get_common, save_common

    cached = get_common("sp500_constituents")
    if cached:
        return cached

    try:
        import requests
        from io import StringIO

        resp = requests.get(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        resp.raise_for_status()
        tables = pd.read_html(StringIO(resp.text))
        symbols = tables[0]["Symbol"].astype(str).str.strip().str.replace(".", "-", regex=False).tolist()
        symbols = sorted(set(s for s in symbols if s))
        if len(symbols) >= 400:
            save_common("sp500_constituents", symbols, ttl_seconds=30 * 86400)
            return symbols
    except Exception:
        pass

    return list(SP500_NASDAQ_UNIVERSE)


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _macd_hist(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.Series:
    """MACD 히스토그램 = (EMA12 - EMA26) - EMA9(그 차이)."""
    macd = _ema(close, fast) - _ema(close, slow)
    return macd - _ema(macd, signal)


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder RSI(14). avg_loss=0(전부 상승) 구간은 RSI 100 으로 둔다."""
    delta = close.diff()
    gain  = delta.clip(lower=0.0)
    loss  = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs  = avg_gain / avg_loss
    rsi = 100.0 - 100.0 / (1.0 + rs)
    return rsi.where(avg_loss != 0.0, 100.0)


def _score_ticker_side(
    ticker: str,
    close: pd.Series,
    volume: "pd.Series | None",
    side: str,
    min_history: int = 210,
) -> "dict | None":
    """단일 종목 · 단일 방향(매수/매도) 스코어(0~100). Step 2 스코어링 공식 본체.

    `sma_macd_rsi_scan` 의 후보 리스트 스코어링과 `score_ticker_both_sides`(검색된
    임의 종목 참고 점수) 가 이 함수 하나를 공유한다 — 두 경로의 점수 공식이
    어긋나지 않게 하기 위해서다. 1차 필터 통과 여부와 무관하게 호출 가능하다.
    """
    c = close.dropna()
    if len(c) < min_history or volume is None:
        return None
    vt = volume.dropna()
    if len(vt) < 20:
        return None
    avg20 = float(vt.iloc[-20:].mean())
    if avg20 <= 0:
        return None
    vratio = float(vt.iloc[-1] / avg20)

    hist    = _macd_hist(c)
    h_today = float(hist.iloc[-1])
    h_prev  = float(hist.iloc[-2])
    rsi_val = float(_rsi(c).iloc[-1])
    price   = float(c.iloc[-1])

    # 수급 폭발 (0~40, 캡)
    s_vol = min(40.0, vratio * 15.0)

    # MACD 가속도 (0~30): 히스토그램 확장 + 영선 돌파
    if side == "long":
        s_mom = 15.0 if (h_today - h_prev) > 0 else 0.0
        if h_prev < 0 <= h_today:
            s_mom += 15.0
    else:
        s_mom = 15.0 if (h_today - h_prev) < 0 else 0.0
        if h_prev > 0 >= h_today:
            s_mom += 15.0

    # RSI 골디락스 존 (-10~30)
    if side == "long":
        if   50 <= rsi_val <= 65: s_rsi = 30.0
        elif 40 <= rsi_val < 50:  s_rsi = 20.0
        elif rsi_val >= 70:       s_rsi = -10.0
        else:                     s_rsi = 0.0
    else:
        if   35 <= rsi_val <= 50: s_rsi = 30.0
        elif 50 < rsi_val <= 60:  s_rsi = 20.0
        elif rsi_val <= 30:       s_rsi = -10.0
        else:                     s_rsi = 0.0

    score = max(0.0, min(100.0, s_vol + s_mom + s_rsi))
    arrow = "▲" if h_today >= h_prev else "▼"

    # 이동평균 상태는 **잰다.** 예전에는 side 로 고른 리터럴이었다:
    #     trend  = "정배열 (20>50일선)" if side == "long" else "역배열 (20<50일선)"
    #     anchor = "100일선 위"        if side == "long" else "200일선 아래"
    #
    # 이 함수는 docstring 대로 "1차 필터 통과 여부와 무관하게 호출 가능" 하다.
    # 그래서 필터가 그 조건을 보장해 주지 않는데도 단정했다. 실측 (005930.KS,
    # long_filter_pass=false · short_filter_pass=false 인데 양쪽 다 나감):
    #     long  "100일선 위 · 정배열 (20>50일선) · …"
    #     short "200일선 아래 · 역배열 (20<50일선) · …"
    # 같은 종목 같은 시각에 20일선이 50일선 위이면서 아래일 수는 없다.
    # anchor 가 어느 이동평균을 보는지는 side 마다 다른 게 맞다(롱은 100일선,
    # 숏은 200일선을 기준으로 본다). 틀렸던 것은 **방향을 안 재고 단정한 것**이다.
    ma20  = float(c.iloc[-20:].mean())
    ma50  = float(c.iloc[-50:].mean())
    ma_anchor = float(c.iloc[-100:].mean()) if side == "long" else float(c.iloc[-200:].mean())
    trend = ("정배열 (20>50일선)" if ma20 > ma50 else
             "역배열 (20<50일선)" if ma20 < ma50 else "20일선 = 50일선")
    anchor = f"{'100일선' if side == 'long' else '200일선'} {'위' if price > ma_anchor else '아래'}"
    return {
        "ticker":         ticker,
        "price":          round(price, 2),
        "score":          round(score),
        "volume_ratio":   round(vratio, 2),
        "rsi":            round(rsi_val, 1),
        "macd_hist":      round(h_today, 4),
        "macd_hist_prev": round(h_prev, 4),
        "components": {
            "volume":   round(s_vol, 1),
            "momentum": round(s_mom, 1),
            # 이 항목은 RSI 점수다. 점수 공식에 추세 항목은 없다 —
            # s_vol · s_mom · s_rsi 셋뿐인데 마지막이 'trend' 로 나가고 있었고,
            # TradeSignalsPanel.tsx 가 그것을 "추세" 막대로 그렸다. RSI 53.8 →
            # s_rsi 30.0 이 화면에 "추세 30/30" 으로 표시됐다.
            "rsi":      round(s_rsi, 1),
        },
        "reason": f"{anchor} · {trend} · 거래량 {vratio:.1f}배 · RSI {rsi_val:.0f} · MACD {arrow}",
    }


# 1차 필터 완화 단계. 통과 종목이 top_n 에 못 미치면 순서대로 한 단계씩 완화해
# 재시도한다 — 실무에서 이 상황의 가장 흔한 원인은 종목 자체가 아니라 '당일
# 거래량이 아직 다 반영되지 않아 20일 평균보다 낮게 잡히는' 데이터 타이밍이라
# (장 마감 1시간 뒤 수집이면 뒤늦게 들어오는 체결분이 못 잡힌다), 거래량 조건부터
# 누그러뜨린다. 그래도 부족하면 추세·가격 조건까지 순서대로 제거해 최후에는
# 유효 종목 전체를 스코어링해서라도 top_n 을 채운다.
_RELAX_LEVELS: list[dict] = [
    {"label": "기준 그대로",                "vol_ratio_min": 1.0, "require_trend": True,  "require_price": True},
    {"label": "거래량 조건 완화(평균 80%)",  "vol_ratio_min": 0.8, "require_trend": True,  "require_price": True},
    {"label": "거래량 조건 완화(평균 50%)",  "vol_ratio_min": 0.5, "require_trend": True,  "require_price": True},
    {"label": "거래량 조건 제외",            "vol_ratio_min": 0.0, "require_trend": True,  "require_price": True},
    {"label": "추세(20/50일선) 조건도 제외", "vol_ratio_min": 0.0, "require_trend": False, "require_price": True},
    {"label": "전 종목 스코어링(최후 수단)", "vol_ratio_min": 0.0, "require_trend": False, "require_price": False},
]


def _step1_candidates(
    side: str, level: dict, valid: list[str],
    last: pd.Series, sma20: pd.Series, sma50: pd.Series, sma100: pd.Series, sma200: pd.Series,
    vol_today: pd.Series, vsma20: pd.Series,
) -> list[str]:
    if not level["require_price"]:
        price_ok = pd.Series(True, index=valid)
    elif side == "long":
        price_ok = (last > sma100).reindex(valid).fillna(False)
    else:
        price_ok = (last < sma200).reindex(valid).fillna(False)

    if not level["require_trend"]:
        trend_ok = pd.Series(True, index=valid)
    elif side == "long":
        trend_ok = (sma20 > sma50).reindex(valid).fillna(False)
    else:
        trend_ok = (sma20 < sma50).reindex(valid).fillna(False)

    if level["vol_ratio_min"] <= 0:
        vol_ok = pd.Series(True, index=valid)
    else:
        vol_ok = (vol_today > vsma20 * level["vol_ratio_min"]).reindex(valid).fillna(False)

    mask = price_ok & trend_ok & vol_ok
    return [t for t in valid if bool(mask.get(t, False))]


def _step1_with_relaxation(
    side: str, valid: list[str], top_n: int,
    last: pd.Series, sma20: pd.Series, sma50: pd.Series, sma100: pd.Series, sma200: pd.Series,
    vol_today: pd.Series, vsma20: pd.Series,
) -> tuple[list[str], int, str]:
    """후보가 top_n 이상 모일 때까지, 또는 마지막 단계에 이를 때까지 완화 단계를 순서대로 시도."""
    for i, level in enumerate(_RELAX_LEVELS):
        cands = _step1_candidates(side, level, valid, last, sma20, sma50, sma100, sma200, vol_today, vsma20)
        if len(cands) >= top_n or i == len(_RELAX_LEVELS) - 1:
            return cands, i, level["label"]
    return [], 0, _RELAX_LEVELS[0]["label"]  # 이론상 도달하지 않음(안전망)


def sma_macd_rsi_scan(
    close_df: pd.DataFrame,
    volume_df: "pd.DataFrame | None",
    top_n: int = 10,
    min_history: int = 210,
) -> dict:
    """2단계 매매신호 스캔 (S&P500).

    Step 1 — 벡터 SMA 필터로 유니버스 축소 (for 문 없이 마지막 행만 비교):
      · 매수 통과: 현재가 > SMA100  AND  SMA20 > SMA50  AND  당일거래량 > 20일평균거래량
      · 매도 통과: 현재가 < SMA200  AND  SMA20 < SMA50  AND  당일거래량 > 20일평균거래량
      통과 종목이 top_n 에 못 미치면 `_RELAX_LEVELS` 순서대로 조건을 완화해 재시도한다
      (long/short 독립적으로). 응답의 `*_filter_level`/`*_filter_note` 로 실제 적용된
      단계를 알 수 있다 — 0 이면 원래 기준 그대로 통과한 것이고, 그보다 크면 완화가
      적용된 것이니 화면에서 그 사실을 사용자에게 알려줘야 한다.
    Step 2 — 후보만 MACD 히스토그램 / RSI(14) 로 통합점수(0~100) 산출 후 상위 top_n.

    거래량 데이터가 아예 없는 티커는 완화 최종 단계 전까지는 후보에서 빠진다.
    """
    empty = {
        "long_picks": [], "short_picks": [], "scanned": 0, "as_of": None,
        "long_filter_level": 0, "long_filter_note": _RELAX_LEVELS[0]["label"],
        "short_filter_level": 0, "short_filter_note": _RELAX_LEVELS[0]["label"],
    }
    if close_df is None or close_df.empty:
        return empty

    close_df = close_df.sort_index()
    valid = [c for c in close_df.columns if int(close_df[c].notna().sum()) >= min_history]
    if not valid:
        return empty
    close = close_df[valid].ffill()

    if volume_df is not None and not volume_df.empty:
        vcols = [c for c in valid if c in volume_df.columns]
        vol   = volume_df.sort_index().reindex(close.index)[vcols]
    else:
        vol = pd.DataFrame(index=close.index)

    # '당일' = 거래량이 폭넓게 존재하는 마지막 날짜.
    # close 는 일부 티커의 장중·부분 봉 때문에 뒤쪽이 들쭉날쭉할 수 있고(ffill 로 메워짐),
    # 그 마지막 행을 '오늘'로 잡으면 거래량이 아직 없어 1차 필터가 전부 탈락한다.
    if vol.shape[1]:
        coverage = vol.notna().sum(axis=1)
        good = coverage[coverage >= max(1, int(vol.shape[1] * 0.5))]
        if len(good):
            asof = good.index[-1]
            close = close.loc[:asof]
            vol   = vol.loc[:asof]

    # ── Step 1: 벡터 1차 필터 (완화 단계 포함) ─────────────────────────────────
    last   = close.iloc[-1]
    sma20  = close.rolling(20).mean().iloc[-1]
    sma50  = close.rolling(50).mean().iloc[-1]
    sma100 = close.rolling(100).mean().iloc[-1]
    sma200 = close.rolling(200).mean().iloc[-1]

    if vol.shape[1]:
        vol_today = vol.iloc[-1].reindex(valid)
        vsma20    = vol.rolling(20).mean().iloc[-1].reindex(valid)
    else:
        vol_today = pd.Series(index=valid, dtype=float)
        vsma20    = pd.Series(index=valid, dtype=float)

    long_cands, long_level, long_note = _step1_with_relaxation(
        "long", valid, top_n, last, sma20, sma50, sma100, sma200, vol_today, vsma20)
    short_cands, short_level, short_note = _step1_with_relaxation(
        "short", valid, top_n, last, sma20, sma50, sma100, sma200, vol_today, vsma20)

    # ── Step 2: 스코어링 ────────────────────────────────────────────────────
    def _score(ticker: str, side: str) -> "dict | None":
        vt = vol[ticker] if ticker in vol.columns else None
        return _score_ticker_side(ticker, close[ticker], vt, side, min_history)

    longs  = [r for r in (_score(t, "long")  for t in long_cands)  if r]
    shorts = [r for r in (_score(t, "short") for t in short_cands) if r]
    longs.sort(key=lambda r: r["score"], reverse=True)
    shorts.sort(key=lambda r: r["score"], reverse=True)

    as_of = close.index[-1]
    return {
        "long_picks":  longs[:top_n],
        "short_picks": shorts[:top_n],
        "scanned":     len(valid),
        "as_of":       as_of.strftime("%Y-%m-%d") if hasattr(as_of, "strftime") else str(as_of),
        "long_filter_level":  long_level,
        "long_filter_note":   long_note,
        "short_filter_level": short_level,
        "short_filter_note":  short_note,
    }


def score_ticker_both_sides(
    ticker: str,
    close: pd.Series,
    volume: "pd.Series | None",
    min_history: int = 210,
) -> dict:
    """검색된 임의의 단일 종목의 매수/매도 참고 점수.

    Signal Scan 상위 top_n 리스트에 없는 종목(비 S&P500 포함)도 검색하면 볼 수 있게
    한다. 1차 필터(추세·가격) 통과 여부와 무관하게 점수를 계산해 반환하고,
    `*_filter_pass` 로 오늘 실제로 1차 필터를 통과했을지도 함께 알려준다.
    """
    c = close.dropna()
    if len(c) < min_history:
        return {
            "ticker": ticker, "price": None, "insufficient_history": True,
            "long": None, "long_filter_pass": False,
            "short": None, "short_filter_pass": False,
        }

    price  = float(c.iloc[-1])
    sma20  = float(c.rolling(20).mean().iloc[-1])
    sma50  = float(c.rolling(50).mean().iloc[-1])
    sma100 = float(c.rolling(100).mean().iloc[-1])
    sma200 = float(c.rolling(200).mean().iloc[-1])

    return {
        "ticker": ticker,
        "price": round(price, 2),
        "insufficient_history": False,
        "long":              _score_ticker_side(ticker, close, volume, "long", min_history),
        "long_filter_pass":  price > sma100 and sma20 > sma50,
        "short":             _score_ticker_side(ticker, close, volume, "short", min_history),
        "short_filter_pass": price < sma200 and sma20 < sma50,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 9. 매크로 스프레드 과거 백분위 분류 (시장 상황)
# ══════════════════════════════════════════════════════════════════════════════

def _percentile_rank(series: pd.Series, value: float) -> float:
    s = series.dropna()
    if len(s) == 0:
        return 50.0
    return float((s < value).sum() / len(s) * 100)


def compute_macro_spread_levels() -> dict:
    """
    T10Y2Y(금리차), BAMLH0A0HYM2(HY 스프레드) 10년치 과거 데이터 기준
    현재값의 백분위와 Low/Normal/High 분류, 의미에 맞는 색상 반환.
    """
    from datetime import datetime, timedelta

    try:
        import pandas_datareader.data as web
        start = datetime.now() - timedelta(days=3650)
        df = web.DataReader(["T10Y2Y", "BAMLH0A0HYM2"], "fred", start).dropna()
        rate_series = df["T10Y2Y"]
        hy_series   = df["BAMLH0A0HYM2"]
        rate_spread = float(rate_series.iloc[-1])
        hy_spread   = float(hy_series.iloc[-1])
        source = "FRED"
    except Exception:
        # FRED 를 못 받았다. 값은 지어내되 **확신은 지어내지 않는다.**
        #
        # 예전에는 한 점짜리 시리즈(`pd.Series([0.5])`)를 백분위 함수에 넘겼다.
        # `(0.5 < 0.5).sum() / 1 * 100` = **0.0** 이라 "과거 10년 대비 백분위 0%"
        # 가 화면에 떴다 — 즉 "10년 중 최저" 라는 최대 확신이다. 게다가 금리차는
        # Low=빨강(역전 위험), HY 는 Low=초록(안전)이라 **서로 모순된 신호**를
        # 동시에 냈다.
        #
        # 이제 백분위를 **내보내지 않는다** (None). 프론트가
        # `metric.percentile != null` 로 막대와 "백분위 N%" 를 건너뛴다
        # (fe9e0f5). 없는 것을 없다고 말하는 자리다.
        logger.warning(
            "FRED 매크로 스프레드 조회 실패 — 백분위 없이 내보낸다 "
            "(source=fallback). value·level 은 아직 지어낸 값이다.",
            exc_info=True,
        )
        rate_spread, hy_spread = 0.5, 3.5
        rate_pct = hy_pct = None
        source = "fallback"
    else:
        rate_pct = _percentile_rank(rate_series, rate_spread)
        hy_pct   = _percentile_rank(hy_series, hy_spread)

    def _level(pct: "float | None") -> str:
        # 백분위가 없으면 방향을 말할 수 없다. `Normal` 을 쓰는 것은 프론트
        # `badge()` 가 Low/High 가 아닌 모든 값을 '정상' 으로 그리기 때문이고,
        # 즉 None 을 보내도 화면은 같다. 그 표시를 고치는 것은 프론트 몫이다.
        if pct is None:
            return "Normal"
        if pct < 33:
            return "Low"
        if pct > 67:
            return "High"
        return "Normal"

    rate_level = _level(rate_pct)
    hy_level   = _level(hy_pct)

    # 금리차: 낮음(역전)=위험(빨강), 높음(가팔라짐)=안전(초록)
    rate_color = {"Low": "#ef4444", "Normal": "#f59e0b", "High": "#10b981"}[rate_level]
    # HY 스프레드: 높음=위험(빨강), 낮음=안전(초록)
    hy_color   = {"Low": "#10b981", "Normal": "#f59e0b", "High": "#ef4444"}[hy_level]

    return {
        "rate_spread": {
            "value": round(rate_spread, 3),
            "percentile": round(rate_pct, 1) if rate_pct is not None else None,
            "level": rate_level, "color": rate_color,
        },
        "hy_spread": {
            "value": round(hy_spread, 3),
            "percentile": round(hy_pct, 1) if hy_pct is not None else None,
            "level": hy_level, "color": hy_color,
        },
        "source": source,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 10. 기술적 차트 상세 (볼린저 밴드 + 저항선 + 키포인트)
# ══════════════════════════════════════════════════════════════════════════════

def technical_chart_detail(
    price: pd.Series,
    window: int = 20,
    n_std: float = 2.0,
    resistance_lookback: int = 55,
    ohlc_df: "pd.DataFrame | None" = None,
) -> dict:
    """
    가격(Close) + OHLC + 볼린저밴드 + 이동평균(5/30/60/120) + z-score 시계열 + 키포인트 반환.
    ohlc_df: Open/High/Low/Close 컬럼 포함 DataFrame (있을 때만 캔들용 open/high/low 반환)
    """
    price = price.dropna()

    # ohlc_df 인덱스 timezone 정규화
    if ohlc_df is not None:
        ohlc_df = ohlc_df.copy()
        if hasattr(ohlc_df.index, "tz") and ohlc_df.index.tz is not None:
            ohlc_df.index = ohlc_df.index.tz_localize(None)
        if hasattr(price.index, "tz") and price.index.tz is not None:
            price.index = price.index.tz_localize(None)

    mr = mean_reversion_signal(price, window=window, n_std=n_std)
    resistance = price.rolling(resistance_lookback).max().shift(1)

    mid, upper, lower, zscore = mr["mid_band"], mr["upper_band"], mr["lower_band"], mr["zscore"]

    ma5   = price.rolling(5).mean()
    ma30  = price.rolling(30).mean()
    ma60  = price.rolling(60).mean()
    ma120 = price.rolling(120).mean()

    # 키포인트: 밴드 이탈 전환 순간 + 저항선 돌파 순간
    points: list[dict] = []
    prev_state = None
    for i, date in enumerate(price.index):
        p = float(price.iloc[i])
        u, l = upper.iloc[i], lower.iloc[i]
        if pd.isna(u) or pd.isna(l):
            continue
        state = "above" if p > u else "below" if p < l else "inside"
        if prev_state is not None and state != prev_state:
            if state == "above":
                points.append({"date": date.strftime("%Y-%m-%d"), "price": round(p, 2), "type": "BAND_BREAK_UP"})
            elif state == "below":
                points.append({"date": date.strftime("%Y-%m-%d"), "price": round(p, 2), "type": "BAND_BREAK_DOWN"})
        r = resistance.iloc[i]
        if not pd.isna(r) and p > r and i > 0 and float(price.iloc[i - 1]) <= r:
            points.append({"date": date.strftime("%Y-%m-%d"), "price": round(p, 2), "type": "RESISTANCE_BREAK"})
        prev_state = state

    def _r(v) -> "float | None":
        return round(float(v), 2) if not pd.isna(v) else None

    def _ohlc(date, col: str) -> "float | None":
        if ohlc_df is None:
            return None
        try:
            v = ohlc_df.loc[date, col]
            return round(float(v), 2) if not pd.isna(v) else None
        except (KeyError, TypeError):
            return None

    series = []
    for i, date in enumerate(price.index):
        close = round(float(price.iloc[i]), 2)
        series.append({
            "date":       date.strftime("%Y-%m-%d"),
            "open":       _ohlc(date, "Open")  or close,
            "high":       _ohlc(date, "High")  or close,
            "low":        _ohlc(date, "Low")   or close,
            "price":      close,
            "mid":        _r(mid.iloc[i]),
            "upper":      _r(upper.iloc[i]),
            "lower":      _r(lower.iloc[i]),
            "zscore":     round(float(zscore.iloc[i]), 3) if not pd.isna(zscore.iloc[i]) else None,
            "ma5":        _r(ma5.iloc[i]),
            "ma30":       _r(ma30.iloc[i]),
            "ma60":       _r(ma60.iloc[i]),
            "ma120":      _r(ma120.iloc[i]),
            "resistance": _r(resistance.iloc[i]),
        })

    current_z = mr["current_z"]
    bias = "LONG" if current_z <= -1.0 else "SHORT" if current_z >= 1.0 else "NEUTRAL"

    return {
        "series":         series,
        "key_points":     points,
        "current_z":      round(float(current_z), 3),
        "current_signal": mr["current_signal"],   # _signal_at 이 이미 None 으로 정규화한다
        "bias":           bias,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 11. 페어 트레이딩 자동 탐색 상세 (비교차트 + 스프레드)
# ══════════════════════════════════════════════════════════════════════════════

def pairs_auto_detail(
    ticker_a: str,
    close_df: pd.DataFrame,
    candidates: list[str],
    threshold_pct: float = 5.0,
    top_n: int = 5,
) -> dict:
    """
    ticker_a와 가장 유사한 종목을 close_df(이미 캐시된 가격) 내에서 탐색하고,
    두 종목의 실제 가격 비교 + 스프레드(%) + 임계치 초과 구간을 반환.

    스프레드는 표시 구간 첫날에 고정 인덱싱하지 않고, 가격비(A/B)의 60일 롤링
    평균 대비 괴리율로 계산한다. 첫날 기준으로 고정하면 아주 예전(예: 2년 전)의
    일회성 괴리가 그 날짜 이후 모든 스프레드 값에 영구히 더해져, 실제로는 두
    종목이 다시 같은 방향으로 움직이고 있어도 격차가 좁혀지지 않고 계속 벌어지는
    것처럼 보인다. 롤링 평균은 시간이 지나며 같이 움직이므로 오래된 괴리는
    자연히 창 밖으로 밀려나 스프레드가 0 근방에서 다시 진동할 수 있다.
    """
    SPREAD_WINDOW = 60  # 페어 트레이딩 기본 lookback(pairs_trading_signal)과 동일한 관례

    pool = [t for t in candidates if t in close_df.columns and t != ticker_a]
    if ticker_a not in close_df.columns or not pool:
        return {"matches": [], "best": None}

    price_a = close_df[ticker_a].dropna()
    vol_a = price_a.pct_change().rolling(20).std().dropna()

    scored = []
    for t in pool:
        price_b = close_df[t].dropna()
        common = price_a.index.intersection(price_b.index)
        if len(common) < 60:
            continue
        vol_b = price_b.pct_change().rolling(20).std().dropna()
        common_vol = vol_a.index.intersection(vol_b.index)
        if len(common_vol) < 30:
            continue
        sim = float(vol_a.loc[common_vol].corr(vol_b.loc[common_vol]))
        if sim != sim:
            continue
        scored.append((t, sim, common))

    if not scored:
        return {"matches": [], "best": None}

    scored.sort(key=lambda x: abs(x[1]), reverse=True)
    matches = [{"ticker": t, "correlation": round(c, 4)} for t, c, _ in scored[:top_n]]

    best_ticker = scored[0][0]

    # 상위 top_n 페어 각각의 차트 데이터 계산 (실제 주가 + 롤링 기준 스프레드)
    all_charts: dict = {}
    all_breaches: dict = {}

    for pair_ticker, _, pair_common in scored[:top_n]:
        pa = price_a.loc[pair_common]
        pb = close_df[pair_ticker].loc[pair_common]
        # 이력이 짧은 페어(최소 60일 보장선 근처)도 워밍업 구간에 전체를 다 뺏기지
        # 않도록, 표시 구간 길이에 맞춰 window 를 줄인다. 통상(2년치, ~500행)엔 60 그대로.
        window    = max(10, min(SPREAD_WINDOW, len(pair_common) // 3))
        ratio     = pa / pb
        ratio_ref = ratio.rolling(window).mean()   # '최근 정상 상태' — 시간에 따라 같이 이동
        spread_pct = (ratio / ratio_ref - 1.0) * 100

        # 롤링 평균이 아직 다 차지 않은 워밍업 구간(첫 window-1일)은 NaN —
        # 그 구간은 표시하지 않는다 (그래프에 무의미한 값이나 null 을 보내지 않는다).
        valid_dates = spread_pct.dropna().index

        pair_chart: list[dict] = []
        pair_breaches: list[dict] = []
        prev_outside = False
        for date in valid_dates:
            a_v = float(pa.loc[date])          # 실제 주가
            b_v = float(pb.loc[date])          # 실제 주가
            s_v = float(spread_pct.loc[date])  # 가격비의 60일 롤링평균 대비 괴리율(%)
            d_str = date.strftime("%Y-%m-%d")
            pair_chart.append({"date": d_str, "a": round(a_v, 2), "b": round(b_v, 2), "spread": round(s_v, 2)})
            curr_outside = abs(s_v) > threshold_pct
            if curr_outside and not prev_outside:
                pair_breaches.append({"date": d_str, "spread": round(s_v, 2)})
            prev_outside = curr_outside

        all_charts[pair_ticker] = pair_chart
        all_breaches[pair_ticker] = pair_breaches

    return {
        "matches":       matches,
        "best":          {"ticker": best_ticker, "correlation": round(scored[0][1], 4)},
        "charts":        all_charts,
        "all_breaches":  all_breaches,
        # backward-compat: best 페어 데이터를 최상위에도 노출
        "chart":         all_charts.get(best_ticker, []),
        "breaches":      all_breaches.get(best_ticker, []),
        "threshold_pct": threshold_pct,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 5-b. K-Means 기반 시장 국면 분류 (상승 / 횡보 / 하락 / 과열)
# ══════════════════════════════════════════════════════════════════════════════

# 국면 라벨 — 프론트엔드와 공유하는 고정 문자열
REGIME_UPTREND    = "Uptrend"
REGIME_SIDEWAYS   = "Sideways"
REGIME_DOWNTREND  = "Downtrend"
REGIME_OVERHEATED = "Overheated"

_KMEANS_FEATURES = [
    "disparity20",   # 종가 / MA20        — 추세 대비 위치
    "slope_ma5",     # MA5 5일 변화율      — 단기 추세 방향
    "vol_ratio",     # 거래량 / 20일 평균  — 참여 강도
    "atr_ratio",     # ATR14 / 종가        — 변동성 수준
]
# 시점 간 순서를 반영하기 위한 지연 피처 (5일 평균) — K-Means 는 순서를 모르므로
# 직전 구간의 상태를 함께 넣어 하루짜리 노이즈로 국면이 튀는 것을 막는다.
_LAG_FEATURES = [f"{c}_lag5" for c in _KMEANS_FEATURES]


# ══════════════════════════════════════════════════════════════════════════════
# 5-c. 카우프만 효율성 비율(ER) 기반 국면 판단
# ══════════════════════════════════════════════════════════════════════════════

REGIME_BULL     = "Bull"
REGIME_BEAR     = "Bear"
# 횡보는 위(REGIME_SIDEWAYS)와 동일한 문자열을 재사용한다.

ER_DEFAULT_WINDOW    = 20     # 약 1개월 거래일 — 국면 판단에 적당한 평활 수준
ER_DEFAULT_THRESHOLD = 0.30   # 사용자 지정 기본 임계값


def efficiency_ratio(price: pd.Series, window: int = ER_DEFAULT_WINDOW) -> pd.Series:
    """카우프만 효율성 비율.

        ER = |기간 내 순변동| / 기간 내 일별 절대변동의 합

    분자는 시작점→끝점의 직선 거리, 분모는 실제로 이동한 총 경로다.
    한 방향으로 곧게 가면 두 값이 같아져 ER→1, 위아래로 요동치며 제자리면 ER→0.
    즉 '추세의 강도'가 아니라 **'움직임의 방향성(효율)'**을 재는 지표다.

    분모가 0인 구간(가격이 전혀 안 움직인 날들)은 방향성을 정의할 수 없으므로 0 으로 둔다.
    """
    p = price.astype(float)
    net   = (p - p.shift(window)).abs()               # 순변동 (직선 거리)
    path  = p.diff().abs().rolling(window).sum()      # 총 경로 (실제 이동거리)
    er = net / path.replace(0.0, np.nan)
    return er.fillna(0.0).clip(0.0, 1.0)



def _backdate_trend_starts(
    price: pd.Series, labels: pd.Series, window: int,
) -> pd.Series:
    """감지된 추세 구간을 실제 시작점(직전 저점/고점)까지 소급한다.

    ER 은 후행 지표라 추세가 확인되는 시점이 실제 시작보다 늦다.
    Bull 이 처음 확인된 날에서 최대 window 만큼 거슬러 올라가 **최저가 지점**을
    찾고, 그 사이 '횡보'로 찍혀 있던 날들을 Bull 로 다시 칠한다.
    (Bear 는 최고가 지점 기준.)

    미래 데이터를 쓰지 않는다 — 이미 지나간 구간을 되돌아보며 라벨을 고칠 뿐이라
    과거 시점의 판단을 앞당겨 왜곡하는 look-ahead 가 아니다.
    다만 **오늘 라벨은 여전히 후행**이다(소급할 미래가 없으므로). 이는 어떤
    추세 지표에서도 피할 수 없는 한계다.
    """
    out = labels.copy()
    vals = price.values
    arr  = labels.values
    n = len(arr)

    i = 0
    while i < n:
        lab = arr[i]
        if lab == REGIME_SIDEWAYS:
            i += 1
            continue
        # 추세 구간의 끝 찾기
        j = i
        while j + 1 < n and arr[j + 1] == lab:
            j += 1

        # 소급 가능한 범위: 직전 횡보 구간 안, 최대 window 봉
        lo = max(0, i - window)
        k = i - 1
        while k >= lo and arr[k] == REGIME_SIDEWAYS:
            k -= 1
        seg_start = k + 1
        if seg_start >= i:
            i = j + 1
            continue

        # 전역 극점까지 무작정 거슬러 가면 횡보 구간 전체가 추세로 칠해진다.
        # 추세가 실제로 '시작된' 지점 = 확인 시점(i)에서 뒤로 걸으며 가격이
        # 단조롭게 낮아지는(Bull) / 높아지는(Bear) 동안만 인정한다.
        # 되돌림이 나오면 거기서 멈춘다 — 그 지점이 직전 스윙 저점/고점이다.
        pivot = i
        for m in range(i - 1, seg_start - 1, -1):
            better = vals[m] < vals[pivot] if lab == REGIME_BULL else vals[m] > vals[pivot]
            if better:
                pivot = m
            else:
                break
        if pivot < i:
            out.iloc[pivot:i] = lab
        i = j + 1

    return out

def detect_regime_er(
    price: pd.Series,
    window: int = ER_DEFAULT_WINDOW,
    threshold: float = ER_DEFAULT_THRESHOLD,
    backdate: bool = True,
) -> dict:
    """ER 기반 일자별 국면 분류 (상승 / 횡보 / 하락).

    판정
      ① ER < threshold                → 횡보 (방향성 없이 요동)
      ② ER ≥ threshold, 순변동 > 0    → 상승
      ③ ER ≥ threshold, 순변동 < 0    → 하락

    K-Means 방식 대비 장점: 학습·군집이 없어 결정적이고(같은 입력=같은 결과)
    종목·기간에 무관하게 임계값의 의미가 동일하다. 계산도 훨씬 가볍다.

    backdate=True (기본) — 감지된 추세를 **실제 시작점까지 소급**한다.
    ER 은 후행 창(t-window ~ t)을 쓰므로, 상승이 시작돼도 창의 대부분이 아직
    직전 횡보 구간이라 임계값을 넘기까지 수 거래일이 걸린다(실측 6거래일).
    그대로 두면 그래프에서 국면 색이 실제 전환점보다 오른쪽으로 밀려 보인다.
    """
    p = price.dropna().astype(float)
    if len(p) < window + 2:
        raise ValueError(f"ER 계산에 필요한 데이터 부족 ({len(p)}행, 최소 {window + 2}행)")

    er        = efficiency_ratio(p, window)
    net_delta = p - p.shift(window)          # 부호 있는 순변동 → 방향 결정

    labels = pd.Series(REGIME_SIDEWAYS, index=p.index, dtype=object)
    trending = er >= threshold
    labels[trending & (net_delta > 0)] = REGIME_BULL
    labels[trending & (net_delta < 0)] = REGIME_BEAR
    # 워밍업 구간(rolling 미충족)은 판단 불가 → 횡보로 두되 통계에서 빠지도록 잘라낸다
    valid = er.notna() & net_delta.notna()
    labels = labels[valid]
    er_valid = er[valid]

    if backdate and len(labels):
        labels = _backdate_trend_starts(p.loc[labels.index], labels, window)

    return {
        "regime_labels":  labels,
        "current_regime": str(labels.iloc[-1]) if len(labels) else "Unknown",
        "current_er":     round(float(er_valid.iloc[-1]), 4) if len(er_valid) else None,
        "er_series":      er_valid,
        "window":         window,
        "threshold":      threshold,
        "n_regimes":      3,
    }

"""
backend/services/trading_signals.py
────────────────────────────────────
pfp/trading_signals.py의 계산 로직을 백엔드 서비스로 직접 이식.
plot_* 함수는 Streamlit용이므로 pfp/에 유지하고 여기선 제외.
sys.path 조작 없이 독립 실행 가능.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

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
    current_signal = signals.iloc[-1]

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

def momentum_breakout_signal(
    price: pd.Series,
    volume: pd.Series | None,
    lookback: int = 55,
    volume_mult: float = 1.5,
) -> dict:
    resistance = price.rolling(lookback).max().shift(1)

    if volume is not None and len(volume) > 0:
        volume_avg    = volume.rolling(lookback).mean().shift(1)
        breakout_vol  = volume > volume_avg * volume_mult
        volume_ratio  = float(volume.iloc[-1] / volume_avg.iloc[-1]) if (
            not pd.isna(volume_avg.iloc[-1]) and volume_avg.iloc[-1] > 0
        ) else 1.0
    else:
        volume_avg   = pd.Series(np.nan, index=price.index)
        breakout_vol = pd.Series(True, index=price.index)
        volume_ratio = 1.0

    breakout = (price > resistance) & breakout_vol
    signals  = pd.Series(None, index=price.index, dtype=object)
    signals[breakout] = "BREAKOUT"

    return {
        "resistance":       resistance,
        "volume_avg":       volume_avg,
        "signals":          signals,
        "current_signal":   signals.iloc[-1],
        "current_price":    float(price.iloc[-1]),
        "is_breakout_today": bool(breakout.iloc[-1]) if len(breakout) > 0 else False,
        "volume_surge":     bool(breakout_vol.iloc[-1]) if len(breakout_vol) > 0 else False,
        "volume_ratio":     round(volume_ratio, 2),
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
    current_signal = signal_series.iloc[-1]

    return {
        "spread": spread, "zscore": zscore, "beta": float(beta),
        "signals": signal_series,
        "current_z": current_z, "current_signal": current_signal,
        "correlation": correlation, "is_valid_pair": is_valid_pair,
        "lock_message": lock_message,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 5. 시장 국면 감지 (K-means)
# ══════════════════════════════════════════════════════════════════════════════

def detect_market_regime(
    price: pd.Series,
    return_window: int = 20,
    vol_window: int = 20,
    n_regimes: int = 3,
) -> dict:
    from sklearn.cluster import KMeans

    ret      = price.pct_change().rolling(return_window).mean() * 252
    vol      = price.pct_change().rolling(vol_window).std() * np.sqrt(252)
    features = pd.DataFrame({"return": ret, "volatility": vol}).dropna()

    if len(features) < n_regimes * 3:
        return _regime_fallback(price)

    X      = features.values
    X_norm = (X - X.mean(axis=0)) / (X.std(axis=0) + 1e-9)

    km         = KMeans(n_clusters=n_regimes, n_init=10, random_state=42)
    raw_labels = km.fit_predict(X_norm)

    centers_orig = km.cluster_centers_ * X.std(axis=0) + X.mean(axis=0)
    order        = np.argsort(-centers_orig[:, 0])
    regime_names = ["Bull", "Sideways", "Bear"] if n_regimes == 3 else \
                   [f"Regime{i+1}" for i in range(n_regimes)]
    label_map    = {int(cid): regime_names[min(rank, len(regime_names)-1)]
                    for rank, cid in enumerate(order)}

    regime_labels  = pd.Series([label_map[int(c)] for c in raw_labels], index=features.index)
    current_regime = regime_labels.iloc[-1] if len(regime_labels) > 0 else "Unknown"

    return {
        "regime_labels":  regime_labels,
        "regime_raw":     pd.Series(raw_labels, index=features.index),
        "features":       features,
        "cluster_centers": centers_orig,
        "label_map":       label_map,
        "current_regime":  current_regime,
    }


def _regime_fallback(price: pd.Series) -> dict:
    ma_short = price.rolling(20).mean()
    ma_long  = price.rolling(60).mean()
    labels   = pd.Series(
        np.where(ma_short > ma_long * 1.02, "Bull",
                 np.where(ma_short < ma_long * 0.98, "Bear", "Sideways")),
        index=price.index,
    )
    valid = labels.dropna()
    return {
        "regime_labels": valid,
        "regime_raw": None, "features": None,
        "cluster_centers": None, "label_map": None,
        "current_regime": valid.iloc[-1] if len(valid) > 0 else "Unknown",
    }


# ══════════════════════════════════════════════════════════════════════════════
# 6. 최적 페어 탐색
# ══════════════════════════════════════════════════════════════════════════════

def find_best_pair(
    ticker_a: str,
    candidate_tickers: list[str],
    period: str = "1y",
    top_n: int = 5,
) -> list[dict]:
    import yfinance as yf

    candidates  = [t for t in candidate_tickers if t != ticker_a]
    all_tickers = list(set([ticker_a] + candidates))

    try:
        data  = yf.download(all_tickers, period=period, progress=False, auto_adjust=True)
        close = data["Close"].ffill().dropna()
    except Exception:
        return []

    if ticker_a not in close.columns:
        return []

    ret_a   = close[ticker_a].pct_change().dropna()
    results = []

    for t in candidates:
        if t not in close.columns:
            continue
        try:
            ret_b  = close[t].pct_change().dropna()
            common = ret_a.index.intersection(ret_b.index)
            if len(common) < 30:
                continue
            corr    = float(ret_a.loc[common].corr(ret_b.loc[common]))
            coint_p = 1.0
            try:
                from statsmodels.tsa.stattools import coint as _coint
                pa = close[ticker_a].dropna()
                pb = close[t].dropna()
                common2 = pa.index.intersection(pb.index)
                if len(common2) >= 60:
                    _, coint_p, _ = _coint(pa.loc[common2].values, pb.loc[common2].values)
            except Exception:
                pass

            score    = abs(corr) * 0.7 + (1.0 - min(coint_p, 1.0)) * 0.3
            abs_corr = abs(corr)
            grade    = "S" if abs_corr >= 0.85 else "A" if abs_corr >= 0.70 else \
                       "B" if abs_corr >= 0.55 else "C"

            results.append({
                "ticker": t, "correlation": round(corr, 3),
                "coint_p": round(coint_p, 3), "score": round(score, 4), "grade": grade,
            })
        except Exception:
            continue

    return sorted(results, key=lambda x: x["score"], reverse=True)[:top_n]


# ══════════════════════════════════════════════════════════════════════════════
# 7. 전수 스캔 (매수가 / 목표가 / 손절가 포함)
# ══════════════════════════════════════════════════════════════════════════════

def scan_universe_with_targets(
    price_df: pd.DataFrame,
    volume_df: pd.DataFrame | None,
    top_n: int = 10,
    bb_window: int = 20,
    stop_pct: float = 0.04,
) -> dict:
    long_picks:  list[dict] = []
    short_picks: list[dict] = []
    scanned = 0

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
                    "reason": f"하단밴드 이탈 (Z={z:.2f}) → 중앙선 ${mid:.2f} 회귀 기대",
                })
            elif signal == "SELL":
                short_picks.append({
                    "ticker":   ticker, "method": "볼린저밴드 하락",
                    "entry":    round(cur, 2), "target": round(mid, 2),
                    "stop":     round(cur * (1 + stop_pct), 2),
                    "downside": round((cur - mid) / cur * 100, 1),
                    "score":    abs(z),
                    "reason":   f"상단밴드 이탈 (Z={z:.2f}) → 중앙선 ${mid:.2f} 하락 기대",
                })
        except Exception:
            pass

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
                            "reason": f"N일 고점 ${resistance:.2f} 돌파 + 거래량 급증",
                        })
        except Exception:
            pass

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


def bollinger_scan_full_universe(
    close_df: pd.DataFrame,
    top_n: int = 10,
    window: int = 20,
    n_std: float = 2.0,
) -> dict:
    """
    유니버스 전체 종목에 볼린저 밴드 평균회귀 신호를 계산해 z-score 기준 순위화.
    가장 음수(과매도, BUY 후보) top_n / 가장 양수(과매수, SELL 후보) top_n 반환.
    """
    rows: list[dict] = []
    scanned = 0

    for ticker in close_df.columns:
        price = close_df[ticker].dropna()
        if len(price) < window + 5:
            continue
        scanned += 1
        try:
            mr = mean_reversion_signal(price, window=window, n_std=n_std)
            z = mr["current_z"]
            if z != z:  # NaN
                continue
            cur    = float(price.iloc[-1])
            mid    = float(mr["mid_band"].iloc[-1])
            upper  = float(mr["upper_band"].iloc[-1])
            lower  = float(mr["lower_band"].iloc[-1])
            rows.append({
                "ticker":     ticker,
                "z":          round(float(z), 3),
                "price":      round(cur, 2),
                "mid_band":   round(mid, 2),
                "upper_band": round(upper, 2),
                "lower_band": round(lower, 2),
                "pct_b":      round(float(mr.get("pct_b", 0.5)), 4),
            })
        except Exception:
            continue

    rows.sort(key=lambda r: r["z"])
    long_candidates  = [r for r in rows if r["z"] < 0]
    short_candidates = [r for r in rows if r["z"] > 0]
    long_candidates.sort(key=lambda r: r["z"])               # 가장 음수 먼저
    short_candidates.sort(key=lambda r: r["z"], reverse=True)  # 가장 양수 먼저

    def _to_pick(r: dict, side: str) -> dict:
        move_pct = round((r["mid_band"] - r["price"]) / r["price"] * 100, 1) if r["price"] else 0.0
        return {
            "ticker":     r["ticker"],
            "z":          r["z"],
            "entry":      r["price"],
            "target":     r["mid_band"],
            "upper_band": r["upper_band"],
            "lower_band": r["lower_band"],
            "pct_b":      r["pct_b"],
            "move_pct":   move_pct if side == "long" else -move_pct,
            "reason": (
                f"하단밴드 이탈 (Z={r['z']:.2f}) → 중앙선 ${r['mid_band']:.2f} 회귀 기대"
                if side == "long" else
                f"상단밴드 이탈 (Z={r['z']:.2f}) → 중앙선 ${r['mid_band']:.2f} 하락 기대"
            ),
        }

    return {
        "long_picks":  [_to_pick(r, "long")  for r in long_candidates[:top_n]],
        "short_picks": [_to_pick(r, "short") for r in short_candidates[:top_n]],
        "scanned":     scanned,
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
        rate_series = pd.Series([0.5])
        hy_series   = pd.Series([3.5])
        rate_spread, hy_spread = 0.5, 3.5
        source = "fallback"

    rate_pct = _percentile_rank(rate_series, rate_spread)
    hy_pct   = _percentile_rank(hy_series, hy_spread)

    def _level(pct: float) -> str:
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
            "value": round(rate_spread, 3), "percentile": round(rate_pct, 1),
            "level": rate_level, "color": rate_color,
        },
        "hy_spread": {
            "value": round(hy_spread, 3), "percentile": round(hy_pct, 1),
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
        "current_signal": str(mr["current_signal"]) if mr["current_signal"] else None,
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
    두 종목의 인덱스화 가격 비교 + 스프레드(%) + 임계치 초과 구간을 반환.
    """
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

    # 상위 top_n 페어 각각의 차트 데이터 계산 (실제 주가 반환, 스프레드는 인덱스화 기반)
    all_charts: dict = {}
    all_breaches: dict = {}

    for pair_ticker, _, pair_common in scored[:top_n]:
        pa = price_a.loc[pair_common]
        pb = close_df[pair_ticker].loc[pair_common]
        idx_a = (pa / float(pa.iloc[0]) * 100)
        idx_b = (pb / float(pb.iloc[0]) * 100)
        spread_pct = idx_a - idx_b

        pair_chart: list[dict] = []
        pair_breaches: list[dict] = []
        prev_outside = False
        for date in pair_common:
            a_v = float(pa.loc[date])          # 실제 주가
            b_v = float(pb.loc[date])          # 실제 주가
            s_v = float(spread_pct.loc[date])  # 인덱스화 기반 스프레드(%)
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

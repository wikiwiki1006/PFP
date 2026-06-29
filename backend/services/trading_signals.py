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

# ── 매크로 저승사자 임계값 ──────────────────────────────────────────────────────
DOOM_RATE_SPREAD_THRESHOLD = 0.0
DOOM_HY_SPREAD_THRESHOLD   = 5.0

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
# 1. 매크로 저승사자 레이더
# ══════════════════════════════════════════════════════════════════════════════

def fetch_macro_doom_indicators() -> dict:
    """장단기 금리차(T10Y2Y) + 하이일드 OAS를 FRED에서 조회. 실패 시 yfinance 근사."""
    from datetime import datetime, timedelta
    try:
        import pandas_datareader.data as web
        start = datetime.now() - timedelta(days=400)
        df = web.DataReader(["T10Y2Y", "BAMLH0A0HYM2"], "fred", start).dropna()
        return {
            "rate_spread": float(df["T10Y2Y"].iloc[-1]),
            "hy_spread":   float(df["BAMLH0A0HYM2"].iloc[-1]),
            "rate_spread_series": df["T10Y2Y"],
            "hy_spread_series":   df["BAMLH0A0HYM2"],
            "source": "FRED",
        }
    except Exception:
        pass

    try:
        import yfinance as yf
        data = yf.download(["HYG", "IEF", "^TNX", "^FVX"], period="1y",
                           progress=False, auto_adjust=True)["Close"].dropna()
        ratio = data["HYG"] / data["IEF"]
        ratio_chg = (ratio / ratio.rolling(60).mean() - 1) * -20
        hy_approx = (ratio_chg + 4.0).clip(lower=2.0)
        rate_approx = (data["^TNX"] / 10) - (data["^FVX"] / 10) + 0.3
        return {
            "rate_spread": float(rate_approx.iloc[-1]),
            "hy_spread":   float(hy_approx.iloc[-1]),
            "rate_spread_series": rate_approx,
            "hy_spread_series":   hy_approx,
            "source": "yfinance(근사)",
        }
    except Exception:
        pass

    return {
        "rate_spread": 0.5, "hy_spread": 3.5,
        "rate_spread_series": None, "hy_spread_series": None,
        "source": "기본값(접속실패)",
    }


def evaluate_doom_radar(rate_spread: float, hy_spread: float) -> dict:
    rate_inverted = rate_spread < DOOM_RATE_SPREAD_THRESHOLD
    hy_elevated   = hy_spread   > DOOM_HY_SPREAD_THRESHOLD
    is_doom       = rate_inverted or hy_elevated

    if rate_inverted and hy_elevated:
        severity = "경보"
        comment  = (f"경고: 장단기 금리차 {rate_spread:+.2f}%p 역전 + "
                    f"HY 스프레드 {hy_spread:.1f}% 위험. 매수 신호 무효화.")
    elif rate_inverted:
        severity = "주의"
        comment  = f"주의: 장단기 금리차 {rate_spread:+.2f}%p 역전. 신규 매수 보수적 접근."
    elif hy_elevated:
        severity = "주의"
        comment  = f"주의: HY 스프레드 {hy_spread:.1f}% 위험 수준. 신용경색 초기 신호."
    else:
        severity = "평시"
        comment  = (f"금리차 {rate_spread:+.2f}%p · HY {hy_spread:.1f}% — "
                    f"정상 범위. 매매 신호 활용 가능.")

    return {
        "is_doom":       is_doom,
        "rate_inverted": rate_inverted,
        "hy_elevated":   hy_elevated,
        "severity":      severity,
        "comment":       comment,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 2. 평균 회귀 (볼린저 밴드)
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

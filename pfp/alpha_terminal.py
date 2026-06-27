"""
pages/alpha_terminal.py
━━━━━━━━━━━━━━━━━━━━━━━
기존 my.py 코드를 페이지 함수로 래핑.
holdings를 session_state에 저장해 매크로/레포트와 공유.
"""

import json
import os
import textwrap
from datetime import datetime, timedelta
from pathlib import Path

import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
import pytz
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

BASE_DIR = Path(__file__).parent

# holdings.json이 data/ 하위 폴더에 있는지, 루트에 바로 있는지 자동 감지.
_root_db = BASE_DIR / "holdings.json"
_data_db = BASE_DIR / "data" / "holdings.json"

if _data_db.exists():
    DATA_DIR = BASE_DIR / "data"
elif _root_db.exists():
    DATA_DIR = BASE_DIR
else:
    DATA_DIR = BASE_DIR  # 둘 다 없으면(최초 실행) 루트를 기본으로 사용

DB_FILE  = DATA_DIR / "holdings.json"
LOG_FILE = DATA_DIR / "trade_log.json"


# ── 저장/로드 ─────────────────────────────────────────────────────────────────
def save_data():
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DB_FILE, "w") as f:
        json.dump({"my_holdings": st.session_state.holdings}, f, indent=4)
    with open(LOG_FILE, "w") as f:
        json.dump(st.session_state.trade_log, f, indent=4, ensure_ascii=False)
    # 방금 우리가 직접 쓴 파일이므로, 다음 렌더에서 "파일이 바뀌었다"고
    # 오인해 자기 자신을 다시 읽어들이지 않도록 시그니처를 갱신해둔다.
    st.session_state["_holdings_file_sig"] = DB_FILE.stat().st_mtime


def _load_from_file():
    holdings, log = None, []
    if DB_FILE.exists():
        with open(DB_FILE, "r") as f:
            raw = json.load(f)
            # 두 가지 포맷 지원: {"my_holdings": {...}} 또는 {...}
            holdings = raw.get("my_holdings", raw)
    if LOG_FILE.exists():
        with open(LOG_FILE, "r") as f:
            log = json.load(f)
    return holdings, log


# ── 시장 데이터 ───────────────────────────────────────────────────────────────
@st.cache_data(ttl=30)
def load_market_data(tickers_tuple):
    import yfinance as yf
    needed = ['^GSPC', '^IXIC', 'XLK','XLF','XLE','XLY','XLV','XLI','XLB',
              'BTC-USD','GC=F','^VIX','^TNX','^IRX','CL=F','USDKRW=X','SPY',
              '^KS11', '^KQ11']  # 코스피, 코스닥
    all_t = list(set(list(tickers_tuple) + needed))
    data = yf.download(all_t, period="5y", progress=False, auto_adjust=True)
    return data['Close'].ffill()


@st.cache_data(ttl=3600)
def get_fred_macro():
    """Fed 기준금리, 실업률, 10Y/2Y 국채금리를 FRED에서 실데이터로 조회."""
    try:
        import pandas_datareader.data as web
        start = datetime.now() - timedelta(days=90)
        df = web.DataReader(
            ['FEDFUNDS', 'UNRATE', 'DGS10', 'DGS2'], 'fred', start
        ).ffill().dropna()
        return {
            "fed_rate": float(df['FEDFUNDS'].iloc[-1]),
            "unemployment": float(df['UNRATE'].iloc[-1]),
            "y10": float(df['DGS10'].iloc[-1]),
            "y2": float(df['DGS2'].iloc[-1]),
            "source": "FRED",
        }
    except Exception:
        return {
            "fed_rate": 5.33, "unemployment": 3.9,
            "y10": 4.2, "y2": 4.5, "source": "기본값(FRED 접속실패)",
        }


@st.cache_data(ttl=3600)
def get_earnings_dividends(tickers_tuple):
    """
    종목별 다음 실적 발표일과 최근 배당 정보를 yfinance에서 실시간 조회.

    Returns
    -------
    dict[ticker] -> {"earn_date": str, "div_date": str, "div_yield": str}
    """
    import yfinance as yf
    result = {}
    for t in tickers_tuple:
        try:
            tk = yf.Ticker(t)

            # ── 다음 실적 발표일 ──────────────────────────────────────────────
            earn_date = "N/A"
            try:
                cal = tk.calendar
                if isinstance(cal, dict) and "Earnings Date" in cal:
                    ed = cal["Earnings Date"]
                    if isinstance(ed, list) and len(ed) > 0:
                        earn_date = ed[0].strftime("%b %d")
                elif hasattr(cal, "loc") and "Earnings Date" in getattr(cal, "index", []):
                    ed = cal.loc["Earnings Date"]
                    earn_date = pd.to_datetime(ed.iloc[0]).strftime("%b %d")
            except Exception:
                pass

            # ── 최근 배당 지급일 + 배당수익률 ────────────────────────────────
            div_date = "N/A"
            div_yield = "N/A"
            try:
                divs = tk.dividends
                if len(divs) > 0:
                    div_date = divs.index[-1].strftime("%b %d")
                info = tk.info
                dy = info.get("dividendYield")
                if dy:
                    div_yield = f"{dy*100:.2f}%" if dy < 1 else f"{dy:.2f}%"
            except Exception:
                pass

            result[t] = {"earn_date": earn_date, "div_date": div_date, "div_yield": div_yield}
        except Exception:
            result[t] = {"earn_date": "N/A", "div_date": "N/A", "div_yield": "N/A"}

    return result


# ── 포트폴리오 베타 계산 ─────────────────────────────────────────────────────
def calculate_portfolio_beta(holdings: dict, close_df: pd.DataFrame, benchmark: str = '^GSPC') -> float:
    """
    보유종목별 시장(S&P500) 대비 베타를 가중평균해 포트폴리오 전체 베타 산출.
    CASH는 베타 0으로 취급(시장과 무관). 데이터 부족/오류 시 1.0(시장평균) 폴백.
    """
    try:
        if benchmark not in close_df.columns:
            return 1.0
        mkt_ret = close_df[benchmark].pct_change().dropna()
        mkt_var = mkt_ret.var()
        if mkt_var <= 1e-12:
            return 1.0

        stock_tickers = [t for t in holdings if t != 'CASH' and t in close_df.columns]
        if not stock_tickers:
            return 1.0

        latest = close_df.iloc[-1]
        values, betas = [], []
        for t in stock_tickers:
            stock_ret = close_df[t].pct_change().dropna()
            common_idx = stock_ret.index.intersection(mkt_ret.index)
            if len(common_idx) < 30:
                beta_t = 1.0
            else:
                cov = np.cov(stock_ret.loc[common_idx], mkt_ret.loc[common_idx])[0, 1]
                beta_t = cov / mkt_var if mkt_var > 0 else 1.0
            v = float(latest.get(t, 0)) * holdings[t]['q']
            values.append(v)
            betas.append(beta_t)

        cash_val = holdings.get('CASH', {}).get('q', 0)
        total_val = sum(values) + cash_val
        if total_val <= 0:
            return 1.0

        weighted_beta = sum(v * b for v, b in zip(values, betas)) / total_val
        return float(np.clip(weighted_beta, -2.0, 3.0))
    except Exception:
        return 1.0


def _vix_signal(vix_value: float) -> dict:
    """VIX 수준에 따른 공포 신호등 상태 산출."""
    if vix_value < 20:
        return {"label": "정상", "color": "#22c55e", "icon": "🟢"}
    elif vix_value < 30:
        return {"label": "주의", "color": "#f59e0b", "icon": "🟡"}
    else:
        return {"label": "발작", "color": "#ef4444", "icon": "🔴"}


def _render_ticker_marquee(close_df: pd.DataFrame):
    """블룸버그 스타일 좌→우 무한 스크롤 주가 전광판."""
    items_def = [
        ("S&P 500", "^GSPC", "$"),
        ("NASDAQ", "^IXIC", "$"),
        ("KOSPI", "^KS11", "₩"),
        ("KOSDAQ", "^KQ11", "₩"),
        ("USD/KRW", "USDKRW=X", "₩"),
        ("WTI", "CL=F", "$"),
        ("GOLD", "GC=F", "$"),
        ("BTC", "BTC-USD", "$"),
        ("VIX", "^VIX", ""),
    ]

    chips = []
    for label, ticker, prefix in items_def:
        if ticker not in close_df.columns:
            continue
        series = close_df[ticker].dropna()
        if len(series) < 2:
            continue
        cur, prev = float(series.iloc[-1]), float(series.iloc[-2])
        chg_pct = (cur / prev - 1) * 100 if prev else 0
        clr = "#39d353" if chg_pct >= 0 else "#ff7b72"
        arrow = "▲" if chg_pct >= 0 else "▼"
        val_fmt = f"{prefix}{cur:,.0f}" if cur >= 100 else f"{prefix}{cur:,.2f}"
        chips.append(
            f'<span style="margin-right:36px;white-space:nowrap;">'
            f'<b style="color:#e0e0e0;">{label}</b> '
            f'<span style="color:#8b949e;">{val_fmt}</span> '
            f'<span style="color:{clr};font-weight:700;">{arrow} {abs(chg_pct):.2f}%</span>'
            f'</span>'
        )

    if not chips:
        return

    # 끊김 없이 흐르도록 동일 시퀀스를 두 번 이어붙여 CSS 애니메이션 루프 처리
    track_html = "".join(chips)

    style_block = (
        "<style>"
        ".ticker-marquee-wrap{width:100%;overflow:hidden;background:#0a0e14;"
        "border:1px solid #21262d;border-radius:3px;padding:7px 0;"
        "margin-bottom:10px;box-sizing:border-box;}"
        ".ticker-marquee-track{display:inline-block;white-space:nowrap;"
        "font-family:'JetBrains Mono',monospace;font-size:12px;"
        "animation:ticker-scroll 38s linear infinite;padding-left:100%;}"
        ".ticker-marquee-wrap:hover .ticker-marquee-track{animation-play-state:paused;}"
        "@keyframes ticker-scroll{0%{transform:translateX(0);}100%{transform:translateX(-100%);}}"
        "</style>"
    )
    marquee_html = (
        style_block +
        '<div class="ticker-marquee-wrap">'
        f'<div class="ticker-marquee-track">{track_html}&nbsp;&nbsp;&nbsp;&nbsp;{track_html}</div>'
        '</div>'
    )
    st.markdown(marquee_html, unsafe_allow_html=True)


# ── 실제 매매 이력 기반 Equity Curve 산출 ─────────────────────────────────────
def build_realistic_equity_curve(holdings: dict, trade_log: list, close_df: pd.DataFrame) -> pd.Series:
    """
    trade_log(실제 매매 이력)를 날짜순으로 재생해, 각 시점의 실제 보유수량으로
    포트폴리오 가치를 계산한 '진짜' Equity Curve를 만든다.

    기존 방식(현재 보유수량을 과거 전체 구간에 고정 적용)과 달리, 매수 이전
    구간은 그 종목 수량을 0으로 취급해 매매 타이밍이 실제로 반영된다.
    trade_log가 비어있거나 부실하면(과거 거래기록 없이 초기 세팅만 있는 경우)
    현재 보유수량을 전체 구간에 적용하는 기존 방식으로 안전하게 폴백한다.
    """
    stock_tickers = [t for t in holdings if t != 'CASH' and t in close_df.columns]
    cash_now = holdings.get('CASH', {}).get('q', 0)

    if not trade_log:
        # 거래 이력이 없으면 기존 방식(현재 수량 고정)으로 폴백
        return (close_df[stock_tickers] * [holdings[t]['q'] for t in stock_tickers]).sum(axis=1) + cash_now

    try:
        log_df = pd.DataFrame(trade_log)
        log_df['date'] = pd.to_datetime(log_df['date'])
        log_df = log_df.sort_values('date')

        logged_tickers = set(log_df['ticker'].unique())

        holdings_over_time = pd.DataFrame(0.0, index=close_df.index, columns=stock_tickers)
        for t in stock_tickers:
            t_log = log_df[log_df['ticker'] == t].sort_values('date')
            if t_log.empty:
                continue
            qty_series = pd.Series(0.0, index=close_df.index)
            running = 0.0
            for _, row in t_log.iterrows():
                q = float(row.get('q', 0))
                if row['type'] == 'ADD':
                    running += q
                elif row['type'] == 'SOLD':
                    running -= q
                elif row['type'] == 'UPDATE':
                    running = q  # UPDATE는 절대값 세팅
                qty_series.loc[qty_series.index >= row['date']] = running
            holdings_over_time[t] = qty_series

        # 거래 이력에 전혀 없지만 현재 보유 중인 종목(초기 세팅값)은
        # 데이터 시작부터 현재 수량으로 보유했던 것으로 간주
        for t in stock_tickers:
            if t not in logged_tickers and holdings[t]['q'] > 0:
                holdings_over_time[t] = holdings[t]['q']

        equity = (close_df[stock_tickers] * holdings_over_time).sum(axis=1) + cash_now

        if equity.iloc[-1] <= 0 or equity.replace(0, np.nan).dropna().empty:
            raise ValueError("재구성된 equity curve가 비정상")

        return equity

    except Exception:
        return (close_df[stock_tickers] * [holdings[t]['q'] for t in stock_tickers]).sum(axis=1) + cash_now


def _compute_alpha_badge(p_perf: pd.Series, bench_perf: pd.Series | None) -> dict | None:
    """포트폴리오 누적수익률 vs 벤치마크 누적수익률을 비교해 알파 배지 정보 산출."""
    if bench_perf is None or len(bench_perf) == 0 or len(p_perf) == 0:
        return None
    try:
        port_final = float(p_perf.iloc[-1])
        bench_final = float(bench_perf.iloc[-1])
        alpha = port_final - bench_final
        return {
            "alpha": alpha,
            "is_winning": alpha > 0,
            "port_final": port_final,
            "bench_final": bench_final,
        }
    except Exception:
        return None


# ── 11대 GICS 섹터 ETF 등락률 표 ───────────────────────────────────────────────

# (표시 라벨, 티커) — GICS 11개 섹터 대표 ETF
GICS_SECTOR_ETFS = [
    ("TECHNOLOGY (정보기술)",          "XLK"),
    ("FINANCIALS (금융)",              "XLF"),
    ("COMMUNICATION SERVICES (통신)",  "XLC"),
    ("CONSUMER DISCRETIONARY (경기소비재)", "XLY"),
    ("HEALTHCARE (헬스케어)",          "XLV"),
    ("INDUSTRIALS (산업재)",           "XLI"),
    ("CONSUMER STAPLES (필수소비재)",  "XLP"),
    ("ENERGY (에너지)",                "XLE"),
    ("UTILITIES (유틸리티)",           "XLU"),
    ("MATERIALS (소재)",               "XLB"),
    ("REAL ESTATE (부동산)",           "XLRE"),
]

SECTOR_ETF_TICKERS = tuple(etf for _, etf in GICS_SECTOR_ETFS)


@st.cache_data(ttl=60)
def _load_sector_etf_prices() -> pd.DataFrame:
    """11개 GICS 섹터 ETF의 최근 2일 종가."""
    import yfinance as yf
    data = yf.download(list(SECTOR_ETF_TICKERS), period="5d", progress=False, auto_adjust=True)
    return data['Close'].ffill()


def _chg_color(pct: float) -> str:
    """등락률에 따른 색상: 양수=초록, 음수=빨강, 0 근처=회색."""
    if pct > 0.05:
        return "#39d353"
    elif pct < -0.05:
        return "#ff7b72"
    else:
        return "#8b949e"


def get_sector_etf_changes() -> dict:
    """{ticker: chg_pct} 형태로 섹터 ETF 등락률 반환. AI Analyst 등 다른 곳에서도 재사용."""
    try:
        etf_df = _load_sector_etf_prices()
        if etf_df.empty or len(etf_df) < 2:
            return {}
        cur_row, prev_row = etf_df.iloc[-1], etf_df.iloc[-2]
        result = {}
        for _, etf in GICS_SECTOR_ETFS:
            if etf in etf_df.columns:
                c, p = cur_row.get(etf), prev_row.get(etf)
                if pd.notna(c) and pd.notna(p) and p:
                    result[etf] = (float(c) / float(p) - 1) * 100
        return result
    except Exception:
        return {}


def _render_sector_treemap(holdings: dict):
    """11대 GICS 섹터 ETF의 오늘 등락률을 HOLDINGS와 동일한 표 형태로 표시."""
    try:
        with st.spinner("섹터 매트릭스 로딩 중..."):
            etf_df = _load_sector_etf_prices()
    except Exception as e:
        st.warning(f"섹터 데이터 로드 실패: {e}")
        return

    if etf_df.empty or len(etf_df) < 2:
        st.warning("섹터 데이터가 부족합니다.")
        return

    cur_row, prev_row = etf_df.iloc[-1], etf_df.iloc[-2]

    rows = []
    for label, etf in GICS_SECTOR_ETFS:
        if etf not in etf_df.columns:
            continue
        c, p = cur_row.get(etf), prev_row.get(etf)
        if pd.isna(c) or pd.isna(p) or p == 0:
            continue
        chg = (float(c) / float(p) - 1) * 100
        sector_name = label.split(" (")[0]
        rows.append({
            "Sector": sector_name,
            "Ticker": etf,
            "Price": round(float(c), 2),
            "Change%": round(chg, 2),
        })

    if not rows:
        st.warning("섹터 매트릭스를 구성할 데이터가 부족합니다.")
        return

    sec_df = pd.DataFrame(rows)

    def _style_change(val):
        if val > 0.05:
            return "color: #39d353; font-weight: 700;"
        elif val < -0.05:
            return "color: #ff7b72; font-weight: 700;"
        else:
            return "color: #8b949e;"

    try:
        styled = sec_df.style.map(_style_change, subset=["Change%"])
    except AttributeError:
        # pandas < 2.1 호환 (Styler.map 미지원 시 applymap 폴백)
        styled = sec_df.style.applymap(_style_change, subset=["Change%"])
    st.dataframe(
        styled, use_container_width=True, hide_index=True,
        column_config={
            "Price":    st.column_config.NumberColumn(format="$%.2f"),
            "Change%":  st.column_config.NumberColumn(format="%+.2f%%"),
        }
    )
    st.caption("초록 = 상승 · 빨강 = 하락 · 회색 = 보합 (1-Day Change)")


def _render_timing_signals_board(holdings: dict):
    """S&P500 + 나스닥 전수 스캔 — 매수가/목표가/손절가 포함 타점 보드."""
    from trading_signals import SP500_NASDAQ_UNIVERSE, scan_universe_with_targets, fetch_macro_doom_indicators, evaluate_doom_radar
    import sys

    st.markdown("""
    <div style="padding:4px 0 10px;">
      <span style="font-size:14px;font-weight:800;color:#e0e0e0;">🎯 AI TIMING SIGNALS</span>
      <span style="font-size:11px;color:#8b949e;margin-left:8px;">S&P500 + 나스닥 전수 스캔 · 매수가/목표가/손절가</span>
    </div>
    """, unsafe_allow_html=True)

    port_tickers = [t for t in holdings if t != 'CASH']
    universe = sorted(set(SP500_NASDAQ_UNIVERSE + port_tickers))

    col_btn, col_info = st.columns([1, 4])
    with col_btn:
        run_scan = st.button("🔍 전수 스캔 실행", key="timing_scan_btn", use_container_width=True)
    with col_info:
        st.caption(f"스캔 대상: S&P500 + 나스닥 {len(universe)}종목")

    if run_scan:
        with st.spinner(f"{len(universe)}종목 스캔 중... (30~60초 소요)"):
            try:
                import yfinance as yf
                data = yf.download(universe, period="6mo", progress=False, auto_adjust=True)
                price_df  = data["Close"].ffill()
                volume_df = data["Volume"].ffill() if "Volume" in data.columns.get_level_values(0) else None

                result = scan_universe_with_targets(price_df, volume_df, top_n=10)

                # doom radar
                macro = fetch_macro_doom_indicators()
                doom  = evaluate_doom_radar(macro["rate_spread"], macro["hy_spread"])

                st.session_state["_timing_scan_result"] = {**result, "doom": doom, "price_df": price_df}

                # ── LENS Trader 신호 전송 ──────────────────────────────────
                try:
                    _lt_path = r"C:\Users\a6225\OneDrive\바탕 화면\lens_trader\lens_trader"
                    if _lt_path not in sys.path:
                        sys.path.insert(0, _lt_path)
                    from lens_exporter import export_signals

                    _signals = []
                    for c in result["long_picks"]:
                        _signals.append({"ticker": c["ticker"], "action": "BUY",
                                         "strategy": c["method"], "strength": min(c["score"]/4.0, 1.0),
                                         "price": c["entry"]})
                    for c in result["short_picks"]:
                        _signals.append({"ticker": c["ticker"], "action": "SELL",
                                         "strategy": c["method"], "strength": min(c["score"]/4.0, 1.0),
                                         "price": c["entry"]})
                    export_signals(signals=_signals, macro_block=doom["is_doom"],
                                   output_path=_lt_path + r"\signals.json")
                    st.toast("📡 LENS Trader 신호 전송 완료", icon="✅")
                except Exception as _e:
                    st.warning(f"LENS Trader 신호 전송 실패: {_e}")

            except Exception as e:
                st.error(f"스캔 실패: {e}")
                return

    scan_result = st.session_state.get("_timing_scan_result")
    if not scan_result:
        st.info("💡 '전수 스캔 실행' 버튼을 누르면 S&P500+나스닥 전체에서 오늘의 타점을 찾아줍니다.")
        return

    doom        = scan_result.get("doom", {})
    long_picks  = scan_result.get("long_picks", [])
    short_picks = scan_result.get("short_picks", [])
    port_set    = set(port_tickers)

    if doom.get("is_doom"):
        st.error("🚨 MACRO DOOM RADAR 경보 — 거시경제 위험 신호. BUY 신호 주의.")

    def _price_card(c, side):
        color    = "#22c55e" if side == "LONG" else "#ef4444"
        bg_color = "rgba(34,197,94,0.06)" if side == "LONG" else "rgba(239,68,68,0.06)"
        star     = "⭐ " if c["ticker"] in port_set else ""
        updown   = c.get("upside") or c.get("downside", 0)
        target_label = "목표가" if side == "LONG" else "목표가(숏)"
        return (
            f'<div style="background:#0d1117;border:1px solid #21262d;border-left:3px solid {color};'
            f'padding:12px 14px;border-radius:3px;margin-bottom:8px;">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">'
            f'<span style="color:#e0e0e0;font-size:14px;font-weight:800;">{star}{c["ticker"]}</span>'
            f'<span style="color:{color};font-size:10px;font-weight:700;background:{bg_color};'
            f'padding:2px 8px;border-radius:10px;">{c["method"]}</span>'
            f'</div>'
            f'<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:6px;">'
            f'<div><div style="color:#8b949e;font-size:9px;font-weight:700;">매수가(진입)</div>'
            f'<div style="color:#e0e0e0;font-size:13px;font-weight:700;">${c["entry"]:,.2f}</div></div>'
            f'<div><div style="color:#8b949e;font-size:9px;font-weight:700;">{target_label}</div>'
            f'<div style="color:{color};font-size:13px;font-weight:700;">${c["target"]:,.2f} '
            f'<span style="font-size:10px;">(+{updown:.1f}%)</span></div></div>'
            f'<div><div style="color:#8b949e;font-size:9px;font-weight:700;">손절가</div>'
            f'<div style="color:#ef4444;font-size:13px;font-weight:700;">${c["stop"]:,.2f}</div></div>'
            f'</div>'
            f'<div style="color:#8b949e;font-size:10px;">{c["reason"]}</div>'
            f'</div>'
        )

    col_long, col_short = st.columns(2)

    with col_long:
        st.markdown(f"<div style='font-size:12px;font-weight:700;color:#22c55e;margin-bottom:8px;'>🟢 LONG 타점 TOP 10</div>", unsafe_allow_html=True)
        if not long_picks:
            st.caption("오늘은 강한 LONG 신호가 없습니다.")
        for c in long_picks:
            st.markdown(_price_card(c, "LONG"), unsafe_allow_html=True)

    with col_short:
        st.markdown(f"<div style='font-size:12px;font-weight:700;color:#ef4444;margin-bottom:8px;'>🔴 SHORT 타점 TOP 10</div>", unsafe_allow_html=True)
        if not short_picks:
            st.caption("오늘은 강한 SHORT 신호가 없습니다.")
        for c in short_picks:
            st.markdown(_price_card(c, "SHORT"), unsafe_allow_html=True)

    st.caption(f"⭐ = 보유 종목 · 스캔 종목 수: {scan_result.get('scanned', 0)} · 신호는 통계 패턴 기반, 투자 조언 아님")


# ── 📡 실시간 뉴스 피드 + 🤖 AI Analyst 진단 박스 ──────────────────────────────

@st.cache_data(ttl=600)
def _fetch_portfolio_news(tickers_tuple: tuple, max_per_ticker: int = 2,
                           include_macro: bool = True, max_macro: int = 4) -> list[dict]:
    """
    보유종목별 + 거시경제(주요 지수/금리) 뉴스를 함께 가져와 최신순으로 정렬.
    API 비용 없음(yfinance 무료 데이터). 실패한 종목은 조용히 건너뜀.

    거시경제 뉴스는 S&P500(^GSPC), 나스닥(^IXIC), 10년물 금리(^TNX) 관련
    뉴스를 가져와 [MACRO] 태그로 구분 표시한다.
    """
    import yfinance as yf
    items = []

    def _extract(news_list, tag, max_n):
        out = []
        for n in news_list[:max_n]:
            content = n.get("content", n)
            title = content.get("title") or n.get("title")
            ts = content.get("pubDate") or n.get("providerPublishTime")
            if not title:
                continue
            if isinstance(ts, str):
                try:
                    pub_dt = pd.to_datetime(ts)
                except Exception:
                    pub_dt = pd.Timestamp.now()
            elif isinstance(ts, (int, float)):
                pub_dt = pd.to_datetime(ts, unit="s")
            else:
                pub_dt = pd.Timestamp.now()
            out.append({"ticker": tag, "title": title.strip(), "time": pub_dt})
        return out

    for t in tickers_tuple:
        try:
            news = yf.Ticker(t).news or []
            items.extend(_extract(news, t, max_per_ticker))
        except Exception:
            continue

    if include_macro:
        macro_sources = ["^GSPC", "^IXIC", "^TNX"]  # S&P500, 나스닥, 10년물 금리
        for src in macro_sources:
            try:
                news = yf.Ticker(src).news or []
                items.extend(_extract(news, "MACRO", max_macro))
            except Exception:
                continue

    items.sort(key=lambda x: x["time"], reverse=True)
    return items[:18]


def _render_news_feed(holdings: dict):
    """📡 LIVE NEWS FEED — 보유종목 뉴스를 1줄 컴팩트 리스트로 표시."""
    st.markdown(textwrap.dedent("""
    <div style="margin-bottom:8px;">
      <span style="font-size:13px;font-weight:800;color:#e0e0e0;">📡 LIVE NEWS FEED</span>
      <span style="font-size:10px;color:#8b949e;margin-left:6px;">보유종목 + 거시경제</span>
    </div>
    """), unsafe_allow_html=True)

    port_tickers = tuple(t for t in holdings if t != 'CASH')
    if not port_tickers:
        st.caption("보유 종목이 없어 뉴스를 표시할 수 없습니다.")
        return

    try:
        news_items = _fetch_portfolio_news(port_tickers)
    except Exception as e:
        st.caption(f"뉴스 로드 실패: {e}")
        return

    if not news_items:
        st.caption("표시할 뉴스가 없습니다.")
        return

    rows_html = []
    for n in news_items:
        time_ago = _format_time_ago(n["time"])
        is_macro = n["ticker"] == "MACRO"
        tag_color = "#a78bfa" if is_macro else "#f59e0b"  # 거시경제=보라, 종목=주황
        tag_label = "🌐 MACRO" if is_macro else n["ticker"]
        rows_html.append(
            f'<div style="font-size:11px;color:#c9d1d9;padding:4px 0;border-bottom:1px solid #161b22;">'
            f'<span style="color:#58a6ff;font-weight:700;">[📡 LIVE]</span> '
            f'<span style="color:{tag_color};font-weight:700;">[{tag_label}]</span> '
            f'{n["title"]} '
            f'<span style="color:#6b7280;font-size:10px;">· {time_ago}</span>'
            f'</div>'
        )

    feed_html = (
        '<div style="background:#0d1117;border:1px solid #30363d;border-radius:4px;'
        'padding:10px 14px;max-height:280px;overflow-y:auto;">'
        + "".join(rows_html) +
        '</div>'
    )
    st.markdown(feed_html, unsafe_allow_html=True)


def _format_time_ago(ts: pd.Timestamp) -> str:
    try:
        delta = pd.Timestamp.now() - ts.tz_localize(None) if ts.tzinfo else pd.Timestamp.now() - ts
        hours = delta.total_seconds() / 3600
        if hours < 1:
            return f"{int(delta.total_seconds()/60)}분 전"
        elif hours < 24:
            return f"{int(hours)}시간 전"
        else:
            return f"{int(hours/24)}일 전"
    except Exception:
        return ""


def _render_ai_analyst_box(holdings: dict, close_df: pd.DataFrame, port_beta: float,
                            vix_now: float, today_chg_pct: float):
    """
    🤖 AI ANALYST REAL-TIME FEEDBACK
    현재 시장 지표 + 섹터 트리맵 상태 + 포트폴리오 변동성을 종합해
    Claude Haiku로 1~2줄 매매 방향성 피드백을 생성. 결과는 5분 캐싱해
    불필요한 API 재호출을 막는다.
    """
    st.markdown(textwrap.dedent("""
    <div style="margin-bottom:8px;">
      <span style="font-size:13px;font-weight:800;color:#e0e0e0;">🤖 AI ANALYST</span>
      <span style="font-size:10px;color:#8b949e;margin-left:6px;">REAL-TIME FEEDBACK</span>
    </div>
    """), unsafe_allow_html=True)

    if not ANTHROPIC_API_KEY:
        st.markdown(textwrap.dedent("""
        <div style="background:#0d1117;border:1px solid #30363d;border-left:3px solid #8b949e;
                     padding:12px 14px;border-radius:4px;">
          <span style="color:#8b949e;font-size:11px;">⚠️ ANTHROPIC_API_KEY 미설정 — .env 파일을 확인하세요.</span>
        </div>
        """), unsafe_allow_html=True)
        return

    # 캐시 키: 분 단위로 라운딩해 동일 입력이면 재호출 안 함(5분 캐싱 효과)
    cache_bucket = datetime.now().strftime("%Y%m%d%H") + str(datetime.now().minute // 5)
    cache_key = f"_ai_analyst_{cache_bucket}"

    if cache_key not in st.session_state:
        try:
            vix_state = "발작" if vix_now >= 30 else ("주의" if vix_now >= 20 else "정상")
            top_sectors = []
            try:
                etf_chgs = get_sector_etf_changes()
                label_map = {etf: label.split(" (")[0] for label, etf in GICS_SECTOR_ETFS}
                sector_chgs = [(label_map.get(etf, etf), chg) for etf, chg in etf_chgs.items()]
                sector_chgs.sort(key=lambda x: x[1], reverse=True)
                top_sectors = sector_chgs[:2]
            except Exception:
                pass

            sector_summary = ", ".join(f"{s}({c:+.1f}%)" for s, c in top_sectors) if top_sectors else "데이터 없음"

            prompt = f"""다음 데이터를 바탕으로 투자자에게 1~2문장(80자 이내)의 간결한 매매 방향성 피드백을 한국어로 작성해줘.
조언이 아닌 관찰 기반 코멘트 톤으로, 구체적 수치를 인용해서 작성해.

- VIX 지수: {vix_now:.1f} ({vix_state})
- 포트폴리오 베타: {port_beta:.2f}
- 오늘 포트폴리오 변동률: {today_chg_pct:+.2f}%
- 주도 섹터(1일): {sector_summary}

출력은 텍스트 1~2문장만, 따옴표나 마크다운 없이."""

            import anthropic
            client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            msg = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=150,
                messages=[{"role": "user", "content": prompt}],
            )
            feedback_text = "".join(
                b.text for b in msg.content if getattr(b, "type", None) == "text"
            ).strip()
            st.session_state[cache_key] = feedback_text
        except Exception as e:
            st.session_state[cache_key] = f"[AI 분석 일시 오류: {e}]"

    feedback = st.session_state[cache_key]
    fb_color = "#39d353" if today_chg_pct >= 0 else "#ff7b72"

    feedback_html = (
        f'<div style="background:#0d1117;border:1px solid #30363d;border-left:3px solid {fb_color};'
        'padding:14px;border-radius:4px;min-height:90px;display:flex;align-items:center;">'
        f'<span style="color:#c9d1d9;font-size:13px;line-height:1.6;">{feedback}</span>'
        '</div>'
    )
    st.markdown(feedback_html, unsafe_allow_html=True)
    st.caption("💡 Claude Haiku 기반 · 5분마다 갱신 · 투자 조언이 아닌 참고용 코멘트입니다.")



# ── 관심종목 Watchlist 라이브 사이드바 (fragment) ────────────────────────────
@st.fragment(run_every=30)
def _watchlist_live():
    """관심종목 미니 대시보드 — with st.sidebar 컨텍스트 안에서 호출."""
    st.divider()
    st.markdown(
        "<div style='font-size:10px;color:#8b949e;font-weight:700;"
        "letter-spacing:1.5px;margin-bottom:6px;'>⭐ WATCHLIST</div>",
        unsafe_allow_html=True,
    )

    if "watchlist" not in st.session_state:
        st.session_state["watchlist"] = []
    wl: list = st.session_state["watchlist"]

    new_wl = st.text_input(
        "종목 추가", placeholder="티커 입력 후 Enter",
        label_visibility="collapsed", key="wl_add_input",
    ).upper().strip()
    if new_wl and new_wl not in wl:
        wl.append(new_wl)
        st.session_state["watchlist"] = wl
        st.rerun()

    if not wl:
        st.caption("관심 종목이 없습니다.")
        _ts = datetime.now(pytz.timezone("America/New_York")).strftime("%H:%M:%S")
        st.caption(f"🔁 30초갱신 · {_ts} EST")
        return

    # 가격 로드 (관심종목 전용)
    try:
        import yfinance as _yf
        _raw = _yf.download(list(wl), period="5d", progress=False, auto_adjust=True)
        if not _raw.empty:
            _price_df = (
                _raw["Close"].ffill()
                if isinstance(_raw.columns, pd.MultiIndex)
                else _raw[["Close"]].rename(columns={"Close": wl[0]}).ffill()
            )
        else:
            _price_df = pd.DataFrame()
    except Exception:
        _price_df = pd.DataFrame()

    rows_html = ""
    remove_candidates: list = []
    for t in wl:
        if t in _price_df.columns and len(_price_df[t].dropna()) >= 2:
            _s = _price_df[t].dropna()
            cur = float(_s.iloc[-1])
            prv = float(_s.iloc[-2])
            chg = (cur / prv - 1) * 100 if prv else 0.0
            clr = "#39d353" if chg >= 0 else "#ff7b72"
            arrow = "▲" if chg >= 0 else "▼"
            price_str = f"${cur:,.2f}"
            chg_str = f"{arrow} {abs(chg):.2f}%"
        else:
            clr, price_str, chg_str = "#8b949e", "—", "—"
        remove_candidates.append(t)
        rows_html += (
            f'<div style="display:flex;justify-content:space-between;align-items:center;'
            f'padding:5px 0;border-bottom:1px solid #161b22;">'
            f'<span style="color:#e0e0e0;font-size:11px;font-weight:700;">{t}</span>'
            f'<span style="color:#8b949e;font-size:10px;">{price_str}</span>'
            f'<span style="color:{clr};font-size:10px;font-weight:700;">{chg_str}</span>'
            f'</div>'
        )

    st.markdown(
        f'<div style="background:#0d1117;border:1px solid #30363d;'
        f'padding:8px 10px;border-radius:4px;">{rows_html}</div>',
        unsafe_allow_html=True,
    )

    to_remove = st.selectbox(
        "제거", ["—"] + remove_candidates,
        label_visibility="collapsed", key="wl_remove_sel",
    )
    if to_remove != "—" and st.button("✕ 제거", key="wl_remove_btn", use_container_width=True):
        wl.remove(to_remove)
        st.session_state["watchlist"] = wl
        st.rerun()

    _ts = datetime.now(pytz.timezone("America/New_York")).strftime("%H:%M:%S")
    st.caption(f"🔁 30초갱신 · {_ts} EST")


# ── 데일리 브리프 인라인 패널 ────────────────────────────────────────────────
def _render_daily_brief_panel():
    """오른쪽 AI 허브 > 데일리 브리프 탭 내용."""
    md = st.session_state.get("_daily_brief_md")

    if st.button("📊 오늘의 포트폴리오 리포트 생성", type="primary",
                 use_container_width=True, key="btn_gen_brief_inline"):
        h = st.session_state.holdings or {}
        status = st.empty()
        steps = []

        def _log(msg):
            steps.append(msg)
            parts = ""
            for i, s in enumerate(steps):
                clr = "#E5E7EB" if i == len(steps) - 1 else "#4B5563"
                ico = "⟳" if i == len(steps) - 1 else "✓"
                parts += f'<div style="color:{clr};padding:1px 0;">{ico} {s}</div>'
            status.markdown(
                f'<div style="font-size:11px;padding:6px 0;">{parts}</div>',
                unsafe_allow_html=True,
            )

        try:
            from daily_portfolio_report import generate_daily_report
            report_md, _ = generate_daily_report(h, log=_log)
            st.session_state["_daily_brief_md"] = report_md
            status.empty()
            st.rerun()
        except Exception as err:
            status.error(f"생성 오류: {err}")
        return

    if md:
        st.markdown(md)
    else:
        st.caption("버튼을 눌러 전일 포트폴리오 브리프를 생성하세요.")
        st.markdown(
            '<div style="background:#111827;border:1px solid #1F2937;'
            'border-left:3px solid #F97316;border-radius:6px;'
            'padding:14px;margin-top:8px;font-size:12px;color:#6B7280;">'
            '📊 데일리 브리프는 보유 종목 전일 변동 + 뉴스 원인 분석 + 매크로 헤드업을<br>'
            'Claude AI가 월가 인텔리전스 스타일로 1~2페이지로 요약합니다.'
            '</div>',
            unsafe_allow_html=True,
        )


# ── 30초 자동 갱신 LIVE 대시보드 ────────────────────────────────────────────
@st.fragment(run_every=30)
def _live_dashboard():
    """Bloomberg Terminal 스타일 3단 고정형 대시보드 — 30초 자동 갱신."""
    holdings = st.session_state.holdings
    if not holdings:
        return

    with st.spinner(""):
        try:
            close_df = load_market_data(tuple(holdings.keys()))
        except Exception as e:
            st.error(f"yfinance 오류: {e}")
            return

    curr       = close_df.iloc[-1]
    prev_close = close_df.iloc[-2]
    ny_now     = datetime.now(pytz.timezone('America/New_York'))

    stock_tickers = [t for t in holdings if t != 'CASH' and t in close_df.columns]
    cash_val      = holdings.get('CASH', {}).get('q', 0)

    def _p(t):
        return float(curr.get(t, 0)) if t != 'CASH' else 1.0
    def _pp(t):
        return float(prev_close.get(t, _p(t))) if t in prev_close.index else _p(t)
    def _clr(v):
        return '#10B981' if v >= 0 else '#F43F5E'

    total_equity  = sum(_p(t) * holdings[t]['q'] for t in stock_tickers) + cash_val
    prev_equity   = sum(_pp(t) * holdings[t]['q'] for t in stock_tickers) + cash_val
    total_cost    = sum(info['avg'] * info['q'] for t, info in holdings.items() if t != 'CASH') + cash_val
    today_chg_val = total_equity - prev_equity
    today_chg_pct = (today_chg_val / prev_equity * 100) if prev_equity else 0
    total_rtn     = (total_equity / total_cost - 1) * 100 if total_cost else 0

    equity_curve = build_realistic_equity_curve(
        holdings, st.session_state.get("trade_log", []), close_df
    )

    def get_perf(days):
        if len(equity_curve) >= days + 1:
            base = float(equity_curve.iloc[-(days + 1)])
            return (total_equity / base - 1) * 100 if base else 0.0
        return 0.0

    port_beta = calculate_portfolio_beta(holdings, close_df)
    vix_now   = float(curr.get('^VIX', 18.0))
    vix_sig   = _vix_signal(vix_now)

    p_perf     = (equity_curve / equity_curve.iloc[0] - 1) * 100
    bench_perf = None
    alpha_pct  = None
    if '^GSPC' in close_df.columns:
        b_sp = close_df['^GSPC'].reindex(equity_curve.index).ffill()
        bench_perf = (b_sp / b_sp.iloc[0] - 1) * 100
        alpha_info = _compute_alpha_badge(p_perf, bench_perf)
        if alpha_info:
            alpha_pct = alpha_info['alpha']

    # ══ TIER 1: 원본 헤더 바 (단일 행 / 슬래시 구분자 스타일) ════════════════
    beta_clr = '#39d353' if 0.8 <= port_beta <= 1.2 else ('#f39c12' if port_beta < 1.5 else '#ff7b72')
    safe_alpha = alpha_pct if (alpha_pct is not None and not (alpha_pct != alpha_pct) and abs(alpha_pct) < 1e9) else None
    st.markdown(
        f'<div style="display:flex;align-items:flex-start;gap:24px;padding:12px 4px 12px;'
        f'border-bottom:1px solid #30363d;font-family:\'JetBrains Mono\',monospace;">'

        f'<div>'
        f'<div style="font-size:10px;color:#58a6ff;font-weight:700;margin-bottom:3px;">ALPHA TERMINAL</div>'
        f'<div style="font-size:12px;color:#8b949e;">PORTFOLIO VALUE</div>'
        f'<div style="font-size:24px;font-weight:700;color:#00e6ff;line-height:1.2;">${total_equity:,.2f}</div>'
        f'<div style="font-size:11px;color:{_clr(today_chg_val)};">'
        f'{today_chg_val:+,.2f} ({today_chg_pct:+.2f}%) TODAY</div>'
        f'</div>'

        f'<div style="font-size:28px;color:#30363d;margin-top:18px;">/</div>'

        f'<div>'
        f'<div style="color:#8b949e;font-size:10px;font-weight:700;margin-bottom:8px;">CHANGE</div>'
        f'<div style="display:flex;gap:14px;">'
        f'<div style="text-align:center;"><div style="color:#8b949e;font-size:9px;">1D</div>'
        f'<div style="color:{_clr(today_chg_pct)};font-size:14px;font-weight:700;">{today_chg_pct:+.1f}%</div></div>'
        f'<div style="text-align:center;"><div style="color:#8b949e;font-size:9px;">1W</div>'
        f'<div style="color:{_clr(get_perf(5))};font-size:14px;font-weight:700;">{get_perf(5):+.1f}%</div></div>'
        f'<div style="text-align:center;"><div style="color:#8b949e;font-size:9px;">1M</div>'
        f'<div style="color:{_clr(get_perf(21))};font-size:14px;font-weight:700;">{get_perf(21):+.1f}%</div></div>'
        f'</div>'
        f'</div>'

        f'<div style="font-size:28px;color:#30363d;margin-top:18px;">/</div>'

        f'<div>'
        f'<div style="color:#8b949e;font-size:10px;font-weight:700;">TOTAL RETURN</div>'
        f'<div style="color:{_clr(total_rtn)};font-size:20px;font-weight:800;">{total_rtn:+.2f}%</div>'
        f'<div style="color:#8b949e;font-size:12px;">${total_equity - total_cost:+,.2f}</div>'
        f'</div>'

        f'<div style="font-size:28px;color:#30363d;margin-top:18px;">/</div>'

        f'<div>'
        f'<div style="color:#8b949e;font-size:10px;font-weight:700;margin-bottom:8px;">RISK</div>'
        f'<div style="display:flex;gap:16px;">'
        f'<div style="text-align:center;"><div style="color:#8b949e;font-size:9px;">PORTFOLIO β</div>'
        f'<div style="color:{beta_clr};font-size:15px;font-weight:800;">{port_beta:.2f}</div></div>'
        f'<div style="text-align:center;"><div style="color:#8b949e;font-size:9px;">VIX {vix_now:.1f}</div>'
        f'<div style="color:{vix_sig["color"]};font-size:12px;font-weight:800;">{vix_sig["icon"]} {vix_sig["label"]}</div></div>'
        f'</div>'
        f'</div>'

        + (
            f'<div style="font-size:28px;color:#30363d;margin-top:18px;">/</div>'
            f'<div>'
            f'<div style="color:#8b949e;font-size:10px;font-weight:700;">vs S&amp;P 500 α</div>'
            f'<div style="color:{_clr(safe_alpha)};font-size:20px;font-weight:800;">{safe_alpha:+.2f}%p</div>'
            f'<div style="color:#8b949e;font-size:11px;">{"아웃퍼폼 🏆" if safe_alpha > 0 else "언더퍼폼"}</div>'
            f'</div>'
            if safe_alpha is not None else ''
        ) +

        f'<div style="margin-left:auto;text-align:right;padding-top:4px;">'
        f'<div style="color:#8b949e;font-size:11px;">NY {ny_now.strftime("%H:%M")} EST</div>'
        f'<div style="color:#00e676;font-size:12px;font-weight:700;margin-top:4px;">'
        f'<span class="live-dot"></span>LIVE</div>'
        f'</div>'

        f'</div>',
        unsafe_allow_html=True,
    )

    # ══ TIER 1-C: 리본 + 액션 버튼 ════════════════════════════════════════
    _render_ticker_marquee(close_df)
    _ac1, _ac2, _ac_sp = st.columns([0.5, 0.5, 11])
    with _ac1:
        if st.button("🔍", key="btn_search_toggle", help="종목 검색"):
            st.session_state["_search_open"] = not st.session_state.get("_search_open", False)
    with _ac2:
        if st.button("🔄", key="btn_refresh_now", help="즉시 새로고침"):
            st.cache_data.clear()
    if st.session_state.get("_search_open", False):
        with st.expander("🔍 TICKER SEARCH", expanded=True):
            _render_ticker_search(close_df)

    # ══ TIER 2/3: 메인 바디 60:40 ═════════════════════════════════════════
    left_col, right_col = st.columns([6, 4], gap="medium")

    # ── LEFT 60%: 퀀트 데이터 상황실 ──────────────────────────────────────
    with left_col:

        # 에쿼티 커브
        _lh, _lb = st.columns([3, 1])
        with _lh:
            st.markdown('<div style="font-size:9px;color:#6B7280;font-weight:700;'
                        'letter-spacing:1.5px;padding-top:6px;margin-bottom:2px;">📈 EQUITY CURVE</div>',
                        unsafe_allow_html=True)
        with _lb:
            _bopts = ["None", "NASDAQ"]
            _curb  = st.session_state.get("benchmark", "None")
            if _curb not in _bopts:
                _curb = "None"
            st.session_state["benchmark"] = st.selectbox(
                "벤치마크", _bopts, index=_bopts.index(_curb),
                label_visibility="collapsed", key="bench_sel_v2",
            )

        fig_eq = go.Figure()
        fig_eq.add_trace(go.Scatter(x=p_perf.index, y=p_perf,
                                    line=dict(color='#10B981', width=2.5), name="Portfolio"))
        if bench_perf is not None:
            fig_eq.add_trace(go.Scatter(x=bench_perf.index, y=bench_perf,
                                        line=dict(color='#F97316', width=1.5, dash='dot'), name="S&P 500"))
        if st.session_state.get("benchmark") == "NASDAQ" and '^IXIC' in close_df.columns:
            _bnq = close_df['^IXIC'].reindex(equity_curve.index).ffill()
            _nqp = (_bnq / _bnq.iloc[0] - 1) * 100
            fig_eq.add_trace(go.Scatter(x=_nqp.index, y=_nqp,
                                        line=dict(color='#FBBF24', width=1.5, dash='dot'), name="NASDAQ"))
        fig_eq.update_layout(
            xaxis=dict(type="date", gridcolor="#1F2937",
                       rangeselector=dict(
                           buttons=[dict(count=1,label="1M",step="month",stepmode="backward"),
                                    dict(count=3,label="3M",step="month",stepmode="backward"),
                                    dict(count=1,label="1Y",step="year",stepmode="backward"),
                                    dict(step="all",label="ALL")],
                           bgcolor="#111827", activecolor="#F97316", font=dict(size=9, color="#9CA3AF"))),
            yaxis=dict(ticksuffix="%", gridcolor="#1F2937", side="right",
                       tickfont=dict(color="#6B7280", size=9)),
            margin=dict(l=0,r=0,t=4,b=0), height=205,
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            font_color="#9CA3AF", hovermode="x unified",
            legend=dict(orientation="h",yanchor="bottom",y=1.02,xanchor="right",x=1,font=dict(size=9)),
        )
        st.plotly_chart(fig_eq, use_container_width=True)

        # 데이터 탭
        tab_h, tab_s, tab_c = st.tabs(["📋 보유 종목 상세", "🍩 섹터 비중", "📉 상관관계"])

        with tab_h:
            with st.container(height=285):
                _rows = []
                for t, info in holdings.items():
                    _p_ = _p(t); _pp_ = _pp(t) if t != 'CASH' else 1.0
                    _chg = (_p_ / _pp_ - 1) * 100 if _pp_ and t != 'CASH' else 0.0
                    _pnl = (_p_ / info['avg'] - 1) * 100 if info['avg'] > 0 and t != 'CASH' else 0.0
                    _w   = (_p_ * info['q'] / total_equity) * 100 if total_equity else 0
                    _rows.append({
                        "Ticker":  t,
                        "Avg":     float(info['avg']) if t != 'CASH' else np.nan,
                        "Shares":  float(info['q']),
                        "Price":   _p_ if t != 'CASH' else np.nan,
                        "Chg%":    f"{'▲' if _chg>0.05 else '▼' if _chg<-0.05 else '—'} {_chg:+.2f}%" if t != 'CASH' else "—",
                        "P&L%":    round(_pnl, 2),
                        "Value":   round(_p_ * info['q'], 0),
                        "Weight%": round(_w, 1),
                    })
                df_h = pd.DataFrame(_rows)
                edited_df = st.data_editor(
                    df_h, use_container_width=True, hide_index=True, key="holdings_editor",
                    disabled=["Ticker","Price","Chg%","P&L%","Value","Weight%"],
                    column_config={
                        "Avg":     st.column_config.NumberColumn(format="$%.2f", step=0.01),
                        "Shares":  st.column_config.NumberColumn(format="%.2f",  step=1.0),
                        "Price":   st.column_config.NumberColumn(format="$%.2f"),
                        "Value":   st.column_config.NumberColumn(format="$%.0f"),
                        "P&L%":    st.column_config.NumberColumn(format="%.2f%%"),
                        "Weight%": st.column_config.NumberColumn(format="%.1f%%"),
                        "Chg%":    st.column_config.TextColumn(),
                    },
                )
                _chgd = False
                for _i, _r in edited_df.iterrows():
                    _tk = _r["Ticker"]
                    if _tk not in holdings:
                        continue
                    _ns = float(_r["Shares"])
                    if abs(_ns - holdings[_tk]['q']) > 1e-9:
                        holdings[_tk]['q'] = _ns; _chgd = True
                    if _tk != 'CASH':
                        _na = float(_r["Avg"]) if not np.isnan(_r["Avg"]) else holdings[_tk]['avg']
                        if abs(_na - holdings[_tk]['avg']) > 1e-9:
                            holdings[_tk]['avg'] = _na; _chgd = True
                if _chgd:
                    save_data(); st.rerun()

        with tab_s:
            with st.container(height=285):
                _s1, _s2 = st.columns(2)
                with _s1:
                    _sd = [{"Sector": info["sector"], "Val": _p(t) * info["q"]}
                           for t, info in holdings.items() if info["q"] > 0]
                    if _sd:
                        _sdf = pd.DataFrame(_sd).groupby("Sector").sum().reset_index()
                        _fd = px.pie(_sdf, values="Val", names="Sector", hole=0.62,
                                     color_discrete_sequence=["#10B981","#F97316","#3B82F6",
                                                              "#8B5CF6","#F43F5E","#06B6D4","#FBBF24"])
                        _fd.update_traces(textfont_size=9, textfont_color="white")
                        _fd.update_layout(margin=dict(l=0,r=0,t=0,b=0), height=270,
                                          paper_bgcolor="rgba(0,0,0,0)", showlegend=True,
                                          legend=dict(font=dict(size=8,color="#9CA3AF"),
                                                      orientation="v",yanchor="middle",y=0.5,
                                                      xanchor="left",x=1.0))
                        st.plotly_chart(_fd, use_container_width=True)
                with _s2:
                    _render_sector_treemap(holdings)

        with tab_c:
            with st.container(height=285):
                _tm = {"XLK":"Tech","XLF":"Fin","XLE":"Energy","XLY":"Cons","XLV":"Health",
                       "^VIX":"VIX","^TNX":"US10Y","GC=F":"Gold","^GSPC":"S&P","^IXIC":"NQ",
                       "BTC-USD":"BTC","CL=F":"WTI"}
                _vt = [t for t in _tm if t in close_df.columns]
                if len(_vt) > 1:
                    _dn = [_tm[t] for t in _vt]
                    _cr = close_df[_vt].pct_change().corr()
                    _fh = go.Figure(data=go.Heatmap(
                        z=_cr.values, x=_dn, y=_dn,
                        colorscale=[[0,"#F43F5E"],[0.5,"#111827"],[1,"#10B981"]],
                        zmin=-1, zmax=1,
                    ))
                    for _ri, _rv in enumerate(_cr.values):
                        for _ci, _cv in enumerate(_rv):
                            _fh.add_annotation(x=_dn[_ci], y=_dn[_ri], text=f"{_cv:.2f}",
                                               showarrow=False,
                                               font=dict(color="#E5E7EB" if abs(_cv)>0.35 else "#4B5563", size=8))
                    _fh.update_layout(margin=dict(l=0,r=0,t=0,b=0), height=270,
                                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                                      yaxis=dict(autorange="reversed", tickfont=dict(color="#9CA3AF",size=8)),
                                      xaxis=dict(side="top", tickfont=dict(color="#9CA3AF",size=8)),
                                      font=dict(color="#9CA3AF"))
                    st.plotly_chart(_fh, use_container_width=True)

        # 어닝 캘린더 + 매크로
        _ec, _mc = st.columns(2)
        with _ec:
            st.markdown('<div style="font-size:9px;color:#6B7280;font-weight:700;'
                        'letter-spacing:1.5px;margin:8px 0 4px;">📅 EARNINGS CALENDAR</div>',
                        unsafe_allow_html=True)
            _ptks = tuple(t for t in holdings if t != 'CASH')
            _ed   = get_earnings_dividends(_ptks) if _ptks else {}
            st.dataframe(pd.DataFrame([
                {"Ticker": t, "Next Earn": _ed.get(t,{}).get("earn_date","N/A"),
                 "Last Div": _ed.get(t,{}).get("div_date","N/A"),
                 "Div Yield": _ed.get(t,{}).get("div_yield","N/A")}
                for t in _ptks
            ]), use_container_width=True, hide_index=True, height=180)
        with _mc:
            _macro = get_fred_macro()
            _y10, _y2 = _macro["y10"], _macro["y2"]
            st.markdown(f'<div style="font-size:9px;color:#6B7280;font-weight:700;'
                        f'letter-spacing:1.5px;margin:8px 0 4px;">🌍 MACRO ({_macro["source"]})</div>',
                        unsafe_allow_html=True)
            st.dataframe(pd.DataFrame([
                {"항목":"Fed Rate",    "값":f"{_macro['fed_rate']:.2f}%"},
                {"항목":"10Y Yield",   "값":f"{_y10:.2f}%"},
                {"항목":"2Y Yield",    "값":f"{_y2:.2f}%"},
                {"항목":"Spread(10-2)","값":f"{_y10-_y2:+.2f}%p"},
                {"항목":"실업률",       "값":f"{_macro['unemployment']:.1f}%"},
                {"항목":"USD/KRW",     "값":f"{float(curr.get('USDKRW=X',1350)):,.0f}"},
            ]), use_container_width=True, hide_index=True, height=180)

    # ── RIGHT 40%: AI 인텔리전스 허브 ──────────────────────────────────────
    with right_col:
        st.markdown('<div style="font-size:9px;color:#6B7280;font-weight:700;'
                    'letter-spacing:1.5px;padding-top:6px;margin-bottom:2px;">🤖 AI INTELLIGENCE HUB</div>',
                    unsafe_allow_html=True)

        tab_db, tab_ts, tab_ai, tab_news = st.tabs([
            "⚡ 데일리 브리프", "🎯 타점 시그널", "🤖 AI 피드", "📡 뉴스",
        ])
        _PH = 640

        with tab_db:
            with st.container(height=_PH):
                _render_daily_brief_panel()

        with tab_ts:
            with st.container(height=_PH):
                if st.button("⚡ 오늘의 LONG/SHORT 전수 스캔 실행",
                             use_container_width=True, key="btn_timing_scan"):
                    st.session_state["_timing_refresh"] = True
                _render_timing_signals_board(holdings)

        with tab_ai:
            with st.container(height=_PH):
                _render_ai_analyst_box(holdings, close_df, port_beta, vix_now, today_chg_pct)

        with tab_news:
            with st.container(height=_PH):
                _render_news_feed(holdings)

# ── 메인 렌더 ─────────────────────────────────────────────────────────────────
def render():
    # 세션 상태 초기화 (파일에서 로드) — 파일이 바뀌면 자동 재로드
    file_sig = None
    if DB_FILE.exists():
        file_sig = DB_FILE.stat().st_mtime  # 파일 수정시각으로 변경 감지

    if st.session_state.holdings is None or st.session_state.get("_holdings_file_sig") != file_sig:
        saved_h, saved_l = _load_from_file()
        if saved_h:
            st.session_state.holdings = saved_h
            st.session_state.trade_log = saved_l
            st.session_state["_holdings_file_sig"] = file_sig
        elif st.session_state.holdings is None:
            st.session_state.holdings = {
                'CASH': {'q': 10000, 'avg': 1, 'sector': 'Cash', 'div': 'N/A',
                         'prev_eps': 0, 'cur_eps': 0, 'earn_date': 'N/A'},
                'VRT':  {'q': 47,  'avg': 200.06, 'sector': 'AI Infra',
                         'div': 'Jun 15', 'prev_eps': 0.45, 'cur_eps': 0.52, 'earn_date': 'Apr 22'},
                'OXY':  {'q': 268, 'avg': 57.47,  'sector': 'Energy',
                         'div': 'Jun 10', 'prev_eps': 0.63, 'cur_eps': 0.74, 'earn_date': 'May 07'},
                'ANET': {'q': 70,  'avg': 142.52, 'sector': 'AI Infra',
                         'div': 'N/A',    'prev_eps': 1.58, 'cur_eps': 1.82, 'earn_date': 'May 01'},
            }
            st.session_state.trade_log = []

    if "benchmark" not in st.session_state:
        st.session_state.benchmark = "None"

    holdings = st.session_state.holdings


    # ── LIVE 대시보드 (30초 자동 갱신) ──────────────────────────────────────
    _live_dashboard()

    # ── 사이드바: 관심종목 (fragment — sidebar 컨텍스트 안에서 실행) ────────────
    with st.sidebar:
        _watchlist_live()

    # ── 사이드바 포트폴리오 편집 ──────────────────────────────────────────────
    with st.sidebar:
        st.divider()
        st.markdown("<div style='font-size:10px;color:#8b949e;font-weight:700;letter-spacing:1.5px;margin-bottom:8px;'>ASSET MANAGEMENT</div>", unsafe_allow_html=True)
        st.caption("💡 메인 화면 HOLDINGS 표에서 Avg/Shares를 직접 수정할 수 있어요. ADD/SOLD/삭제 및 거래 메모는 아래에서.")

        with st.expander("🛠️ EDIT HOLDINGS"):
            NEW_TICKER_LABEL = "➕ 신규 종목 추가..."
            ticker_options = list(holdings.keys()) + [NEW_TICKER_LABEL]
            t_choice = st.selectbox("Ticker", ticker_options)
            is_new = (t_choice == NEW_TICKER_LABEL)

            if is_new:
                t_edit = st.text_input("새 티커 심볼 (예: NVDA)", placeholder="NVDA").upper().strip()
                sector = st.selectbox(
                    "Sector",
                    ["AI Infra", "Energy", "Tech", "Finance", "Healthcare",
                     "Consumer", "Industrial", "Materials", "Cash", "기타"],
                )
                mode = "ADD"
                st.caption("💡 신규 종목은 첫 매수(ADD)로만 등록됩니다.")
            else:
                t_edit = t_choice
                mode = st.radio("Action", ["ADD", "SOLD", "UPDATE", "DELETE"], horizontal=True)

            if mode != "DELETE":
                cq, cp = st.columns(2)
                default_q = 0.0 if is_new else float(holdings[t_edit]['q'])
                default_p = 0.0 if is_new else float(holdings[t_edit].get('avg', 1.0))
                q_edit  = cq.number_input("Qty",   value=default_q, min_value=0.0)
                p_edit  = cp.number_input("Price", value=default_p, min_value=0.0)
                memo    = st.text_area("Memo", placeholder="매매 사유...")

                btn_label = "신규 종목 추가" if is_new else f"Confirm {mode}"
                if st.button(btn_label, use_container_width=True):
                    if not t_edit:
                        st.warning("티커 심볼을 입력하세요.")
                    elif is_new:
                        if t_edit in holdings:
                            st.warning(f"{t_edit}는 이미 보유 중인 종목입니다.")
                        elif q_edit <= 0:
                            st.warning("수량(Qty)을 입력하세요.")
                        else:
                            holdings[t_edit] = {
                                'q': q_edit, 'avg': p_edit, 'sector': sector,
                                'div': 'N/A', 'prev_eps': 0, 'cur_eps': 0, 'earn_date': 'N/A',
                            }
                            now_dt = datetime.now().strftime('%Y-%m-%d')
                            st.session_state.trade_log.append({
                                'date': now_dt, 'ticker': t_edit, 'type': 'ADD',
                                'q': q_edit, 'p': p_edit, 'memo': memo
                            })
                            save_data()
                            st.rerun()
                    else:
                        now_dt = datetime.now().strftime('%Y-%m-%d')
                        if mode == "UPDATE":
                            holdings[t_edit].update({'q': q_edit, 'avg': p_edit})
                        elif mode == "ADD":
                            old   = holdings[t_edit]
                            new_q = old['q'] + q_edit
                            holdings[t_edit].update({
                                'q':   new_q,
                                'avg': ((old['q']*old['avg']) + (q_edit*p_edit)) / new_q if new_q else p_edit
                            })
                        elif mode == "SOLD":
                            holdings[t_edit]['q'] -= q_edit
                        st.session_state.trade_log.append({
                            'date': now_dt, 'ticker': t_edit, 'type': mode,
                            'q': q_edit, 'p': p_edit, 'memo': memo
                        })
                        save_data()
                        st.rerun()
            elif st.button("Delete Ticker") and t_edit != 'CASH':
                del holdings[t_edit]
                save_data()
                st.rerun()

        # 거래 이력
        st.divider()
        st.markdown("<div style='font-size:10px;color:#8b949e;font-weight:700;letter-spacing:1.5px;'>TRADE HISTORY</div>", unsafe_allow_html=True)
        log = st.session_state.get("trade_log", [])
        if log:
            for i, entry in enumerate(reversed(log[-10:])):
                real_i = len(log) - 1 - i
                clr    = "#00e676" if entry['type'] in ["ADD","UPDATE"] else "#ff7b72"
                memo_html = f'<div style="color:#8b949e;font-size:10px;margin-top:3px;">{entry["memo"]}</div>' if entry.get('memo') else ''
                entry_html = (
                    '<div style="background:#161b22;border:1px solid #30363d;border-radius:2px;'
                    'padding:8px;margin-bottom:4px;font-size:11px;">'
                    f'<span style="color:#8b949e;font-size:10px;">{entry["date"]}</span><br>'
                    f'<b style="color:{clr}">{entry["type"]}</b> | <b>{entry["ticker"]}</b><br>'
                    f'{entry.get("q",0):,.0f}주 @ ${entry.get("p",0):,.2f}'
                    f'{memo_html}'
                    '</div>'
                )
                st.markdown(entry_html, unsafe_allow_html=True)
                if st.button("✕", key=f"del_{real_i}"):
                    log.pop(real_i)
                    save_data()
                    st.rerun()
        else:
            st.info("No records.")


# ── 종목 검색 인터페이스 ──────────────────────────────────────────────────────
def _render_ticker_search(close_df: pd.DataFrame):
    """단독 종목 검색창 — 실시간 차트 + 퀀트 분석 서브 레이아웃."""
    st.markdown(
        '<div style="font-size:10px;color:#8b949e;font-weight:700;letter-spacing:1.5px;margin-bottom:8px;">'
        '🔎 TICKER SEARCH <span style="color:#58a6ff;font-weight:400;">단독 종목 분석</span>'
        '</div>',
        unsafe_allow_html=True,
    )

    search_col, btn_col = st.columns([3, 1])
    with search_col:
        search_ticker = st.text_input(
            "티커 입력",
            value=st.session_state.get("_search_ticker", ""),
            placeholder="예: NVDA, TSLA, ALAB ...",
            label_visibility="collapsed",
            key="ticker_search_input",
        ).upper().strip()
    with btn_col:
        do_search = st.button("🔍 분석", use_container_width=True, key="ticker_search_btn")

    if do_search and search_ticker:
        st.session_state["_search_ticker"] = search_ticker

    active_ticker = st.session_state.get("_search_ticker", "")
    if not active_ticker:
        st.caption("💡 티커를 입력하고 '분석' 버튼을 누르면 실시간 차트와 퀀트 지표가 나타납니다.")
        return

    # 데이터 로드
    with st.spinner(f"{active_ticker} 데이터 로딩 중..."):
        try:
            import yfinance as yf
            tk_data = yf.download(active_ticker, period="1y", progress=False, auto_adjust=True)
            tk_price = tk_data["Close"].squeeze().dropna() if not tk_data.empty else None
            tk_info = yf.Ticker(active_ticker).info
        except Exception as e:
            st.error(f"데이터 로드 실패: {e}")
            return

    if tk_price is None or tk_price.empty:
        st.error(f"'{active_ticker}' 데이터를 찾을 수 없습니다.")
        return

    cur_p = float(tk_price.iloc[-1])
    prev_p = float(tk_price.iloc[-2]) if len(tk_price) > 1 else cur_p
    chg = (cur_p / prev_p - 1) * 100 if prev_p else 0.0
    high52 = float(tk_price.rolling(252).max().iloc[-1])
    low52  = float(tk_price.rolling(252).min().iloc[-1])
    ann_vol = float(tk_price.pct_change().dropna().std() * np.sqrt(252) * 100)
    ma20   = float(tk_price.rolling(20).mean().iloc[-1])
    ma60   = float(tk_price.rolling(60).mean().iloc[-1])

    chg_clr = "#39d353" if chg >= 0 else "#ff7b72"

    # 헤더 메트릭
    st.markdown(f"""
    <div style="background:#0d1117;border:1px solid #30363d;border-left:4px solid #00e6ff;
                 padding:14px 18px;border-radius:4px;margin-bottom:12px;">
      <div style="display:flex;align-items:baseline;gap:16px;flex-wrap:wrap;">
        <span style="font-size:20px;font-weight:800;color:#58a6ff;">{active_ticker}</span>
        <span style="font-size:24px;font-weight:700;color:#00e6ff;">${cur_p:,.2f}</span>
        <span style="color:{chg_clr};font-size:16px;font-weight:700;">
          {'▲' if chg>=0 else '▼'} {abs(chg):.2f}% (1D)
        </span>
        <span style="color:#8b949e;font-size:12px;margin-left:auto;">
          52W H: ${high52:,.2f} · L: ${low52:,.2f}
        </span>
      </div>
    </div>
    """, unsafe_allow_html=True)

    chart_col, stat_col = st.columns([2.5, 1])

    with chart_col:
        # 6개월 주가 차트 (MA20 · MA60 포함)
        fig_s = go.Figure()
        fig_s.add_trace(go.Scatter(
            x=tk_price.index, y=tk_price,
            line=dict(color="#00e6ff", width=2), name=active_ticker,
        ))
        ma20_s = tk_price.rolling(20).mean()
        ma60_s = tk_price.rolling(60).mean()
        fig_s.add_trace(go.Scatter(x=ma20_s.index, y=ma20_s,
                                    line=dict(color="#f59e0b", width=1, dash="dot"), name="MA20"))
        fig_s.add_trace(go.Scatter(x=ma60_s.index, y=ma60_s,
                                    line=dict(color="#9b59b6", width=1, dash="dot"), name="MA60"))
        fig_s.update_layout(
            margin=dict(l=0, r=0, t=10, b=0), height=240,
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="white"), hovermode="x unified",
            xaxis=dict(gridcolor="#21262d"),
            yaxis=dict(gridcolor="#21262d", side="right"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02,
                        xanchor="right", x=1, font=dict(size=10)),
        )
        st.plotly_chart(fig_s, use_container_width=True)

    with stat_col:
        st.markdown('<div style="font-size:10px;color:#8b949e;font-weight:700;letter-spacing:1.5px;margin-bottom:6px;">📐 QUANT</div>', unsafe_allow_html=True)
        quant_rows = [
            ("현재가", f"${cur_p:,.2f}"),
            ("1D 변동", f"{chg:+.2f}%"),
            ("연환산 변동성", f"{ann_vol:.1f}%"),
            ("MA20", f"${ma20:,.2f}"),
            ("MA60", f"${ma60:,.2f}"),
            ("MA20 대비", f"{(cur_p/ma20-1)*100:+.1f}%"),
            ("P/E (Fwd)", str(tk_info.get("forwardPE", "N/A"))[:6]),
            ("시가총액", f"${tk_info.get('marketCap',0)/1e9:.1f}B" if tk_info.get("marketCap") else "N/A"),
            ("배당수익률", f"{tk_info.get('dividendYield',0)*100:.2f}%" if tk_info.get("dividendYield") else "N/A"),
        ]
        rows_html = "".join(
            f'<div style="display:flex;justify-content:space-between;padding:4px 0;'
            f'border-bottom:1px solid #161b22;">'
            f'<span style="color:#8b949e;font-size:10px;">{k}</span>'
            f'<span style="color:#e0e0e0;font-size:11px;font-weight:600;">{v}</span>'
            f'</div>'
            for k, v in quant_rows
        )
        st.markdown(
            f'<div style="background:#0d1117;border:1px solid #30363d;'
            f'padding:10px 12px;border-radius:4px;">{rows_html}</div>',
            unsafe_allow_html=True,
        )

        # 관심종목 추가 버튼
        wl = st.session_state.get("watchlist", [])
        if active_ticker not in wl:
            if st.button(f"★ 관심종목 추가 ({active_ticker})", use_container_width=True, key="add_wl_search"):
                wl.append(active_ticker)
                st.session_state["watchlist"] = wl
                st.rerun()
        else:
            if st.button(f"☆ 관심종목 제거 ({active_ticker})", use_container_width=True, key="rm_wl_search"):
                wl.remove(active_ticker)
                st.session_state["watchlist"] = wl
                st.rerun()


# ── 관심종목 Watchlist 사이드바 가젯 ─────────────────────────────────────────
def _render_watchlist_sidebar(close_df: pd.DataFrame):
    """사이드바에 관심종목 미니 테이블 표시 (현재가 + 1D 등락률)."""
    with st.sidebar:
        st.divider()
        st.markdown(
            "<div style='font-size:10px;color:#8b949e;font-weight:700;letter-spacing:1.5px;margin-bottom:6px;'>"
            "⭐ WATCHLIST</div>",
            unsafe_allow_html=True,
        )

        if "watchlist" not in st.session_state:
            st.session_state["watchlist"] = []

        wl: list[str] = st.session_state["watchlist"]

        # 추가 입력
        new_wl = st.text_input(
            "종목 추가", placeholder="티커 입력 후 Enter",
            label_visibility="collapsed", key="wl_add_input",
        ).upper().strip()
        if new_wl and new_wl not in wl:
            wl.append(new_wl)
            st.session_state["watchlist"] = wl
            st.rerun()

        if not wl:
            st.caption("관심 종목이 없습니다.")
            return

        # 가격 데이터 (close_df에 있는 종목은 바로, 없는 종목은 yfinance 추가 로드)
        missing = [t for t in wl if t not in close_df.columns]
        extra_df = None
        if missing:
            try:
                import yfinance as yf
                extra_raw = yf.download(missing, period="5d", progress=False, auto_adjust=True)
                if not extra_raw.empty:
                    extra_df = extra_raw["Close"].ffill() if isinstance(extra_raw.columns, pd.MultiIndex) \
                               else extra_raw[["Close"]].rename(columns={"Close": missing[0]}).ffill()
            except Exception:
                pass

        rows_html = ""
        remove_candidates = []
        for t in wl:
            # 가격 조회
            series = None
            if t in close_df.columns:
                series = close_df[t].dropna()
            elif extra_df is not None and t in extra_df.columns:
                series = extra_df[t].dropna()

            if series is not None and len(series) >= 2:
                cur = float(series.iloc[-1])
                prv = float(series.iloc[-2])
                chg = (cur / prv - 1) * 100 if prv else 0.0
                clr = "#39d353" if chg >= 0 else "#ff7b72"
                arrow = "▲" if chg >= 0 else "▼"
                price_str = f"${cur:,.2f}"
                chg_str = f"{arrow} {abs(chg):.2f}%"
            else:
                clr, price_str, chg_str = "#8b949e", "—", "—"

            rows_html += (
                f'<div style="display:flex;justify-content:space-between;align-items:center;'
                f'padding:5px 0;border-bottom:1px solid #161b22;">'
                f'<span style="color:#e0e0e0;font-size:11px;font-weight:700;">{t}</span>'
                f'<span style="color:#8b949e;font-size:10px;">{price_str}</span>'
                f'<span style="color:{clr};font-size:10px;font-weight:700;">{chg_str}</span>'
                f'</div>'
            )
            remove_candidates.append(t)

        st.markdown(
            f'<div style="background:#0d1117;border:1px solid #30363d;'
            f'padding:8px 10px;border-radius:4px;">{rows_html}</div>',
            unsafe_allow_html=True,
        )

        # 삭제 UI
        to_remove = st.selectbox(
            "제거", ["—"] + remove_candidates,
            label_visibility="collapsed", key="wl_remove_sel",
        )
        if to_remove != "—" and st.button("✕ 제거", key="wl_remove_btn", use_container_width=True):
            wl.remove(to_remove)
            st.session_state["watchlist"] = wl
            st.rerun()

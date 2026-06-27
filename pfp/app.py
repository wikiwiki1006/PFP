"""
Personal Financial Platform
━━━━━━━━━━━━━━━━━━━━━━━━━━━
알파 터미널 + 거시경제 시나리오 + LENS 레포트 생성기 통합 앱
"""

import sys
import importlib
from pathlib import Path
import streamlit as st

# ── 루트 경로를 sys.path에 추가 (pages/ 없이 직접 import) ────────────────────
ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

st.set_page_config(
    page_title="PERSONAL FINANCIAL PLATFORM",
    page_icon="🔭",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── 다크 터미널 테마 ──────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&display=swap');

html, body, [class*="css"] {
    font-family: 'JetBrains Mono', monospace !important;
    background-color: #05070a;
    color: #e0e0e0;
}
.stApp { background-color: #05070a; }

section[data-testid="stSidebar"] {
    background-color: #0d1117 !important;
    border-right: 1px solid #30363d;
}
section[data-testid="stSidebar"] * { color: #e0e0e0 !important; }

.stButton > button {
    background-color: #161b22;
    border: 1px solid #30363d;
    color: #00e6ff;
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px;
    font-weight: 600;
}
.stButton > button:hover {
    border-color: #00e6ff;
    background-color: #0d2233;
}

div[data-testid="metric-container"] {
    background: #0d1117;
    border: 1px solid #30363d;
    border-left: 3px solid #00e6ff;
    border-radius: 2px;
    padding: 10px;
}

.stTabs [data-baseweb="tab-list"] {
    background: #0d1117;
    border-bottom: 1px solid #30363d;
}
.stTabs [data-baseweb="tab"] {
    color: #8b949e !important;
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px;
}
.stTabs [aria-selected="true"] {
    color: #00e6ff !important;
    border-bottom: 2px solid #00e6ff !important;
}

hr { border-color: #30363d; }

.pipe-badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 2px;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1px;
}
.pipe-ready  { background: #0e3a1e; color: #39d353; border: 1px solid #39d353; }
.pipe-wait   { background: #1a1208; color: #f39c12; border: 1px solid #f39c12; }
.pipe-empty  { background: #1a1117; color: #8b949e; border: 1px solid #30363d; }

.live-dot {
    height: 8px; width: 8px; background-color: #00e676;
    border-radius: 50%; display: inline-block; margin-right: 5px;
    animation: blink 1s infinite;
}
@keyframes blink { 0%{opacity:1} 50%{opacity:.3} 100%{opacity:1} }
</style>
""", unsafe_allow_html=True)


# ── 세션 상태 초기화 ─────────────────────────────────────────────────────────
def _init():
    defaults = {
        "holdings":         None,
        "trade_log":        None,
        "macro_results":    None,
        "macro_event":      "",
        "last_report_path": None,
        "current_page":     "home",
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init()


# ── 사이드바 ─────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("""
    <div style="padding:12px 0 8px">
      <div style="color:#58a6ff;font-size:10px;font-weight:700;letter-spacing:2px;">PERSONAL</div>
      <div style="color:#00e6ff;font-size:18px;font-weight:700;">FINANCIAL PLATFORM</div>
      <div style="color:#8b949e;font-size:10px;margin-top:2px;">Personal Investment OS</div>
    </div>
    """, unsafe_allow_html=True)
    st.divider()

    h_ok  = st.session_state.holdings is not None
    mc_ok = st.session_state.macro_results is not None

    st.markdown(f"""
    <div style="font-size:10px;color:#8b949e;font-weight:700;letter-spacing:1.5px;margin-bottom:8px;">
    PIPELINE STATUS
    </div>
    <div style="margin-bottom:6px;">
      <span class="pipe-badge {'pipe-ready' if h_ok else 'pipe-empty'}">
        {'✓' if h_ok else '○'} PORTFOLIO
      </span>
    </div>
    <div style="margin-bottom:6px;">
      <span class="pipe-badge {'pipe-ready' if mc_ok else 'pipe-empty'}">
        {'✓' if mc_ok else '○'} MACRO
      </span>
    </div>
    <div style="margin-bottom:12px;">
      <span class="pipe-badge {'pipe-ready' if (h_ok and mc_ok) else 'pipe-wait'}">
        {'✓ REPORT READY' if (h_ok and mc_ok) else '⊙ REPORT WAITING'}
      </span>
    </div>
    """, unsafe_allow_html=True)

    st.divider()

    st.markdown("<div style='font-size:10px;color:#8b949e;font-weight:700;letter-spacing:1.5px;margin-bottom:8px;'>NAVIGATION</div>", unsafe_allow_html=True)

    nav_pages = [
        ("🏠", "HOME",           "home"),
        ("📊", "ALPHA TERMINAL", "alpha"),
        ("📋", "DAILY BRIEF",    "daily"),
        ("🌐", "MACRO SCENARIO", "macro"),
        ("🎲", "MONTE CARLO",    "montecarlo"),
        ("⚖️", "OPTIMIZER",      "optimizer"),
        ("⚡", "TIMING ENGINE",  "timing"),
        ("📝", "LENS REPORT",    "report"),
        ("🤖", "AUTO TRADING",   "autotrading"),
    ]
    for icon, label, key in nav_pages:
        active = st.session_state.current_page == key
        if st.button(
            f"{icon}  {label}",
            key=f"nav_{key}",
            use_container_width=True,
            type="primary" if active else "secondary",
        ):
            st.session_state.current_page = key
            st.rerun()

    st.divider()

    if st.button("🔄  전체 캐시 리셋", use_container_width=True):
        st.cache_data.clear()
        st.session_state.holdings = None
        st.session_state.macro_results = None
        st.session_state.last_report_path = None
        st.rerun()

    st.markdown("<div style='font-size:9px;color:#30363d;margin-top:8px;text-align:center;'>v1.0 · Personal Financial Platform</div>", unsafe_allow_html=True)


# ── 페이지 라우터 (pages/ 폴더 없이 루트에서 직접 import) ────────────────────
page = st.session_state.current_page

if page == "home":
    import home as _mod
    importlib.reload(_mod)
    _mod.render()

elif page == "alpha":
    import alpha_terminal as _mod
    importlib.reload(_mod)
    _mod.render()

elif page == "macro":
    import macro_scenario as _mod
    importlib.reload(_mod)
    _mod.render()

elif page == "montecarlo":
    import monte_carlo_page as _mod
    importlib.reload(_mod)
    _mod.render()

elif page == "optimizer":
    import portfolio_optimizer_page as _mod
    importlib.reload(_mod)
    _mod.render()

elif page == "timing":
    import trading_signals_page as _mod
    importlib.reload(_mod)
    _mod.render()

elif page == "daily":
    import daily_report_page as _mod
    importlib.reload(_mod)
    _mod.render()

elif page == "report":
    import lens_report as _mod
    importlib.reload(_mod)
    _mod.render()

elif page == "autotrading":
    import auto_trading as _mod
    importlib.reload(_mod)
    _mod.render()

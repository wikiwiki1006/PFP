import os
from dotenv import load_dotenv

load_dotenv()

# ── Alpaca ─────────────────────────────────────────────────────────────────
ALPACA_API_KEY    = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
ALPACA_PAPER      = os.getenv("ALPACA_PAPER", "true").lower() == "true"

# ── Telegram ────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ── LENS 신호 파일 경로 ────────────────────────────────────────────────────
LENS_SIGNALS_PATH = os.getenv("LENS_SIGNALS_PATH", "signals.json")

# ── 리스크 파라미터 ────────────────────────────────────────────────────────
MAX_POSITION_PCT    = float(os.getenv("MAX_POSITION_PCT",    "0.05"))
STOP_LOSS_PCT       = float(os.getenv("STOP_LOSS_PCT",       "0.03"))
MIN_SIGNAL_STRENGTH = float(os.getenv("MIN_SIGNAL_STRENGTH", "0.70"))
MIN_BUYING_POWER    = float(os.getenv("MIN_BUYING_POWER",    "500.0"))

# ── 스케줄러 ────────────────────────────────────────────────────────────────
POLL_INTERVAL   = int(os.getenv("POLL_INTERVAL",   "60"))   # 신호 파일 폴링 간격 (초)
SIGNAL_INTERVAL = int(os.getenv("SIGNAL_INTERVAL", "30"))   # 자동 신호 생성 간격 (분)

# ── 자동 신호 생성 ────────────────────────────────────────────────────────
PFP_PATH = os.getenv(
    "PFP_PATH",
    r"C:\Users\a6225\OneDrive\바탕 화면\Personal Financial Platform - 복사본"
)
AUTO_WATCHLIST = [
    t.strip()
    for t in os.getenv(
        "AUTO_WATCHLIST",
        "MSFT,NVDA,AAPL,AVGO,JPM,V,GOOGL,META,AMZN,TSLA"
    ).split(",")
    if t.strip()
]

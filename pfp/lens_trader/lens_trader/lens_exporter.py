"""
lens_exporter.py
────────────────
기존 LENS trading signals 엔진 → signals.json 출력 어댑터

사용법:
    LENS의 trading_signals.py 또는 app.py 마지막에 아래 호출 추가:

    from lens_exporter import export_signals
    export_signals(
        signals=generated_signals,
        macro_block=doom_radar_active,
    )
"""

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# LENS Trader가 읽는 경로와 반드시 일치
DEFAULT_OUTPUT = "signals.json"


def export_signals(
    signals: list[dict],
    macro_block: bool,
    output_path: str = DEFAULT_OUTPUT,
) -> None:
    """
    Parameters
    ----------
    signals : list[dict]
        각 원소 예시:
        {
            "ticker":   "NVDA",
            "action":   "BUY",           # "BUY" | "SELL"
            "strategy": "momentum_breakout",
            "strength": 0.87,            # 0.0 ~ 1.0
            "price":    145.20           # 참고가 (실제 주문은 시장가)
        }
    macro_block : bool
        FRED doom radar 활성 여부 — True이면 LENS Trader가 BUY 신호 전부 차단
    output_path : str
        저장 경로 (.env의 LENS_SIGNALS_PATH와 동일하게 설정)
    """
    payload = {
        "timestamp":   datetime.now().isoformat(timespec="seconds"),
        "macro_block": macro_block,
        "signals":     signals,
    }

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    logger.info(
        f"[EXPORTER] signals.json 업데이트: "
        f"{len(signals)}개 신호 | macro_block={macro_block}"
    )


# ── LENS trading_signals.py 연동 예시 ─────────────────────────────────────
#
# 기존 코드 (예시):
#   df = generate_signals(tickers)
#   doom_active = check_doom_radar()
#
# 추가할 코드:
#   from lens_exporter import export_signals
#
#   signals = []
#   for _, row in df[df["action"] != "HOLD"].iterrows():
#       signals.append({
#           "ticker":   row["ticker"],
#           "action":   row["action"],          # BUY / SELL
#           "strategy": row["strategy"],
#           "strength": float(row["strength"]),
#           "price":    float(row["price"]),
#       })
#
#   export_signals(signals=signals, macro_block=doom_active)

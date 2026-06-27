"""
trade_logger.py
───────────────
모든 거래를 trade_log.json에 기록. 왜 샀는지/왜 팔았는지 포함.
"""

import json
import logging
from datetime import datetime
from pathlib import Path

LOG_PATH = Path(__file__).parent / "trade_log.json"

logger = logging.getLogger(__name__)

_REASON_MAP = {
    "평균회귀":   "볼린저밴드 이탈 — 평균 회귀 기대",
    "모멘텀 돌파": "저항선 돌파 + 거래량 급증",
    "bb_mean_reversion":    "볼린저밴드 이탈 — 평균 회귀 기대",
    "momentum_breakout":    "저항선 돌파 + 거래량 급증",
    "k_means_regime_exit":  "K-Means 시장 국면 전환 (Bear 감지)",
}


def _load() -> list:
    if LOG_PATH.exists():
        try:
            return json.loads(LOG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def record(result: dict) -> None:
    """주문 결과 dict를 받아 trade_log.json에 추가."""
    action   = result.get("action", "")
    ticker   = result.get("ticker", "")
    strategy = result.get("strategy", "unknown")
    strength = result.get("strength", 0.0)
    price    = result.get("price", 0.0)
    qty      = result.get("qty", 0)

    if action == "BUY":
        reason = (
            f"[매수 근거] {_REASON_MAP.get(strategy, strategy)} "
            f"| 신호 강도 {strength:.0%} | 진입가 ${price:.2f} "
            f"| 배분 {qty}주 (포트 25%)"
        )
    else:
        reason = (
            f"[매도 근거] {_REASON_MAP.get(strategy, strategy)} "
            f"| 청산가 ${price:.2f} | {qty}주 전량"
        )

    entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "action":    action,
        "ticker":    ticker,
        "qty":       qty,
        "price":     price,
        "strategy":  strategy,
        "strength":  round(strength, 4),
        "reason":    reason,
        "order_id":  result.get("order_id", ""),
        "stop_loss": result.get("stop_loss"),
    }

    log = _load()
    log.append(entry)
    LOG_PATH.write_text(
        json.dumps(log, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info(f"[LOG] 기록 완료: {action} {ticker} — {reason}")


def load_all() -> list:
    """전체 로그 반환 (최신순)."""
    return list(reversed(_load()))

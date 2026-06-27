import json
import logging
from pathlib import Path
import config

logger = logging.getLogger(__name__)


class SignalBridge:
    """
    LENS signals.json → 필터링된 매매 신호 파싱기

    LENS 출력 포맷 (signals.json):
    {
        "timestamp":   "2026-06-22T10:30:00",
        "macro_block": false,           ← FRED doom radar 활성 여부
        "signals": [
            {
                "ticker":   "NVDA",
                "action":   "BUY",      ← "BUY" | "SELL"
                "strategy": "momentum_breakout",
                "strength": 0.87,       ← 0.0 ~ 1.0
                "price":    145.20      ← LENS 계산 시점 참고가 (실제 주문은 시장가)
            }
        ]
    }
    """

    def __init__(self):
        self._path = Path(config.LENS_SIGNALS_PATH)
        self._last_mtime: float = 0.0

    # ── 신규 신호 여부 (mtime 기반) ───────────────────────────────────────
    def has_new_signals(self) -> bool:
        if not self._path.exists():
            logger.debug(f"[BRIDGE] {self._path} 없음")
            return False
        mtime = self._path.stat().st_mtime
        return mtime > self._last_mtime

    # ── 파일 로드 ─────────────────────────────────────────────────────────
    def load(self) -> dict | None:
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._last_mtime = self._path.stat().st_mtime
            cnt = len(data.get("signals", []))
            ts  = data.get("timestamp", "?")
            logger.info(f"[BRIDGE] 로드 완료: {cnt}개 신호 | ts={ts}")
            return data
        except json.JSONDecodeError as e:
            logger.error(f"[BRIDGE] JSON 파싱 오류: {e}")
            return None
        except Exception as e:
            logger.error(f"[BRIDGE] 파일 읽기 실패: {e}")
            return None

    # ── 실행 가능 신호 필터링 ─────────────────────────────────────────────
    def filter_actionable(self, data: dict) -> list[dict]:
        """
        필터 조건:
        1. macro_block=True → BUY 신호 전부 차단 (FRED doom radar 활성 시)
        2. strength < MIN_SIGNAL_STRENGTH → 스킵
        3. action이 BUY/SELL 아닌 경우 → 스킵
        """
        if not data:
            return []

        macro_block = data.get("macro_block", False)
        raw = data.get("signals", [])

        if macro_block:
            logger.warning("[BRIDGE] ⚠️  MACRO BLOCK 활성 — BUY 신호 전부 차단")

        result = []
        for sig in raw:
            action   = sig.get("action", "").upper()
            ticker   = sig.get("ticker", "?")
            strength = sig.get("strength", 0.0)

            if action not in ("BUY", "SELL"):
                logger.debug(f"[BRIDGE] {ticker} 알 수 없는 액션 '{action}', 스킵")
                continue

            if strength < config.MIN_SIGNAL_STRENGTH:
                logger.debug(
                    f"[BRIDGE] {ticker} 강도 미달 "
                    f"({strength:.2f} < {config.MIN_SIGNAL_STRENGTH:.2f}), 스킵"
                )
                continue

            if macro_block and action == "BUY":
                logger.warning(f"[BRIDGE] MACRO BLOCK → {ticker} BUY 차단")
                continue

            result.append({**sig, "action": action})  # action 대문자 정규화

        logger.info(f"[BRIDGE] 필터 결과: {len(result)}/{len(raw)}개 실행 가능")
        return result

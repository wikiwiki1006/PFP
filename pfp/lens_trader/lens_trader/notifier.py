import logging
import requests
import config

logger = logging.getLogger(__name__)


class TelegramNotifier:
    _BASE = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(self):
        self.token   = config.TELEGRAM_TOKEN
        self.chat_id = config.TELEGRAM_CHAT_ID
        self.enabled = bool(self.token and self.chat_id)
        if not self.enabled:
            logger.warning("[NOTIFY] Telegram 미설정 — 알림 비활성화")

    def _send(self, text: str):
        if not self.enabled:
            return
        try:
            url = self._BASE.format(token=self.token)
            resp = requests.post(
                url,
                json={
                    "chat_id":    self.chat_id,
                    "text":       text,
                    "parse_mode": "HTML",
                },
                timeout=8,
            )
            if not resp.ok:
                logger.warning(f"[NOTIFY] Telegram 오류: {resp.status_code} {resp.text[:80]}")
        except Exception as e:
            logger.error(f"[NOTIFY] 전송 실패: {e}")

    def send(self, text: str):
        """범용 메시지 발송 (긴 텍스트는 4096자 단위로 분할)."""
        for i in range(0, max(1, len(text)), 4000):
            self._send(text[i:i + 4000])

    # ── 주문 체결 알림 ────────────────────────────────────────────────────
    def order_executed(self, result: dict):
        action = result["action"]
        emoji  = "🟢" if action == "BUY" else "🔴"
        sl_line = (
            f"스탑로스: <code>${result['stop_loss']:.2f}</code>\n"
            if action == "BUY" else ""
        )
        mode = "PAPER" if config.ALPACA_PAPER else "⚠️ LIVE"
        msg = (
            f"{emoji} <b>LENS AUTO TRADE</b> [{mode}]\n"
            f"━━━━━━━━━━━━━━\n"
            f"종목: <b>{result['ticker']}</b>\n"
            f"액션: <b>{action}</b>\n"
            f"수량: {result['qty']}주\n"
            f"가격: <code>${result['price']:,.2f}</code>\n"
            f"{sl_line}"
            f"전략: {result['strategy']} ({result.get('strength', 0):.0%})\n"
            f"━━━━━━━━━━━━━━\n"
            f"<i>LENS Capital Research</i>"
        )
        self._send(msg)

    # ── 매크로 차단 알림 ──────────────────────────────────────────────────
    def macro_blocked(self, ticker: str):
        self._send(
            f"🚫 <b>MACRO BLOCK</b>\n"
            f"{ticker} BUY 신호 차단\n"
            f"(yield curve 역전 또는 HY spread 과열)"
        )

    # ── 시스템 알림 ───────────────────────────────────────────────────────
    def system_alert(self, text: str):
        self._send(f"⚠️ <b>LENS Trader Alert</b>\n{text}")

    # ── 시작/종료 알림 ────────────────────────────────────────────────────
    def startup(self):
        mode = "PAPER 🟡" if config.ALPACA_PAPER else "LIVE 🔴"
        self._send(
            f"🚀 <b>LENS Trader 가동</b>\n"
            f"Mode: {mode}\n"
            f"Poll: {config.POLL_INTERVAL}s\n"
            f"Min strength: {config.MIN_SIGNAL_STRENGTH:.0%}\n"
            f"Max pos: {config.MAX_POSITION_PCT:.0%} | SL: {config.STOP_LOSS_PCT:.0%}"
        )

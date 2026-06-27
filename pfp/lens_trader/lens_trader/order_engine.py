import logging
from alpaca_client import AlpacaClient
from risk_manager import RiskManager
from trade_logger import record as log_trade
import config

logger = logging.getLogger(__name__)


class OrderEngine:
    def __init__(self):
        self.alpaca = AlpacaClient()
        self.risk   = RiskManager(self.alpaca)

    # ── 메인 진입점 ───────────────────────────────────────────────────────
    def execute(self, signal: dict) -> dict | None:
        ticker   = signal["ticker"]
        action   = signal["action"]
        strategy = signal.get("strategy", "unknown")
        strength = signal.get("strength", 0.0)

        if not self.alpaca.is_market_open():
            logger.info(f"[ENGINE] 시장 마감 중 — {ticker} {action} 스킵")
            return None

        if action == "BUY":
            return self._buy(ticker, strategy, strength)
        elif action == "SELL":
            return self._sell(ticker, strategy, strength)
        else:
            logger.warning(f"[ENGINE] 알 수 없는 액션: {action}")
            return None

    # ── BUY (롱 진입 or 숏 청산) ─────────────────────────────────────────
    def _buy(self, ticker: str, strategy: str, strength: float) -> dict | None:
        pos = self.alpaca.get_open_position(ticker)

        # 숏 포지션 보유 중이면 청산 먼저
        if pos and float(pos.qty) < 0:
            logger.info(f"[ENGINE] {ticker} 숏 포지션 청산 후 롱 진입")
            self.alpaca.close_position(ticker)

        # 이미 롱 보유 중이면 스킵
        if pos and float(pos.qty) > 0:
            logger.info(f"[ENGINE] {ticker} 이미 롱 보유 중 — BUY 스킵")
            return None

        if not self.risk.account_ok():
            return None

        try:
            price = self.alpaca.get_latest_price(ticker)
        except Exception as e:
            logger.error(f"[ENGINE] {ticker} 가격 조회 실패: {e}")
            return None

        qty = self.risk.calc_qty(ticker, price, strength)
        if qty < 1:
            return None

        stop_loss = self.risk.stop_loss_price(price, "long")
        order = self.alpaca.market_buy(ticker, qty)
        if not order:
            return None

        # 스탑로스 주문 즉시 등록 (GTC SELL stop)
        self.alpaca.place_stop_loss(ticker, qty, stop_loss, side="sell")

        result = {
            "action":    "BUY",
            "ticker":    ticker,
            "qty":       qty,
            "price":     price,
            "stop_loss": stop_loss,
            "strategy":  strategy,
            "strength":  strength,
            "order_id":  str(order.id),
        }
        logger.info(
            f"[ENGINE] ✅ BUY(롱) 완료: {ticker} {qty}주 @ ~${price:.2f} "
            f"| SL=${stop_loss:.2f} | {strategy} {strength:.0%}"
        )
        log_trade(result)
        return result

    # ── SELL (숏 진입 or 롱 청산) ────────────────────────────────────────
    def _sell(self, ticker: str, strategy: str, strength: float) -> dict | None:
        pos = self.alpaca.get_open_position(ticker)

        # 롱 포지션 보유 중이면 청산
        if pos and float(pos.qty) > 0:
            qty = int(float(pos.qty))
            try:
                price = self.alpaca.get_latest_price(ticker)
            except Exception as e:
                logger.error(f"[ENGINE] {ticker} 가격 조회 실패: {e}")
                price = float(pos.avg_entry_price)

            order = self.alpaca.close_position(ticker)
            if not order:
                return None

            result = {
                "action":   "SELL(롱청산)",
                "ticker":   ticker,
                "qty":      qty,
                "price":    price,
                "strategy": strategy,
                "strength": strength,
            }
            logger.info(f"[ENGINE] ✅ 롱 청산: {ticker} {qty}주 @ ~${price:.2f}")
            log_trade(result)
            return result

        # 이미 숏이면 스킵
        if pos and float(pos.qty) < 0:
            logger.info(f"[ENGINE] {ticker} 이미 숏 보유 중 — SELL 스킵")
            return None

        # 포지션 없으면 숏 진입
        if not self.risk.account_ok():
            return None

        try:
            price = self.alpaca.get_latest_price(ticker)
        except Exception as e:
            logger.error(f"[ENGINE] {ticker} 가격 조회 실패: {e}")
            return None

        qty = self.risk.calc_qty(ticker, price, strength)
        if qty < 1:
            return None

        stop_loss = self.risk.stop_loss_price(price, "short")
        order = self.alpaca.market_sell(ticker, qty)
        if not order:
            return None

        # 숏 스탑로스 주문 즉시 등록 (GTC BUY stop)
        self.alpaca.place_stop_loss(ticker, qty, stop_loss, side="buy")

        result = {
            "action":    "SELL(숏진입)",
            "ticker":    ticker,
            "qty":       qty,
            "price":     price,
            "stop_loss": stop_loss,
            "strategy":  strategy,
            "strength":  strength,
            "order_id":  str(order.id),
        }
        logger.info(
            f"[ENGINE] ✅ SELL(숏) 완료: {ticker} {qty}주 @ ~${price:.2f} "
            f"| SL=${stop_loss:.2f} | {strategy} {strength:.0%}"
        )
        log_trade(result)
        return result

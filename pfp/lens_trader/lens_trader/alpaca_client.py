import logging
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, StopOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest
import config

logger = logging.getLogger(__name__)


class AlpacaClient:
    def __init__(self):
        self.trading = TradingClient(
            api_key=config.ALPACA_API_KEY,
            secret_key=config.ALPACA_SECRET_KEY,
            paper=config.ALPACA_PAPER
        )
        self.data = StockHistoricalDataClient(
            api_key=config.ALPACA_API_KEY,
            secret_key=config.ALPACA_SECRET_KEY
        )
        mode = "PAPER" if config.ALPACA_PAPER else "LIVE ⚠️"
        logger.info(f"[ALPACA] 초기화 완료 ({mode})")

    # ── 계좌 ──────────────────────────────────────────────────────────────
    def get_account(self):
        return self.trading.get_account()

    def get_portfolio_value(self) -> float:
        return float(self.trading.get_account().portfolio_value)

    def get_buying_power(self) -> float:
        return float(self.trading.get_account().buying_power)

    # ── 포지션 ────────────────────────────────────────────────────────────
    def get_all_positions(self) -> dict:
        return {p.symbol: p for p in self.trading.get_all_positions()}

    def get_open_position(self, symbol: str):
        try:
            return self.trading.get_open_position(symbol)
        except Exception:
            return None

    def has_position(self, symbol: str) -> bool:
        return self.get_open_position(symbol) is not None

    # ── 시장 상태 ─────────────────────────────────────────────────────────
    def is_market_open(self) -> bool:
        return self.trading.get_clock().is_open

    # ── 가격 데이터 ───────────────────────────────────────────────────────
    def get_latest_price(self, symbol: str) -> float:
        """bid/ask 중간값 반환"""
        req = StockLatestQuoteRequest(symbol_or_symbols=symbol)
        quote = self.data.get_stock_latest_quote(req)
        ask = float(quote[symbol].ask_price)
        bid = float(quote[symbol].bid_price)
        return (ask + bid) / 2

    # ── 주문 ──────────────────────────────────────────────────────────────
    def market_buy(self, symbol: str, qty: int):
        req = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY
        )
        order = self.trading.submit_order(req)
        logger.info(f"[ORDER] BUY {qty}주 {symbol} | id={order.id}")
        return order

    def market_sell(self, symbol: str, qty: int):
        req = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY
        )
        order = self.trading.submit_order(req)
        logger.info(f"[ORDER] SELL {qty}주 {symbol} | id={order.id}")
        return order

    def close_position(self, symbol: str):
        """전체 포지션 청산"""
        try:
            result = self.trading.close_position(symbol)
            logger.info(f"[ORDER] CLOSE {symbol} 전량 청산")
            return result
        except Exception as e:
            logger.error(f"[ORDER] CLOSE {symbol} 실패: {e}")
            return None

    def cancel_all_orders(self):
        return self.trading.cancel_orders()

    def place_stop_loss(self, symbol: str, qty: int, stop_price: float, side: str = "sell"):
        """진입 포지션에 대한 GTC 스탑로스 주문 등록."""
        try:
            req = StopOrderRequest(
                symbol=symbol,
                qty=qty,
                side=OrderSide.SELL if side == "sell" else OrderSide.BUY,
                time_in_force=TimeInForce.GTC,
                stop_price=round(stop_price, 2),
            )
            order = self.trading.submit_order(req)
            logger.info(f"[ORDER] STOP-LOSS {symbol} @ ${stop_price:.2f} (GTC) | id={order.id}")
            return order
        except Exception as e:
            logger.error(f"[ORDER] STOP-LOSS 등록 실패 {symbol}: {e}")
            return None

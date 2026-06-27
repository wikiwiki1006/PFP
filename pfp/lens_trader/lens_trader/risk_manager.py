import logging
from alpaca_client import AlpacaClient
import config

logger = logging.getLogger(__name__)

_STRENGTH_TIERS = [
    (0.85, 0.20),
    (0.70, 0.12),
    (0.60, 0.08),
    (0.00, 0.04),
]

_MAX_POSITIONS = 10   # 동시 보유 최대 종목 수
_BP_RESERVE    = 0.10  # 포트폴리오의 10%는 항상 현금 유보


class RiskManager:
    def __init__(self, client: AlpacaClient):
        self.client = client

    def account_ok(self) -> bool:
        acct = self.client.trading.get_account()
        bp   = float(acct.buying_power)
        pv   = float(acct.portfolio_value)
        min_bp = pv * _BP_RESERVE

        pos_count = len(self.client.trading.get_all_positions())

        logger.info(
            f"[RISK] 포트폴리오: ${pv:,.0f} | 매수여력: ${bp:,.0f} "
            f"| 유보금: ${min_bp:,.0f} | 포지션: {pos_count}/{_MAX_POSITIONS}"
        )

        if bp < min_bp:
            logger.warning(
                f"[RISK] 신규 진입 차단 — 매수여력(${bp:,.0f}) < 유보금(${min_bp:,.0f}, 포트 10%)"
            )
            return False

        if pos_count >= _MAX_POSITIONS:
            logger.warning(f"[RISK] 신규 진입 차단 — 포지션 {pos_count}개 한도 초과")
            return False

        return True

    def position_pct(self, strength: float) -> float:
        for threshold, pct in _STRENGTH_TIERS:
            if strength >= threshold:
                return pct
        return 0.04

    def calc_qty(self, symbol: str, price: float, strength: float = 1.0) -> int:
        if price <= 0:
            logger.error(f"[RISK] {symbol} 가격 이상: ${price}")
            return 0

        acct        = self.client.trading.get_account()
        pv          = float(acct.portfolio_value)
        bp          = float(acct.buying_power)
        pct         = self.position_pct(strength)
        dollar_size = pv * pct

        # 매수여력 10% 유보 후 남은 여력으로 제한
        available = bp - pv * _BP_RESERVE
        if available <= 0:
            logger.warning(f"[RISK] {symbol} 유보금 확보 후 여력 없음 — 스킵")
            return 0

        dollar_size = min(dollar_size, available)
        qty = int(dollar_size / price)

        if qty < 1:
            logger.warning(f"[RISK] {symbol} 수량 0 — 가용여력 ${available:,.0f}, 주가 ${price:.2f}")
            return 0

        logger.info(
            f"[RISK] {symbol} 수량: {qty}주 "
            f"(강도 {strength:.0%} → 배분 {pct:.0%} | "
            f"${qty * price:,.0f} / 포트 ${pv:,.0f}의 {qty * price / pv:.1%})"
        )
        return qty

    def stop_loss_price(self, entry_price: float, side: str = "long") -> float:
        if side == "short":
            return round(entry_price * (1 + config.STOP_LOSS_PCT), 2)
        return round(entry_price * (1 - config.STOP_LOSS_PCT), 2)

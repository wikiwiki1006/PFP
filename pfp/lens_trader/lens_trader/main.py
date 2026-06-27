"""
LENS Trader — Alpaca Paper Trading Bot
LENS signals.json → Signal Bridge → Risk Manager → Order Engine → Alpaca

실행:
    python main.py

종료:
    Ctrl+C
"""

import logging
import time
import schedule

from signal_bridge import SignalBridge
from order_engine import OrderEngine
from notifier import TelegramNotifier
from auto_signal import run_auto_scan
import daily_report
import config

# ── 로깅 설정 ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("lens_trader.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("main")


# ── 매매 사이클 ────────────────────────────────────────────────────────────
def trading_cycle(
    bridge: SignalBridge,
    engine: OrderEngine,
    notifier: TelegramNotifier,
):
    """POLL_INTERVAL 마다 호출되는 핵심 루프"""

    # 신호 파일 변경 없으면 스킵
    if not bridge.has_new_signals():
        return

    data = bridge.load()
    if not data:
        return

    signals = bridge.filter_actionable(data)
    if not signals:
        logger.info("[CYCLE] 실행 가능한 신호 없음")
        return

    logger.info(f"[CYCLE] {'=' * 40}")
    logger.info(f"[CYCLE] {len(signals)}개 신호 실행 시작")

    for sig in signals:
        ticker = sig.get("ticker", "?")
        try:
            result = engine.execute(sig)
            if result:
                notifier.order_executed(result)
        except Exception as e:
            msg = f"{ticker} 처리 중 오류: {e}"
            logger.error(f"[CYCLE] {msg}", exc_info=True)
            notifier.system_alert(msg)

    logger.info(f"[CYCLE] {'=' * 40}")


# ── 메인 ───────────────────────────────────────────────────────────────────
def main():
    logger.info("=" * 55)
    logger.info(" LENS TRADER 시작")
    logger.info(f"  Mode     : {'PAPER' if config.ALPACA_PAPER else '⚠️  LIVE'}")
    logger.info(f"  Signals  : {config.LENS_SIGNALS_PATH}")
    logger.info(f"  Poll     : {config.POLL_INTERVAL}s")
    logger.info(f"  Strength : ≥ {config.MIN_SIGNAL_STRENGTH:.0%}")
    logger.info(f"  Max pos  : {config.MAX_POSITION_PCT:.0%} of portfolio")
    logger.info(f"  Stop-loss: {config.STOP_LOSS_PCT:.0%}")
    logger.info("=" * 55)

    bridge   = SignalBridge()
    engine   = OrderEngine()
    notifier = TelegramNotifier()

    notifier.startup()

    # 신호 파일 감지 → 주문 실행 (매 POLL_INTERVAL 초)
    schedule.every(config.POLL_INTERVAL).seconds.do(
        trading_cycle, bridge, engine, notifier
    )

    # 자동 신호 생성 (매 SIGNAL_INTERVAL 분, 장 시간에만 동작)
    schedule.every(config.SIGNAL_INTERVAL).minutes.do(
        run_auto_scan, engine.alpaca
    )

    # 일일 레포트 (매일 09:00 ET = 23:00 KST)
    def _send_daily_report():
        try:
            msg = daily_report.generate(engine.alpaca)
            notifier.send(msg)
            logger.info("[MAIN] 일일 레포트 발송 완료")
        except Exception as e:
            logger.error(f"[MAIN] 일일 레포트 실패: {e}", exc_info=True)

    schedule.every().day.at("09:00").do(_send_daily_report)

    logger.info(f"[MAIN] 스케줄러 등록 완료 (폴링 {config.POLL_INTERVAL}초 / 신호생성 {config.SIGNAL_INTERVAL}분 / 일일레포트 09:00 ET)")
    logger.info("[MAIN] Ctrl+C 로 종료")

    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("[MAIN] 종료 요청 수신 — LENS Trader 정지")
        notifier.system_alert("LENS Trader 정지 (수동 종료)")


if __name__ == "__main__":
    main()

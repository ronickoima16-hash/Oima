"""
main.py — entry point
Wires all modules together and starts the event loop.
"""

import asyncio
import sys
from loguru import logger

from config import log, telegram, binance, trade
from modules.telegram_reader import TelegramReader
from modules.signal_parser import SignalParser
from modules.risk_manager import RiskManager
from modules.trade_validator import TradeValidator
from modules.executor import BinanceExecutor
from modules.position_monitor import PositionMonitor
from modules.notifier import Notifier


def setup_logging():
    logger.remove()
    logger.add(
        sys.stdout,
        level=log.LOG_LEVEL,
        format=(
            "<green>{time:HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | {message}"
        ),
        colorize=True,
    )
    logger.add(
        log.LOG_FILE,
        level="DEBUG",
        rotation=log.ROTATION,
        retention=log.RETENTION,
        compression="zip",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{line} | {message}",
    )


async def main():
    setup_logging()

    logger.info("=" * 55)
    logger.info("  Trading Bot starting")
    logger.info(f"  Trade size  : ${trade.TRADE_SIZE_USDT} USDT")
    logger.info(f"  Max trades  : {trade.MAX_OPEN_TRADES}")
    logger.info(f"  Testnet     : {binance.TESTNET}")
    logger.info(f"  Channels    : {telegram.SIGNAL_CHANNELS}")
    logger.info("=" * 55)

    # ── Initialise modules ────────────────────────
    notifier  = Notifier()
    executor  = BinanceExecutor(notifier)
    monitor   = PositionMonitor(executor, notifier)
    risk_mgr  = RiskManager()
    validator = TradeValidator(executor)
    parser    = SignalParser()

    # ── Wire cross-references ─────────────────────
    executor.attach_monitor(monitor)
    monitor.attach_risk_manager(risk_mgr)

    # ── Connect to Binance ────────────────────────
    await executor.connect()

    # ── Signal pipeline ───────────────────────────
    async def on_signal(raw_text: str, source: str):
        signal = parser.parse(raw_text, source)
        if not signal:
            logger.debug("Not a signal — skipping")
            return

        logger.info(f"Signal: {signal}")

        sized = risk_mgr.apply(signal)
        if not sized:
            return

        ok, reason = await validator.validate(sized)
        if not ok:
            logger.warning(f"Validation failed: {reason}")
            await notifier.send(f"⛔ Skipped `{signal.pair}`: {reason}")
            return

        risk_mgr.increment_open_trades()
        await executor.open_trade(sized)

    # ── Telegram reader ───────────────────────────
    reader = TelegramReader(on_signal_callback=on_signal)

    try:
        async def start_and_attach():
            await reader.start()
            notifier.attach(reader._client)

        await asyncio.gather(
            start_and_attach(),
            monitor.run(),
        )

    except KeyboardInterrupt:
        logger.info("Shutdown requested — stopping gracefully")
    finally:
        await executor.disconnect()
        await reader.stop()
        logger.info("Bot stopped cleanly")


if __name__ == "__main__":
    asyncio.run(main())

"""
Binance Testnet smoke test.
Tests connection, price fetch, and a single market order.
No real money involved.

Run: python tests/test_executor.py
"""

import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from modules.executor import BinanceExecutor
from modules.notifier import Notifier
from modules.risk_manager import RiskManager
from modules.signal import Signal, Side, EntryType
from datetime import datetime, timezone


async def main():
    notifier = Notifier()
    executor = BinanceExecutor(notifier)

    print("Connecting to Binance Testnet...")
    await executor.connect()

    # ── 1. Price fetch ────────────────────────────
    price = await executor.get_price("BTCUSDT")
    print(f"✅  BTCUSDT live price: ${price:,.2f}")

    # ── 2. Fake signal ────────────────────────────
    signal = Signal(
        pair         = "BTCUSDT",
        side         = Side.BUY,
        entry        = 0.0,
        entry_type   = EntryType.MARKET,
        take_profits = [price * 1.01, price * 1.02],
        stop_loss    = price * 0.98,
        leverage     = 5,
        source       = "test",
        received_at  = datetime.now(timezone.utc),
    )

    # ── 3. Size it ────────────────────────────────
    risk_mgr = RiskManager()
    sized    = risk_mgr.apply(signal)
    if not sized:
        print("❌  Risk manager rejected signal")
        return

    print(f"✅  Sized: qty={sized.quantity} | splits={sized.qty_per_tp}")

    # ── 4. Place orders ───────────────────────────
    print("\nPlacing test orders on TESTNET...")
    await executor.open_trade(sized)

    # ── 5. Check positions ────────────────────────
    positions = await executor.get_open_positions()
    print(f"\nOpen positions after test: {len(positions)}")
    for p in positions:
        print(f"  {p['symbol']}: {p['positionAmt']} @ {p['entryPrice']}")

    await executor.disconnect()
    print("\n✅  Executor test complete")


if __name__ == "__main__":
    asyncio.run(main())

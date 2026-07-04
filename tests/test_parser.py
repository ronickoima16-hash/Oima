"""
Quick parser test — no Telegram or Binance connection needed.
Run with:  python -m pytest tests/test_parser.py -v
       or:  python tests/test_parser.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from modules.signal_parser import SignalParser
from modules.signal import Side, EntryType

parser = SignalParser()


# ── Sample messages ───────────────────────────────────────────────────────────

CRYPTO_DEVIL_SAMPLE = """
#ETHUSDT
Direction: Long 📈
Leverage: Cross 10x
Entry: 3730 - 3750
Targets: 3780 / 3850 / 3920 / 4000
Stop loss: 3680
"""

GREEN_ROCK_SAMPLE = """
🟢 LONG BTCUSDT 5x
📊 Entry: 67000
🎯 TP1: 68000
🎯 TP2: 69500
🎯 TP3: 71000
🛑 SL: 65500
"""

SHORT_SAMPLE = """
#SOLUSDT
Direction: Short 📉
Leverage: Cross 20x
Entry: 185 - 188
Targets: 180 / 175 / 168 / 160
Stop loss: 192
"""

MARKET_ENTRY_SAMPLE = """
🔴 SHORT XRPUSDT 10x
📊 Entry: Market
🎯 TP1: 0.52
🎯 TP2: 0.49
🎯 TP3: 0.45
🛑 SL: 0.62
"""

NOT_A_SIGNAL = """
Great call yesterday everyone!
BTC is looking strong 💪
DYOR as always 🚀
"""


# ── Test runner ───────────────────────────────────────────────────────────────

def test(name: str, text: str, expect_none: bool = False):
    print(f"\n{'='*55}")
    print(f"TEST: {name}")
    print(f"{'='*55}")

    signal = parser.parse(text, source="test")

    if expect_none:
        assert signal is None, f"Expected None but got: {signal}"
        print("✅  Correctly returned None (not a signal)")
        return

    assert signal is not None, "Parser returned None — check the raw text"

    print(f"  Pair         : {signal.pair}")
    print(f"  Side         : {signal.side}")
    print(f"  Entry type   : {signal.entry_type}")
    print(f"  Entry        : {signal.entry}")
    print(f"  Entry high   : {signal.entry_high}")
    print(f"  Take profits : {signal.take_profits}")
    print(f"  Stop loss    : {signal.stop_loss}")
    print(f"  Leverage     : {signal.leverage}x")
    print(f"  Fingerprint  : {signal.fingerprint}")
    print(f"\n  Full str: {signal}")
    print("✅  Parsed OK")


if __name__ == "__main__":
    test("Crypto Devil (range entry)", CRYPTO_DEVIL_SAMPLE)
    test("Green Rock (numbered TPs)", GREEN_ROCK_SAMPLE)
    test("Short signal", SHORT_SAMPLE)
    test("Market entry", MARKET_ENTRY_SAMPLE)
    test("Not a signal", NOT_A_SIGNAL, expect_none=True)

    print(f"\n{'='*55}")
    print("All tests passed ✅")

"""
Signal
──────
The canonical data structure that flows through the entire pipeline.
Created by SignalParser, consumed by RiskManager → TradeValidator → Executor.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class Side(str, Enum):
    BUY  = "BUY"   # Long
    SELL = "SELL"  # Short


class EntryType(str, Enum):
    MARKET = "MARKET"   # Enter immediately at market price
    LIMIT  = "LIMIT"    # Enter at a specific price level


@dataclass
class Signal:
    # ── Core fields (always present after parsing) ──────────
    pair:         str          # e.g. "BTCUSDT"
    side:         Side         # BUY or SELL
    entry:        float        # Primary entry price (or 0.0 for market)
    entry_type:   EntryType    # MARKET or LIMIT
    take_profits: list[float]  # [TP1, TP2, TP3, ...] in order
    stop_loss:    float        # SL price

    # ── Optional fields ─────────────────────────────────────
    leverage:     int   = 10        # Default if signal doesn't specify
    entry_high:   float = 0.0       # Upper bound of entry zone (if range given)
    size_usdt:    float = 0.0       # Filled by RiskManager

    # ── Metadata ─────────────────────────────────────────────
    source:       str           = ""
    raw_text:     str           = ""
    received_at:  datetime      = field(
                      default_factory=lambda: datetime.now(timezone.utc)
                  )

    # ── Duplicate detection ──────────────────────────────────
    @property
    def fingerprint(self) -> str:
        """
        Unique key for this signal.
        Two signals with the same pair + side + entry = duplicate.
        """
        return f"{self.pair}:{self.side}:{self.entry}"

    def __str__(self) -> str:
        tps = " | ".join(str(t) for t in self.take_profits)
        return (
            f"{self.side} {self.pair} "
            f"@ {self.entry} ({self.entry_type}) "
            f"| TP: {tps} | SL: {self.stop_loss} "
            f"| {self.leverage}x"
        )

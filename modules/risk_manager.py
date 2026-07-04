"""
Risk Manager
────────────
Responsibilities:
  1. Reject signals that would exceed MAX_OPEN_TRADES
  2. Detect and reject duplicate signals
  3. Calculate position size in base asset units from fixed USDT amount
  4. Split position across TP targets
  5. Apply leverage from signal (or config override)

No orders are placed here — this module only makes decisions
and annotates the Signal object.
"""

import time
from dataclasses import dataclass
from loguru import logger

from config import trade as trade_cfg
from modules.signal import Signal, Side


@dataclass
class SizedSignal:
    """
    Signal enriched with position-sizing data.
    Passed to TradeValidator then Executor.
    """
    signal:          Signal
    size_usdt:       float          # Fixed $20 (or configured amount)
    size_per_tp:     list[float]    # USDT allocated to each TP slice
    quantity:        float          # Base asset quantity for the full position
    qty_per_tp:      list[float]    # Base asset qty per TP slice
    effective_lever: int            # Leverage that will be set on Binance


class RiskManager:

    def __init__(self):
        # fingerprint → unix timestamp of when we first saw it
        self._seen_signals: dict[str, float] = {}

        # Tracks open trade count (incremented by Executor, decremented by Monitor)
        self._open_trade_count: int = 0

    # ──────────────────────────────────────────────
    # Called by Executor / Monitor to keep count accurate
    # ──────────────────────────────────────────────

    def increment_open_trades(self):
        self._open_trade_count += 1
        logger.debug(f"Open trades: {self._open_trade_count}/{trade_cfg.MAX_OPEN_TRADES}")

    def decrement_open_trades(self):
        self._open_trade_count = max(0, self._open_trade_count - 1)
        logger.debug(f"Open trades: {self._open_trade_count}/{trade_cfg.MAX_OPEN_TRADES}")

    @property
    def open_trade_count(self) -> int:
        return self._open_trade_count

    # ──────────────────────────────────────────────
    # Main entry point
    # ──────────────────────────────────────────────

    def apply(self, signal: Signal) -> SizedSignal | None:
        """
        Validate risk rules and return a SizedSignal, or None to skip.
        """

        # ── 1. Max open trades ─────────────────────
        if self._open_trade_count >= trade_cfg.MAX_OPEN_TRADES:
            logger.warning(
                f"Max open trades reached ({trade_cfg.MAX_OPEN_TRADES}) "
                f"— skipping {signal.pair}"
            )
            return None

        # ── 2. Duplicate detection ─────────────────
        fp  = signal.fingerprint
        now = time.time()

        if fp in self._seen_signals:
            age = now - self._seen_signals[fp]
            if age < trade_cfg.DUPLICATE_WINDOW_SECONDS:
                logger.warning(
                    f"Duplicate signal for {signal.pair} "
                    f"(seen {age:.0f}s ago) — skipping"
                )
                return None
            else:
                logger.debug(f"Same signal seen again after {age:.0f}s — treating as fresh")

        self._seen_signals[fp] = now
        self._prune_seen()

        # ── 3. Leverage ────────────────────────────
        lever = (
            trade_cfg.DEFAULT_LEVERAGE
            if trade_cfg.DEFAULT_LEVERAGE is not None
            else signal.leverage
        )
        lever = max(1, min(lever, 125))

        # ── 4. Position sizing ─────────────────────
        size_usdt = trade_cfg.TRADE_SIZE_USDT
        notional  = size_usdt * lever

        entry_price = signal.entry if signal.entry > 0 else 1.0
        quantity    = round(notional / entry_price, 8)

        # ── 5. TP slicing ──────────────────────────
        n_tps      = len(signal.take_profits)
        splits     = self._get_splits(n_tps)
        size_per_tp = [round(size_usdt * s, 4) for s in splits]
        qty_per_tp  = [round(quantity   * s, 8) for s in splits]

        sized = SizedSignal(
            signal          = signal,
            size_usdt       = size_usdt,
            size_per_tp     = size_per_tp,
            quantity        = quantity,
            qty_per_tp      = qty_per_tp,
            effective_lever = lever,
        )

        logger.info(
            f"Risk OK — {signal.pair} {signal.side} | "
            f"${size_usdt} USDT | {lever}x | qty={quantity} | "
            f"splits={splits}"
        )
        return sized

    # ──────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────

    def _get_splits(self, n_tps: int) -> list[float]:
        """
        Return per-TP weight fractions that sum to 1.0.
        Uses config splits if enough are defined, otherwise splits evenly.
        """
        configured = trade_cfg.TP_SPLIT

        if n_tps == 0:
            return []

        if n_tps <= len(configured):
            raw = configured[:n_tps]
        else:
            raw = [1.0 / n_tps] * n_tps

        total = sum(raw)
        return [round(w / total, 6) for w in raw]

    def _prune_seen(self):
        """Remove fingerprints older than the duplicate window to save memory."""
        cutoff = time.time() - trade_cfg.DUPLICATE_WINDOW_SECONDS
        self._seen_signals = {
            fp: ts for fp, ts in self._seen_signals.items() if ts > cutoff
        }

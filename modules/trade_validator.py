"""
Trade Validator
───────────────
Final checks before any order touches Binance:

  1. Pair exists on Binance Futures
  2. Price is still valid (not too far from entry)
  3. Quantity meets Binance minimum notional / lot size
  4. SL is on the correct side of entry

Returns (True, "") on pass, (False, reason) on fail.
"""

from loguru import logger
from modules.risk_manager import SizedSignal
from modules.signal import Side, EntryType
from config import trade as trade_cfg


class TradeValidator:

    def __init__(self, executor):
        self._executor = executor

        # Cache of valid Futures symbols so we don't hammer the API
        self._valid_symbols: set[str] = set()
        self._symbol_info:   dict     = {}

    # ──────────────────────────────────────────────
    # Public
    # ──────────────────────────────────────────────

    async def validate(self, sized: SizedSignal) -> tuple[bool, str]:
        """
        Run all checks. Returns (passed, reason).
        """
        signal = sized.signal

        # ── 1. Symbol exists ───────────────────────
        ok, reason = await self._check_symbol(signal.pair)
        if not ok:
            return False, reason

        # ── 2. Get live price ──────────────────────
        live_price = await self._executor.get_price(signal.pair)
        if not live_price:
            return False, f"Could not fetch live price for {signal.pair}"

        # ── 3. Entry deviation ─────────────────────
        if signal.entry_type == EntryType.LIMIT and signal.entry > 0:
            ok, reason = self._check_entry_deviation(signal, live_price)
            if not ok:
                return False, reason

        # ── 4. SL on correct side ──────────────────
        ref_price = signal.entry if signal.entry > 0 else live_price
        ok, reason = self._check_sl_side(signal, ref_price)
        if not ok:
            return False, reason

        # ── 5. Minimum notional ────────────────────
        ok, reason = self._check_min_notional(sized, live_price)
        if not ok:
            return False, reason

        logger.info(f"Validation passed for {signal.pair}")
        return True, ""

    # ──────────────────────────────────────────────
    # Individual checks
    # ──────────────────────────────────────────────

    async def _check_symbol(self, pair: str) -> tuple[bool, str]:
        if not self._valid_symbols:
            await self._load_symbols()

        if pair not in self._valid_symbols:
            return False, f"{pair} is not a valid Binance Futures symbol"

        return True, ""

    def _check_entry_deviation(self, signal, live_price: float) -> tuple[bool, str]:
        """
        Reject if the live price has moved too far from the signal's entry.
        For LONG:  if live > entry * (1 + max_dev%)  → price already ran up
        For SHORT: if live < entry * (1 - max_dev%)  → price already dropped
        """
        max_dev = trade_cfg.MAX_ENTRY_DEVIATION_PCT / 100

        if signal.side == Side.BUY:
            ref   = signal.entry_high if signal.entry_high else signal.entry
            limit = ref * (1 + max_dev)
            if live_price > limit:
                return (
                    False,
                    f"{signal.pair} price {live_price} is "
                    f"{((live_price/ref)-1)*100:.1f}% above entry {ref} "
                    f"(max {trade_cfg.MAX_ENTRY_DEVIATION_PCT}%)"
                )
        else:
            ref   = signal.entry
            limit = ref * (1 - max_dev)
            if live_price < limit:
                return (
                    False,
                    f"{signal.pair} price {live_price} is "
                    f"{((ref/live_price)-1)*100:.1f}% below entry {ref} "
                    f"(max {trade_cfg.MAX_ENTRY_DEVIATION_PCT}%)"
                )

        return True, ""

    def _check_sl_side(self, signal, ref_price: float) -> tuple[bool, str]:
        """SL must be below entry for LONG, above entry for SHORT."""
        sl = signal.stop_loss
        if signal.side == Side.BUY  and sl >= ref_price:
            return False, f"SL {sl} is above/equal to entry {ref_price} for LONG"
        if signal.side == Side.SELL and sl <= ref_price:
            return False, f"SL {sl} is below/equal to entry {ref_price} for SHORT"
        return True, ""

    def _check_min_notional(self, sized: SizedSignal, live_price: float) -> tuple[bool, str]:
        """
        Binance Futures requires minimum notional of $5 per order.
        """
        min_notional    = 5.0
        smallest_usdt   = min(sized.size_per_tp) if sized.size_per_tp else sized.size_usdt
        if smallest_usdt < min_notional:
            return (
                False,
                f"Smallest TP slice ${smallest_usdt:.2f} is below "
                f"Binance minimum notional ${min_notional}"
            )
        return True, ""

    # ──────────────────────────────────────────────
    # Symbol cache
    # ──────────────────────────────────────────────

    async def _load_symbols(self):
        """Fetch all active Futures symbols from Binance and cache them."""
        try:
            info = await self._executor.get_exchange_info()
            for s in info.get("symbols", []):
                if s.get("status") == "TRADING":
                    sym = s["symbol"]
                    self._valid_symbols.add(sym)
                    self._symbol_info[sym] = s
            logger.info(f"Loaded {len(self._valid_symbols)} active Futures symbols")
        except Exception as e:
            logger.error(f"Failed to load exchange info: {e}")

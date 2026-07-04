"""
Position Monitor
────────────────
Runs as a background asyncio task alongside the Telegram reader.

Responsibilities:
  1. Poll open positions on Binance every N seconds
  2. Detect when a TP level has been hit (position size reduced)
  3. Detect when an SL has been hit (position fully closed)
  4. Move SL to breakeven after TP1 is hit (configurable)
  5. Cancel orphaned TP/SL orders when a position closes
  6. Notify you on every state change
  7. Keep RiskManager open-trade count accurate
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from loguru import logger

from modules.executor import BinanceExecutor
from modules.notifier import Notifier
from modules.signal import Side


# ── Per-trade state ────────────────────────────────────────────────────────────

@dataclass
class TradeState:
    pair:           str
    side:           Side
    entry_price:    float
    initial_qty:    float
    remaining_qty:  float
    tp_prices:      list[float]
    sl_price:       float
    tp_hit:         int      = 0
    opened_at:      datetime = field(
                        default_factory=lambda: datetime.now(timezone.utc)
                    )
    sl_moved_to_be: bool     = False

    entry_order_id: int      = 0
    tp_order_ids:   list[int] = field(default_factory=list)
    sl_order_id:    int      = 0

    @property
    def is_long(self) -> bool:
        return self.side == Side.BUY

    @property
    def next_tp(self) -> float | None:
        idx = self.tp_hit
        if idx < len(self.tp_prices):
            return self.tp_prices[idx]
        return None

    @property
    def all_tps_hit(self) -> bool:
        return self.tp_hit >= len(self.tp_prices)


# ── Config ─────────────────────────────────────────────────────────────────────

POLL_INTERVAL      = 10       # Seconds between Binance polls
MOVE_SL_TO_BREAKEVEN = True   # Move SL to entry after TP1 hits
QTY_TOLERANCE      = 0.0001   # Floating point noise threshold


# ── Monitor ────────────────────────────────────────────────────────────────────

class PositionMonitor:

    def __init__(self, executor: BinanceExecutor, notifier: Notifier):
        self._executor  = executor
        self._notifier  = notifier
        self._trades:   dict[str, TradeState] = {}
        self._risk_mgr  = None

    def attach_risk_manager(self, risk_mgr):
        self._risk_mgr = risk_mgr

    # ──────────────────────────────────────────────
    # Called by Executor when a trade opens
    # ──────────────────────────────────────────────

    def register_trade(
        self,
        pair:           str,
        side:           Side,
        entry_price:    float,
        quantity:       float,
        tp_prices:      list[float],
        sl_price:       float,
        tp_order_ids:   list[int],
        sl_order_id:    int,
    ):
        state = TradeState(
            pair           = pair,
            side           = side,
            entry_price    = entry_price,
            initial_qty    = quantity,
            remaining_qty  = quantity,
            tp_prices      = sorted(tp_prices),
            sl_price       = sl_price,
            tp_order_ids   = tp_order_ids,
            sl_order_id    = sl_order_id,
        )
        self._trades[pair] = state
        logger.info(f"Monitor: registered {pair} | TPs={tp_prices} | SL={sl_price}")

    # ──────────────────────────────────────────────
    # Main loop
    # ──────────────────────────────────────────────

    async def run(self):
        logger.info("Position monitor started")

        while True:
            try:
                if self._trades:
                    await self._poll()
            except Exception as e:
                logger.exception(f"Monitor poll error: {e}")
                await self._notifier.send_error("PositionMonitor", str(e))

            await asyncio.sleep(POLL_INTERVAL)

    # ──────────────────────────────────────────────
    # Poll logic
    # ──────────────────────────────────────────────

    async def _poll(self):
        live_positions = await self._executor.get_open_positions()
        live_map: dict[str, dict] = {p["symbol"]: p for p in live_positions}

        for pair, state in list(self._trades.items()):
            live = live_map.get(pair)

            if live is None:
                await self._handle_fully_closed(pair, state, reason="unknown")
                continue

            live_qty = abs(float(live.get("positionAmt", 0)))

            if live_qty < QTY_TOLERANCE:
                await self._handle_fully_closed(pair, state, reason="unknown")
                continue

            qty_diff = state.remaining_qty - live_qty
            if qty_diff > QTY_TOLERANCE:
                await self._handle_partial_close(pair, state, live_qty, live)

            live_price = float(live.get("markPrice", 0))
            if live_price and self._sl_breached(state, live_price):
                logger.warning(
                    f"[{pair}] Mark price {live_price} has breached SL "
                    f"{state.sl_price} — waiting for Binance to fill SL order"
                )

    # ──────────────────────────────────────────────
    # Event handlers
    # ──────────────────────────────────────────────

    async def _handle_partial_close(
        self, pair: str, state: TradeState, live_qty: float, live_pos: dict
    ):
        """Called when the position size has decreased — a TP was hit."""
        tp_num    = state.tp_hit + 1
        tp_price  = state.next_tp or 0.0
        qty_closed = state.remaining_qty - live_qty
        pnl       = self._calc_pnl(state, tp_price, qty_closed)

        logger.success(
            f"[{pair}] TP{tp_num} HIT @ {tp_price} | "
            f"closed {qty_closed:.6f} | PnL ≈ ${pnl:+.2f}"
        )

        state.tp_hit       += 1
        state.remaining_qty = live_qty

        await self._notifier.send(
            f"🎯 TP{tp_num} HIT — `{pair}`\n"
            f"Price : `{tp_price}`\n"
            f"PnL   : `${pnl:+.2f} USDT`\n"
            f"Remaining position: `{live_qty}`"
        )

        if tp_num == 1 and MOVE_SL_TO_BREAKEVEN and not state.sl_moved_to_be:
            await self._move_sl_to_breakeven(pair, state)

        if state.all_tps_hit:
            logger.success(f"[{pair}] All TPs hit — trade complete")
            await self._close_out(pair, state, reason="All TPs hit")

    async def _handle_fully_closed(self, pair: str, state: TradeState, reason: str):
        """Called when the position no longer exists on Binance."""
        if state.all_tps_hit:
            reason = "All TPs hit"
        elif state.tp_hit > 0:
            reason = f"Closed after TP{state.tp_hit} (SL or manual)"
        else:
            reason = "SL hit or manually closed"

        if state.tp_hit > 0:
            pnl = self._calc_pnl(
                state,
                state.tp_prices[state.tp_hit - 1],
                state.initial_qty - state.remaining_qty,
            )
        else:
            pnl = self._calc_pnl(state, state.sl_price, state.initial_qty)

        logger.info(f"[{pair}] Trade closed: {reason} | PnL ≈ ${pnl:+.2f}")
        await self._notifier.send_trade_closed(pair, reason, pnl)
        await self._close_out(pair, state, reason)

    async def _close_out(self, pair: str, state: TradeState, reason: str):
        """Clean up after a trade ends."""
        await self._cancel_open_orders(pair, state)

        del self._trades[pair]

        if self._risk_mgr:
            self._risk_mgr.decrement_open_trades()

        logger.info(f"[{pair}] Removed from monitor. Reason: {reason}")

    # ──────────────────────────────────────────────
    # SL management
    # ──────────────────────────────────────────────

    async def _move_sl_to_breakeven(self, pair: str, state: TradeState):
        logger.info(f"[{pair}] Moving SL to breakeven @ {state.entry_price}")

        try:
            if state.sl_order_id:
                await self._executor._client.futures_cancel_order(
                    symbol=pair, orderId=state.sl_order_id
                )
                logger.debug(f"[{pair}] Old SL order {state.sl_order_id} cancelled")

            close_side   = "SELL" if state.is_long else "BUY"
            new_sl_price = await self._executor._round_price(pair, state.entry_price)

            new_sl = await self._executor._client.futures_create_order(
                symbol        = pair,
                side          = close_side,
                type          = "STOP_MARKET",
                stopPrice     = new_sl_price,
                closePosition = True,
                reduceOnly    = True,
                timeInForce   = "GTC",
            )

            state.sl_order_id    = new_sl.get("orderId", 0)
            state.sl_price       = state.entry_price
            state.sl_moved_to_be = True

            logger.success(f"[{pair}] SL moved to breakeven @ {state.entry_price}")
            await self._notifier.send(
                f"🔒 SL moved to breakeven — `{pair}` @ `{state.entry_price}`"
            )

        except Exception as e:
            logger.error(f"[{pair}] Failed to move SL to breakeven: {e}")
            await self._notifier.send_error(f"Move SL ({pair})", str(e))

    # ──────────────────────────────────────────────
    # Order cleanup
    # ──────────────────────────────────────────────

    async def _cancel_open_orders(self, pair: str, state: TradeState):
        order_ids = [*state.tp_order_ids, state.sl_order_id]
        order_ids = [oid for oid in order_ids if oid]

        for order_id in order_ids:
            try:
                await self._executor._client.futures_cancel_order(
                    symbol=pair, orderId=order_id
                )
                logger.debug(f"[{pair}] Cancelled order {order_id}")
            except Exception as e:
                logger.debug(f"[{pair}] Could not cancel order {order_id}: {e}")

    # ──────────────────────────────────────────────
    # Calculations
    # ──────────────────────────────────────────────

    @staticmethod
    def _calc_pnl(state: TradeState, close_price: float, qty: float) -> float:
        if state.is_long:
            return round((close_price - state.entry_price) * qty, 4)
        else:
            return round((state.entry_price - close_price) * qty, 4)

    @staticmethod
    def _sl_breached(state: TradeState, live_price: float) -> bool:
        if state.is_long:
            return live_price < state.sl_price
        else:
            return live_price > state.sl_price

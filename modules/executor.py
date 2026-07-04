"""
Binance Futures Executor
────────────────────────
Places and manages orders on Binance USD-M Futures.

Order flow for each signal:
  1. Set leverage for the symbol
  2. Place entry order (LIMIT or MARKET)
  3. Place all TP orders as TAKE_PROFIT_MARKET (reduce-only)
  4. Place SL order as STOP_MARKET (reduce-only)

Each order is logged. Failures are caught per-order so a bad TP
order doesn't prevent the SL from being placed.
"""

import asyncio
from loguru import logger
from binance import AsyncClient
from binance.exceptions import BinanceAPIException

from config import binance as bnb_cfg, trade as trade_cfg
from modules.signal import Side, EntryType
from modules.risk_manager import SizedSignal


class BinanceExecutor:

    def __init__(self, notifier, monitor=None):
        self._notifier = notifier
        self._monitor  = monitor
        self._client: AsyncClient | None = None
        self._orders: dict[str, list[dict]] = {}

    def attach_monitor(self, monitor):
        """Called from main.py after both objects are created."""
        self._monitor = monitor

    # ──────────────────────────────────────────────
    # Lifecycle
    # ──────────────────────────────────────────────

    async def connect(self):
        """Create the async Binance client."""
        self._client = await AsyncClient.create(
            api_key    = bnb_cfg.API_KEY,
            api_secret = bnb_cfg.API_SECRET,
            testnet    = bnb_cfg.TESTNET,
        )
        await self._client.futures_ping()
        mode = "TESTNET" if bnb_cfg.TESTNET else "LIVE"
        logger.success(f"Binance Futures connected ({mode})")

    async def disconnect(self):
        if self._client:
            await self._client.close_connection()
            logger.info("Binance client disconnected")

    # ──────────────────────────────────────────────
    # Market data helpers (used by TradeValidator)
    # ──────────────────────────────────────────────

    async def get_price(self, pair: str) -> float | None:
        try:
            ticker = await self._client.futures_symbol_ticker(symbol=pair)
            return float(ticker["price"])
        except Exception as e:
            logger.error(f"Failed to get price for {pair}: {e}")
            return None

    async def get_exchange_info(self) -> dict:
        return await self._client.futures_exchange_info()

    async def get_open_positions(self) -> list[dict]:
        """Return positions with non-zero size."""
        positions = await self._client.futures_position_information()
        return [p for p in positions if float(p.get("positionAmt", 0)) != 0]

    # ──────────────────────────────────────────────
    # Core: open a trade
    # ──────────────────────────────────────────────

    async def open_trade(self, sized: SizedSignal):
        """
        Full order sequence for one signal:
          entry order → TP orders → SL order
        """
        signal     = sized.signal
        pair       = signal.pair
        side       = signal.side.value
        close_side = "SELL" if side == "BUY" else "BUY"

        logger.info(f"Opening trade: {signal}")

        try:
            # ── Step 1: Set leverage ───────────────
            await self._set_leverage(pair, sized.effective_lever)

            # ── Step 2: Quantity ───────────────────
            if signal.entry_type == EntryType.MARKET:
                live_price = await self.get_price(pair)
                if not live_price:
                    raise RuntimeError(f"Cannot get live price for {pair}")
                notional = trade_cfg.TRADE_SIZE_USDT * sized.effective_lever
                quantity = notional / live_price
            else:
                quantity = sized.quantity

            quantity   = await self._round_quantity(pair, quantity)
            qty_per_tp = await self._split_quantities(pair, quantity, sized.qty_per_tp)

            # ── Step 3: Entry order ────────────────
            entry_order = await self._place_entry(pair, side, quantity, signal)
            if not entry_order:
                await self._notifier.send_error("Executor", f"Entry order failed for {pair}")
                return

            logger.success(
                f"Entry order placed | {pair} | id={entry_order.get('orderId')}"
            )

            # ── Step 4: TP orders ──────────────────
            tp_orders = []
            for i, (tp_price, qty) in enumerate(
                zip(signal.take_profits, qty_per_tp), start=1
            ):
                tp_order = await self._place_tp(pair, close_side, qty, tp_price, i)
                if tp_order:
                    tp_orders.append(tp_order)

            # ── Step 5: SL order ───────────────────
            sl_order = await self._place_sl(pair, close_side, quantity, signal.stop_loss)

            # ── Step 6: Record & notify ────────────
            self._orders[pair] = {
                "entry": entry_order,
                "tps":   tp_orders,
                "sl":    sl_order,
                "sized": sized,
            }

            await self._notifier.send_trade_opened(signal)

            # ── Step 7: Register with monitor ──────
            if self._monitor and entry_order:
                tp_ids = [o.get("orderId", 0) for o in tp_orders if o]
                sl_id  = sl_order.get("orderId", 0) if sl_order else 0

                fill_price = float(
                    entry_order.get("avgPrice") or
                    entry_order.get("price")    or
                    signal.entry or 0
                )

                self._monitor.register_trade(
                    pair         = pair,
                    side         = signal.side,
                    entry_price  = fill_price,
                    quantity     = quantity,
                    tp_prices    = signal.take_profits,
                    sl_price     = signal.stop_loss,
                    tp_order_ids = tp_ids,
                    sl_order_id  = sl_id,
                )

        except BinanceAPIException as e:
            logger.error(f"Binance API error for {pair}: {e.status_code} — {e.message}")
            await self._notifier.send_error("Executor", f"{pair}: {e.message}")
        except Exception as e:
            logger.exception(f"Unexpected error opening trade for {pair}: {e}")
            await self._notifier.send_error("Executor", str(e))

    # ──────────────────────────────────────────────
    # Order placers
    # ──────────────────────────────────────────────

    async def _set_leverage(self, pair: str, leverage: int):
        try:
            await self._client.futures_change_leverage(symbol=pair, leverage=leverage)
            logger.debug(f"Leverage set: {pair} → {leverage}x")
        except BinanceAPIException as e:
            if e.code == -4028:
                logger.debug(f"Leverage already {leverage}x for {pair}")
            else:
                raise

    async def _place_entry(self, pair: str, side: str, quantity: float, signal) -> dict | None:
        try:
            if signal.entry_type == EntryType.MARKET:
                order = await self._client.futures_create_order(
                    symbol   = pair,
                    side     = side,
                    type     = "MARKET",
                    quantity = quantity,
                )
            else:
                price = await self._round_price(pair, signal.entry)
                order = await self._client.futures_create_order(
                    symbol      = pair,
                    side        = side,
                    type        = "LIMIT",
                    quantity    = quantity,
                    price       = price,
                    timeInForce = "GTC",
                )
            self._log_order("ENTRY", order)
            return order
        except BinanceAPIException as e:
            logger.error(f"Entry order failed: {e.message}")
            return None

    async def _place_tp(
        self, pair: str, side: str, quantity: float, tp_price: float, tp_num: int
    ) -> dict | None:
        try:
            price = await self._round_price(pair, tp_price)
            order = await self._client.futures_create_order(
                symbol        = pair,
                side          = side,
                type          = "TAKE_PROFIT_MARKET",
                stopPrice     = price,
                closePosition = False,
                quantity      = quantity,
                reduceOnly    = True,
                timeInForce   = "GTC",
            )
            self._log_order(f"TP{tp_num}", order)
            return order
        except BinanceAPIException as e:
            logger.error(f"TP{tp_num} order failed for {pair}: {e.message}")
            return None

    async def _place_sl(
        self, pair: str, side: str, quantity: float, sl_price: float
    ) -> dict | None:
        try:
            price = await self._round_price(pair, sl_price)
            order = await self._client.futures_create_order(
                symbol        = pair,
                side          = side,
                type          = "STOP_MARKET",
                stopPrice     = price,
                closePosition = True,
                reduceOnly    = True,
                timeInForce   = "GTC",
            )
            self._log_order("SL", order)
            return order
        except BinanceAPIException as e:
            logger.error(f"SL order failed for {pair}: {e.message}")
            return None

    # ──────────────────────────────────────────────
    # Precision helpers
    # ──────────────────────────────────────────────

    async def _round_quantity(self, pair: str, qty: float) -> float:
        try:
            info = await self._client.futures_exchange_info()
            for sym in info["symbols"]:
                if sym["symbol"] == pair:
                    for f in sym["filters"]:
                        if f["filterType"] == "LOT_SIZE":
                            step = float(f["stepSize"])
                            return self._floor_to_step(qty, step)
        except Exception:
            pass
        return round(qty, 3)

    async def _round_price(self, pair: str, price: float) -> float:
        try:
            info = await self._client.futures_exchange_info()
            for sym in info["symbols"]:
                if sym["symbol"] == pair:
                    for f in sym["filters"]:
                        if f["filterType"] == "PRICE_FILTER":
                            tick = float(f["tickSize"])
                            return self._floor_to_step(price, tick)
        except Exception:
            pass
        return round(price, 2)

    async def _split_quantities(
        self, pair: str, total_qty: float, raw_splits: list[float]
    ) -> list[float]:
        result = []
        for q in raw_splits:
            rounded = await self._round_quantity(pair, q)
            result.append(rounded)
        return result

    @staticmethod
    def _floor_to_step(value: float, step: float) -> float:
        """Floor a value to the nearest step increment."""
        if step <= 0:
            return value
        precision = len(str(step).rstrip("0").split(".")[-1])
        floored   = (value // step) * step
        return round(floored, precision)

    # ──────────────────────────────────────────────
    # Logging
    # ──────────────────────────────────────────────

    @staticmethod
    def _log_order(label: str, order: dict):
        logger.info(
            f"  [{label}] orderId={order.get('orderId')} | "
            f"status={order.get('status')} | "
            f"qty={order.get('origQty')} | "
            f"price={order.get('price') or order.get('stopPrice', 'MARKET')}"
        )

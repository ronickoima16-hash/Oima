"""
Notifier
────────
Sends status messages back to your personal Telegram chat.
Uses the same Telethon session as the reader — no extra bot needed.
"""

from loguru import logger
from telethon import TelegramClient
from config import telegram as tg_cfg


class Notifier:
    """
    Thin wrapper around Telethon's send_message.
    Call `await notifier.send("text")` from anywhere.
    """

    def __init__(self):
        self._client: TelegramClient | None = None

    def attach(self, client: TelegramClient):
        """Called by TelegramReader once the client is authenticated."""
        self._client = client

    async def send(self, text: str, parse_mode: str = "markdown"):
        if not self._client:
            logger.warning(f"Notifier has no client — cannot send: {text}")
            return

        if not tg_cfg.NOTIFY_CHAT_ID:
            logger.warning("NOTIFY_CHAT_ID not set — skipping notification")
            return

        try:
            await self._client.send_message(
                tg_cfg.NOTIFY_CHAT_ID,
                text,
                parse_mode=parse_mode,
            )
        except Exception as exc:
            logger.error(f"Failed to send notification: {exc}")

    async def send_trade_opened(self, signal):
        direction = "🟢 LONG" if signal.side == "BUY" else "🔴 SHORT"
        targets = "\n".join(
            f"  TP{i+1}: {tp}" for i, tp in enumerate(signal.take_profits)
        )
        msg = (
            f"{direction} `{signal.pair}`\n"
            f"Entry : `{signal.entry}`\n"
            f"{targets}\n"
            f"SL    : `{signal.stop_loss}`\n"
            f"Lever : `{signal.leverage}x`\n"
            f"Size  : `${signal.size_usdt} USDT`"
        )
        await self.send(msg)

    async def send_trade_closed(self, pair: str, reason: str, pnl: float):
        emoji = "✅" if pnl >= 0 else "❌"
        await self.send(
            f"{emoji} `{pair}` closed — {reason}\n"
            f"PnL: `{pnl:+.2f} USDT`"
        )

    async def send_error(self, context: str, error: str):
        await self.send(f"⚠️ Error in {context}:\n`{error}`")

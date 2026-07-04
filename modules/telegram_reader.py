"""
Telegram Reader
───────────────
Uses Telethon (user-account client) to listen to signal channels.
Feeds raw message text to the signal pipeline via on_signal_callback.

Key behaviours:
  • Resolves channel usernames and numeric IDs at startup
  • Ignores edited messages (configurable)
  • Ignores messages from channels not in the whitelist
  • Auto-reconnects on disconnect
  • Logs every received message to file for debugging
"""

import asyncio
from datetime import datetime, timezone

from telethon import TelegramClient, events
from telethon.errors import (
    SessionPasswordNeededError,
    FloodWaitError,
    ChannelPrivateError,
)
from loguru import logger

from config import telegram as tg_cfg, trade as trade_cfg


class TelegramReader:
    def __init__(self, on_signal_callback):
        """
        on_signal_callback: async fn(raw_text: str, source: str)
        """
        self._callback = on_signal_callback
        self._client: TelegramClient | None = None

        # Maps resolved entity ID → friendly name
        self._watched: dict[int, str] = {}

    # ──────────────────────────────────────────────
    # Public interface
    # ──────────────────────────────────────────────

    async def start(self):
        """Connect, authenticate, resolve channels, then listen."""
        self._client = TelegramClient(
            tg_cfg.SESSION_NAME,
            tg_cfg.API_ID,
            tg_cfg.API_HASH,
        )

        await self._connect()
        await self._resolve_channels()
        self._register_handlers()

        logger.info("Telegram reader is live — waiting for signals...")
        await self._client.run_until_disconnected()

    async def stop(self):
        if self._client and self._client.is_connected():
            await self._client.disconnect()
            logger.info("Telegram client disconnected")

    # ──────────────────────────────────────────────
    # Connection & authentication
    # ──────────────────────────────────────────────

    async def _connect(self):
        """Connect and handle first-time login (OTP + 2FA if needed)."""
        logger.info("Connecting to Telegram...")
        await self._client.connect()

        if not await self._client.is_user_authorized():
            logger.info("First-time login — sending code to your phone/app")
            await self._client.send_code_request(tg_cfg.PHONE)

            code = input("Enter the Telegram login code you received: ").strip()

            try:
                await self._client.sign_in(tg_cfg.PHONE, code)

            except SessionPasswordNeededError:
                password = input("2FA password: ").strip()
                await self._client.sign_in(password=password)

        me = await self._client.get_me()
        logger.success(f"Logged in as: {me.first_name} (@{me.username})")

    # ──────────────────────────────────────────────
    # Channel resolution
    # ──────────────────────────────────────────────

    async def _resolve_channels(self):
        """
        Turn usernames / numeric IDs from config into Telegram entity IDs.
        Stores them in self._watched for fast lookup when messages arrive.
        """
        logger.info("Resolving signal channels...")

        for raw in tg_cfg.SIGNAL_CHANNELS:
            try:
                if raw.lstrip("-").isdigit():
                    entity = await self._client.get_entity(int(raw))
                else:
                    entity = await self._client.get_entity(raw)

                name = getattr(entity, "title", None) or getattr(entity, "username", raw)
                self._watched[entity.id] = name
                logger.success(f"  ✓ Watching [{name}] (id={entity.id})")

            except ChannelPrivateError:
                logger.error(
                    f"  ✗ Cannot access [{raw}] — make sure your account "
                    "is a member of this private channel"
                )
            except Exception as exc:
                logger.error(f"  ✗ Failed to resolve [{raw}]: {exc}")

        if not self._watched:
            raise RuntimeError(
                "No channels could be resolved. "
                "Check SIGNAL_CHANNELS in your .env file."
            )

    # ──────────────────────────────────────────────
    # Event handlers
    # ──────────────────────────────────────────────

    def _register_handlers(self):
        """Attach Telethon event handlers to the client."""

        @self._client.on(events.NewMessage(chats=list(self._watched.keys())))
        async def on_new_message(event):
            await self._handle_message(event, edited=False)

        if not trade_cfg.IGNORE_EDITED_MESSAGES:
            @self._client.on(events.MessageEdited(chats=list(self._watched.keys())))
            async def on_edited_message(event):
                await self._handle_message(event, edited=True)
        else:
            logger.debug("Edited messages will be ignored (IGNORE_EDITED_MESSAGES=True)")

    async def _handle_message(self, event, edited: bool):
        """
        Called for every new (or edited) message from a watched channel.
        Filters noise, then forwards clean text to the pipeline.
        """
        chat_id = event.chat_id
        if chat_id not in self._watched:
            return

        source = self._watched[chat_id]
        msg    = event.message

        text = (msg.text or "").strip()
        if not text:
            logger.debug(f"[{source}] Empty message — skipping")
            return

        if msg.fwd_from:
            logger.debug(f"[{source}] Forwarded message — skipping")
            return

        tag       = "EDITED" if edited else "NEW"
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        logger.info(f"[{source}] {tag} message at {timestamp}")
        logger.debug(f"[{source}] Content:\n{'─'*40}\n{text}\n{'─'*40}")

        try:
            await self._callback(text, source)
        except FloodWaitError as e:
            logger.warning(f"Telegram flood wait — sleeping {e.seconds}s")
            await asyncio.sleep(e.seconds)
        except Exception as exc:
            logger.exception(f"Error in signal pipeline for message from [{source}]: {exc}")

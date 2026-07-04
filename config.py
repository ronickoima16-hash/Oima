"""
Central configuration — all tunable settings live here.
Loaded once at startup; imported by every module.
"""

import os
from dotenv import load_dotenv

load_dotenv()


class TelegramConfig:
    API_ID: int = int(os.getenv("TELEGRAM_API_ID", "0"))
    API_HASH: str = os.getenv("TELEGRAM_API_HASH", "")
    PHONE: str = os.getenv("TELEGRAM_PHONE", "")
    SESSION_NAME: str = "trading_bot_session"

    # Channels to listen to (usernames or numeric IDs)
    SIGNAL_CHANNELS: list[str] = [
        ch.strip()
        for ch in os.getenv("SIGNAL_CHANNELS", "").split(",")
        if ch.strip()
    ]

    # Where to send YOUR notifications
    NOTIFY_CHAT_ID: int = int(os.getenv("NOTIFY_CHAT_ID", "0"))


class BinanceConfig:
    API_KEY: str = os.getenv("BINANCE_API_KEY", "")
    API_SECRET: str = os.getenv("BINANCE_API_SECRET", "")
    TESTNET: bool = os.getenv("BINANCE_TESTNET", "true").lower() == "true"

    # Futures base URLs
    BASE_URL: str = (
        "https://testnet.binancefuture.com"
        if os.getenv("BINANCE_TESTNET", "true").lower() == "true"
        else "https://fapi.binance.com"
    )


class TradeConfig:
    # Fixed USDT per trade
    TRADE_SIZE_USDT: float = 20.0

    # Maximum simultaneous open trades
    MAX_OPEN_TRADES: int = 3

    # Leverage: None means read from signal; set an int to override
    DEFAULT_LEVERAGE: int | None = None

    # Take-profit: use all targets from signal
    USE_ALL_TP_TARGETS: bool = True

    # How to split position across TP targets (must sum to 1.0)
    TP_SPLIT: list[float] = [0.5, 0.3, 0.2]  # 50% at TP1, 30% at TP2, 20% at TP3

    # Stop-loss from signal
    USE_SIGNAL_SL: bool = True

    # Skip duplicate signals within this window (seconds)
    DUPLICATE_WINDOW_SECONDS: int = 300

    # Skip signal if current price is this % away from entry
    MAX_ENTRY_DEVIATION_PCT: float = 2.0

    # Ignore edited Telegram messages
    IGNORE_EDITED_MESSAGES: bool = True


class LogConfig:
    LOG_DIR: str = "logs"
    LOG_FILE: str = "logs/trading_bot.log"
    LOG_LEVEL: str = "DEBUG"
    ROTATION: str = "10 MB"
    RETENTION: str = "14 days"


# ── Convenience instances ──────────────────────────────────
telegram = TelegramConfig()
binance  = BinanceConfig()
trade    = TradeConfig()
log      = LogConfig()

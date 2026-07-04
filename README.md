# Telegram → Binance Futures Trading Bot

Automatically copies trade signals from Telegram channels to Binance USD-M Futures.

## Architecture

```
Telegram
     │
     ▼
Signal Parser
     │
     ▼
Risk Manager
     │
     ▼
Trade Validator
     │
     ▼
Binance Futures Executor
     │
     ▼
Position Monitor
     │
     ▼
Telegram Notifications
```

## Features

- **$10 USDT fixed size** per trade (configurable)
- **Max 3 open trades** simultaneously (configurable)
- **Leverage** read from signal or overridden in config
- **All TP targets** used — position split across TP1/TP2/TP3 (50/30/20%)
- **Stop-loss** from signal, auto-moves to breakeven after TP1
- **Duplicate signal detection** — ignores repeats within 5 minutes
- **Entry deviation check** — skips stale signals (price moved >2% from entry)
- **Supports private channels** via Telethon user account
- **Auto-reconnects** to Telegram on disconnect
- **Structured logs** with rotation — every event recorded

### Signal formats supported

**Crypto Devil format:**
```
#ETHUSDT
Direction: Long 📈
Leverage: Cross 10x
Entry: 3730 - 3750
Targets: 3780 / 3850 / 3920 / 4000
Stop loss: 3680
```

**Green Rock format:**
```
🟢 LONG BTCUSDT 5x
📊 Entry: 67000
🎯 TP1: 68000
🎯 TP2: 69500
🎯 TP3: 71000
🛑 SL: 65500
```

## Project Structure

```
trading-bot/
├── .env                    # Your secrets (never commit)
├── .env.example            # Template
├── requirements.txt
├── config.py               # All settings
├── main.py                 # Entry point
├── modules/
│   ├── signal.py           # Signal dataclass
│   ├── signal_parser.py    # Raw text → Signal
│   ├── risk_manager.py     # Sizing, duplicates, trade count
│   ├── trade_validator.py  # Symbol check, price check, SL side
│   ├── executor.py         # Binance order placement
│   ├── position_monitor.py # TP/SL tracking, breakeven SL
│   ├── telegram_reader.py  # Reads signal channels
│   └── notifier.py         # Sends updates to your Telegram
├── tests/
│   ├── test_parser.py      # Parser unit tests (no API needed)
│   └── test_executor.py    # Binance testnet smoke test
├── deploy/
│   └── bot.service         # systemd service file
└── logs/
```

## Setup

### 1. Clone and install

```bash
git clone https://github.com/YOUR_USERNAME/trading-bot.git
cd trading-bot
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Get Telegram API credentials

Go to [my.telegram.org/apps](https://my.telegram.org/apps), sign in, create an app.
You'll receive an `api_id` and `api_hash`.

### 3. Find your channel IDs

Forward any message from the private channel to [@userinfobot](https://t.me/userinfobot).
It will return the numeric channel ID (e.g. `-1001234567890`).

### 4. Get your Telegram user ID

Message [@userinfobot](https://t.me/userinfobot) directly — it shows your numeric user ID.

### 5. Set up Binance Testnet keys

Register at [testnet.binancefuture.com](https://testnet.binancefuture.com) and generate API keys.

### 6. Configure

```bash
cp .env.example .env
# Edit .env with your credentials
```

```bash
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=abcdef1234567890abcdef1234567890
TELEGRAM_PHONE=+1234567890
NOTIFY_CHAT_ID=987654321
BINANCE_API_KEY=your_testnet_key
BINANCE_API_SECRET=your_testnet_secret
BINANCE_TESTNET=true
SIGNAL_CHANNELS=CryptoDevilVip,-1001234567890
```

## Running

```bash
# Test the signal parser (no API needed)
python tests/test_parser.py

# Test Binance connection (testnet)
python tests/test_executor.py

# Run the bot
python main.py
```

First run will prompt for your Telegram OTP. After that, the session is saved locally.

## Deployment (Linux VPS)

```bash
# Copy the service file
sudo cp deploy/bot.service /etc/systemd/system/trading-bot.service

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable trading-bot
sudo systemctl start trading-bot

# Check status
sudo systemctl status trading-bot

# Watch live logs
sudo journalctl -u trading-bot -f
```

## Pre-live checklist

```
TESTNET PHASE
─────────────
□ python tests/test_parser.py         — all formats parse correctly
□ python tests/test_executor.py       — orders appear in testnet dashboard
□ Run bot live on testnet for 24h     — at least 3 signals processed
□ Confirm TP orders fill at target price
□ Confirm SL order fills at SL price
□ Confirm SL moves to breakeven after TP1
□ Confirm Telegram notifications arrive
□ Confirm duplicate signals are rejected
□ Confirm max-trades limit blocks a 4th trade
□ Check logs/ — entries present for every event

GOING LIVE
──────────
□ Set BINANCE_TESTNET=false in .env
□ Add real Binance Futures API keys
□ Confirm API key has Futures trading enabled (not spot)
□ Confirm API key IP whitelist includes your server IP
□ API key: read + trade only (NEVER withdrawal permission)
□ Open 1 trade manually, watch it end-to-end
□ Only then allow all 3 slots
```

## Notifications

```
Trade opens:
  🟢 LONG  ETHUSDT
  Entry : 3740.0
  TP1   : 3780.0  |  TP2: 3850.0  |  TP3: 3920.0
  SL    : 3680.0
  Lever : 10x  |  Size: $20 USDT

TP hit:
  🎯 TP1 HIT — ETHUSDT
  Price: 3780.0  |  PnL: +$3.20 USDT

Breakeven SL:
  🔒 SL moved to breakeven — ETHUSDT @ 3740.0

Trade closed:
  ✅ ETHUSDT closed — All TPs hit
  PnL: +$9.40 USDT

Signal skipped:
  ⛔ Skipped BTCUSDT: price 68200 is 2.4% above entry
```

## Configuration reference

All settings are in `config.py`:

| Setting | Default | Description |
|---|---|---|
| `TRADE_SIZE_USDT` | `10.0` | Fixed USDT per trade |
| `MAX_OPEN_TRADES` | `3` | Max simultaneous positions |
| `DEFAULT_LEVERAGE` | `None` | `None` = read from signal |
| `TP_SPLIT` | `[0.5, 0.3, 0.2]` | Position % closed at each TP |
| `DUPLICATE_WINDOW_SECONDS` | `300` | Ignore re-sent signals within this window |
| `MAX_ENTRY_DEVIATION_PCT` | `2.0` | Skip if price moved this % from entry |
| `IGNORE_EDITED_MESSAGES` | `True` | Don't act on edited Telegram messages |

## Disclaimer

This bot trades real money. Always test on testnet first. Use at your own risk.
Never give API keys withdrawal permission. The authors accept no liability for financial losses.

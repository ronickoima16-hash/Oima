"""
Signal Parser
─────────────
Converts raw Telegram message text into a Signal object.

Supports the two channel formats you shared:

  FORMAT A — Crypto Devil
  ───────────────────────
  #ETHUSDT
  Direction: Long 📈
  Leverage: Cross 10x
  Entry: 3730 - 3750
  Targets: 3780 / 3850 / 3920 / 4000
  Stop loss: 3680

  FORMAT B — Green Rock
  ──────────────────────
  🟢 LONG BTCUSDT 5x
  📊 Entry: 67000
  🎯 TP1: 68000
  🎯 TP2: 69500
  🎯 TP3: 71000
  🛑 SL: 65500

The parser is regex-based and deliberately lenient — it can handle
emoji, extra whitespace, and minor formatting differences between
messages. Add new patterns at the bottom of each section as you
encounter new formats.
"""

import re
from loguru import logger
from modules.signal import Signal, Side, EntryType


# ── Normalization helpers ──────────────────────────────────────────────────────

def _clean(text: str) -> str:
    """Strip emoji, extra spaces, normalize line endings."""
    text = re.sub(
        r"[\U00010000-\U0010FFFF"
        r"\U0001F300-\U0001F9FF"
        r"\u2600-\u26FF"
        r"\u2700-\u27BF]",
        " ", text, flags=re.UNICODE
    )
    text = re.sub(r"[ \t]+", " ", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.strip()


def _parse_number(raw: str) -> float:
    """
    Turn a string like '3,750.50' or '3 750' or '3750' into 3750.50.
    Returns 0.0 if it cannot be parsed.
    """
    if not raw:
        return 0.0
    cleaned = re.sub(r"[^\d.]", "", raw.replace(",", "."))
    parts = cleaned.split(".")
    if len(parts) > 2:
        cleaned = "".join(parts[:-1]) + "." + parts[-1]
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _normalize_pair(raw: str) -> str:
    """
    Ensure the pair ends with USDT.
    '#ETHUSDT' → 'ETHUSDT', 'ETH/USDT' → 'ETHUSDT', 'ETH' → 'ETHUSDT'
    """
    pair = raw.upper().lstrip("#").strip()
    pair = pair.replace("/", "").replace("-", "")
    if not pair.endswith("USDT"):
        pair += "USDT"
    return pair


def _parse_side(raw: str) -> Side | None:
    raw = raw.upper()
    if any(w in raw for w in ("LONG", "BUY")):
        return Side.BUY
    if any(w in raw for w in ("SHORT", "SELL")):
        return Side.SELL
    return None


def _parse_leverage(raw: str) -> int:
    """Extract integer leverage from strings like '10x', 'Cross 10x', '10X'."""
    m = re.search(r"(\d+)\s*[xX]", raw)
    return int(m.group(1)) if m else 10


# ── Pattern library ────────────────────────────────────────────────────────────

_PATTERNS = {

    # ── Pair ──────────────────────────────────────────────────
    "pair": [
        re.compile(r"^#?(?P<pair>[A-Z]{2,10}USDT)\b", re.IGNORECASE | re.MULTILINE),
        re.compile(r"(?:LONG|SHORT|BUY|SELL)\s+(?P<pair>[A-Z]{2,10}USDT)", re.IGNORECASE),
        re.compile(r"pair\s*[:\-]\s*#?(?P<pair>[A-Z]{2,10}USDT)", re.IGNORECASE),
    ],

    # ── Side ──────────────────────────────────────────────────
    "side": [
        re.compile(r"direction\s*[:\-]\s*(?P<side>long|short|buy|sell)", re.IGNORECASE),
        re.compile(r"\b(?P<side>long|short|buy|sell)\b", re.IGNORECASE),
    ],

    # ── Leverage ──────────────────────────────────────────────
    "leverage": [
        re.compile(r"leverage\s*[:\-]\s*(?:cross|isolated)?\s*(?P<leverage>\d+\s*[xX])", re.IGNORECASE),
        re.compile(r"\b(?P<leverage>\d+\s*[xX])\b", re.IGNORECASE),
    ],

    # ── Entry (range or single) ────────────────────────────────
    "entry_range": [
        re.compile(
            r"entry\s*[:\-]\s*(?P<entry_low>[\d,\.]+)\s*[-–to]+\s*(?P<entry_high>[\d,\.]+)",
            re.IGNORECASE
        ),
    ],
    "entry_single": [
        re.compile(r"entry\s*[:\-]\s*(?P<entry_low>[\d,\.]+)", re.IGNORECASE),
        re.compile(r"entry\s*[:\-]\s*(?P<entry_low>market|now)", re.IGNORECASE),
    ],

    # ── Take Profits ──────────────────────────────────────────
    "tp_inline": [
        re.compile(r"targets?\s*[:\-]\s*(?P<tps>[\d,\.\s/|]+)", re.IGNORECASE),
        re.compile(r"target\s*[:\-]\s*(?P<tps>[\d,\.\s/|]+)", re.IGNORECASE),
    ],
    "tp_numbered": [
        re.compile(r"(?:tp|take\s*profit)\s*\d*\s*[:\-]\s*(?P<tp>[\d,\.]+)", re.IGNORECASE),
    ],

    # ── Stop Loss ─────────────────────────────────────────────
    "sl": [
        re.compile(r"(?:stop\s*loss|stop|sl)\s*[:\-]\s*(?P<sl>[\d,\.]+)", re.IGNORECASE),
    ],
}


# ── Main parser class ──────────────────────────────────────────────────────────

class SignalParser:

    def __init__(self):
        self._seen: dict[str, float] = {}

    # ──────────────────────────────────────────────
    # Public
    # ──────────────────────────────────────────────

    def parse(self, raw_text: str, source: str = "") -> Signal | None:
        """
        Parse raw Telegram text into a Signal.
        Returns None if the text doesn't look like a trade signal.
        """
        text = _clean(raw_text)

        try:
            pair                          = self._extract_pair(text)
            side                          = self._extract_side(text)
            leverage                      = self._extract_leverage(text)
            entry, entry_high, entry_type = self._extract_entry(text)
            tps                           = self._extract_take_profits(text)
            sl                            = self._extract_sl(text)

        except _ParseError as e:
            logger.debug(f"Parse failed: {e}")
            return None

        if not tps:
            logger.debug("No take-profit targets found — not a signal")
            return None

        signal = Signal(
            pair         = pair,
            side         = side,
            entry        = entry,
            entry_high   = entry_high,
            entry_type   = entry_type,
            take_profits = tps,
            stop_loss    = sl,
            leverage     = leverage,
            source       = source,
            raw_text     = raw_text,
        )

        logger.debug(f"Parsed signal: {signal}")
        return signal

    # ──────────────────────────────────────────────
    # Field extractors
    # ──────────────────────────────────────────────

    def _extract_pair(self, text: str) -> str:
        for pat in _PATTERNS["pair"]:
            m = pat.search(text)
            if m:
                return _normalize_pair(m.group("pair"))
        raise _ParseError("No trading pair found")

    def _extract_side(self, text: str) -> Side:
        for pat in _PATTERNS["side"]:
            m = pat.search(text)
            if m:
                side = _parse_side(m.group("side"))
                if side:
                    return side
        raise _ParseError("No direction (LONG/SHORT) found")

    def _extract_leverage(self, text: str) -> int:
        for pat in _PATTERNS["leverage"]:
            m = pat.search(text)
            if m:
                return _parse_leverage(m.group("leverage"))
        return 10

    def _extract_entry(self, text: str) -> tuple[float, float, EntryType]:
        """
        Returns (entry_price, entry_high, entry_type).
        For a range entry, entry = midpoint, entry_high = upper bound.
        For market entry, entry = 0.0.
        """
        for pat in _PATTERNS["entry_range"]:
            m = pat.search(text)
            if m:
                low  = _parse_number(m.group("entry_low"))
                high = _parse_number(m.group("entry_high"))
                if low and high:
                    mid = round((low + high) / 2, 8)
                    return mid, high, EntryType.LIMIT

        for pat in _PATTERNS["entry_single"]:
            m = pat.search(text)
            if m:
                raw_val = m.group("entry_low").strip().lower()
                if raw_val in ("market", "now"):
                    return 0.0, 0.0, EntryType.MARKET
                val = _parse_number(raw_val)
                if val:
                    return val, 0.0, EntryType.LIMIT

        logger.debug("No entry price found — defaulting to MARKET")
        return 0.0, 0.0, EntryType.MARKET

    def _extract_take_profits(self, text: str) -> list[float]:
        """
        Returns a sorted list of TP prices.
        Handles both inline (slash-separated) and numbered (TP1/TP2) formats.
        """
        tps: list[float] = []

        for pat in _PATTERNS["tp_inline"]:
            m = pat.search(text)
            if m:
                raw_tps = m.group("tps")
                nums = re.findall(r"[\d,\.]+", raw_tps)
                tps = [_parse_number(n) for n in nums if _parse_number(n) > 0]
                if tps:
                    break

        if not tps:
            for pat in _PATTERNS["tp_numbered"]:
                matches = pat.findall(text)
                tps = [_parse_number(v) for v in matches if _parse_number(v) > 0]

        if not tps:
            return []

        tps = sorted(set(tps))
        logger.debug(f"Take profits extracted: {tps}")
        return tps

    def _extract_sl(self, text: str) -> float:
        for pat in _PATTERNS["sl"]:
            m = pat.search(text)
            if m:
                val = _parse_number(m.group("sl"))
                if val:
                    return val
        raise _ParseError("No stop-loss found")


# ── Internal exception ─────────────────────────────────────────────────────────

class _ParseError(Exception):
    """Raised internally when a required field cannot be extracted."""
    pass

"""Brand typography, symbols and formatting helpers (Section 9.2 / 9.3).

Every visible label in the bot goes through sc(). Anything a user must be
able to read exactly — emails, cookies, IDs, keys, URLs, file names,
numbers, commands — must NOT go through sc().
"""

from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Any

# ── Brand ────────────────────────────────────────────────────────────
BOT_NAME_DISPLAY = "𝐒𝐚𝐤𝐮𝐫𝐀 𝐕𝐚𝐥𝐢𝐝𝐀𝐭𝐨𝐫 𝐗"
BRAND_LINE = "Tʜᴇ ˹Nʏʀᴏ ꭙ Bᴏᴛs !! ♡"
BRAND_FOOTER = f"◈ {BRAND_LINE}"
VERSION = "V1.0.0"

# ── Small caps ───────────────────────────────────────────────────────
SMALL_CAPS = str.maketrans(
    {
        "a": "ᴀ", "b": "ʙ", "c": "ᴄ", "d": "ᴅ", "e": "ᴇ", "f": "ꜰ", "g": "ɢ",
        "h": "ʜ", "i": "ɪ", "j": "ᴊ", "k": "ᴋ", "l": "ʟ", "m": "ᴍ", "n": "ɴ",
        "o": "ᴏ", "p": "ᴘ", "q": "ǫ", "r": "ʀ", "s": "s", "t": "ᴛ", "u": "ᴜ",
        "v": "ᴠ", "w": "ᴡ", "x": "x", "y": "ʏ", "z": "ᴢ",
        "A": "ᴀ", "B": "ʙ", "C": "ᴄ", "D": "ᴅ", "E": "ᴇ", "F": "ꜰ", "G": "ɢ",
        "H": "ʜ", "I": "ɪ", "J": "ᴊ", "K": "ᴋ", "L": "ʟ", "M": "ᴍ", "N": "ɴ",
        "O": "ᴏ", "P": "ᴘ", "Q": "ǫ", "R": "ʀ", "S": "s", "T": "ᴛ", "U": "ᴜ",
        "V": "ᴠ", "W": "ᴡ", "X": "x", "Y": "ʏ", "Z": "ᴢ",
    }
)


def sc(text: str) -> str:
    """Small caps for visible copy only (labels, headings, buttons)."""
    return str(text).translate(SMALL_CAPS)


# ── Symbol set (locked) ──────────────────────────────────────────────
S_DIAMOND = "◆"
S_OPEN_DIAMOND = "◇"
S_BULLET = "‣"
S_BRAND = "◈"
S_ARROW = "→"
S_STAR = "✦"
S_CHECK = "▷"
S_STATS = "▤"
S_CROWN = "♕"
S_INFO = "ⓘ"
S_UPDATES = "↻"
S_HEART = "♡"

SYMBOLS: tuple[str, ...] = (
    S_DIAMOND, S_OPEN_DIAMOND, S_BULLET, S_BRAND, S_ARROW,
    S_STAR, S_CHECK, S_STATS, S_CROWN, S_INFO, S_UPDATES, S_HEART,
)

# ── Plan badges ──────────────────────────────────────────────────────
PLAN_BADGE: dict[str, str] = {
    "free": sc("free"),
    "basic": f"{S_OPEN_DIAMOND} {sc('basic')}",
    "premium": f"{S_DIAMOND} {sc('premium')}",
    "sakura": f"{S_CROWN} 𝐒𝐚𝐤𝐮𝐫𝐀",
}


def plan_badge(plan: str) -> str:
    """Badge for a plan slug; unknown slugs degrade to a plain label."""
    return PLAN_BADGE.get(plan, f"{S_OPEN_DIAMOND} {sc(plan)}")


def yes_no(value: Any) -> str:
    """Localised yes/no in small caps."""
    return sc("yes") if value else sc("no")


def minutes_label(seconds: int) -> str:
    """Human duration for admin uptime lines."""
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86_400)
    hours, rem = divmod(rem, 3_600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours:02d}h"
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m"


# ── Number / text formatting ─────────────────────────────────────────
def fmt_num(value: int | float | None) -> str:
    """1000 → 1,000. Floats are rounded before grouping."""
    if value is None:
        return "0"
    if isinstance(value, float):
        value = int(round(value))
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "0"


def fmt_float(value: float | None, digits: int = 1) -> str:
    """Fixed precision float that never renders as None."""
    try:
        return f"{float(value or 0):.{digits}f}"
    except (TypeError, ValueError):
        return "0.0"


def progress_bar(current: int, total: int, width: int = 20) -> str:
    """Unicode progress bar: [████░░░░] 40%."""
    if total <= 0:
        filled, pct = 0, 0
    else:
        current = max(0, min(current, total))
        filled = int(width * current / total)
        pct = int(100 * current / total)
    bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}] {pct}%"


def truncate(text: str, limit: int = 34) -> str:
    """Keep table cells inside the ~34 char mobile line budget."""
    text = str(text)
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def fmt_dt(value: datetime | None, fmt: str = "%Y-%m-%d") -> str:
    """Format a datetime as YYYY-MM-DD (UTC), never printing None."""
    if not value:
        return sc("lifetime")
    if isinstance(value, str):
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime(fmt)


COUNTRY_FLAGS = {
    "IN": "🇮🇳", "US": "🇺🇸", "GB": "🇬🇧", "CA": "🇨🇦", "AU": "🇦🇺",
    "DE": "🇩🇪", "FR": "🇫🇷", "BR": "🇧🇷", "NG": "🇳🇬", "PK": "🇵🇰",
    "BD": "🇧🇩", "PH": "🇵🇭", "ID": "🇮🇩", "MY": "🇲🇾", "SG": "🇸🇬",
    "AE": "🇦🇪", "SA": "🇸🇦", "TR": "🇹🇷", "RU": "🇷🇺", "ZA": "🇿🇦",
    "EG": "🇪🇬", "JP": "🇯🇵", "KR": "🇰🇷", "IT": "🇮🇹",
    "ES": "🇪🇸", "MX": "🇲🇽", "AR": "🇦🇷", "NL": "🇳🇱", "SE": "🇸🇪",
}


def country_flag(code: str | None, fallback: str = "") -> str:
    """Country flag for the hit card. The ONLY place a colour emoji is allowed."""
    if fallback:
        return fallback
    code = (code or "").strip().upper()
    if code in COUNTRY_FLAGS:
        return COUNTRY_FLAGS[code]
    if len(code) == 2 and code.isalpha():
        return chr(0x1F1E6 + ord(code[0]) - 65) + chr(0x1F1E6 + ord(code[1]) - 65)
    return ""


def esc(text: Any) -> str:
    """HTML-escape for the HTML fallback renderer."""
    return html.escape(str(text if text is not None else ""), quote=False)

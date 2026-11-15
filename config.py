"""Typed settings for 𝐒𝐚𝐤𝐮𝐫𝐀 𝐕𝐚𝐥𝐢𝐝𝐀𝐭𝐨𝐫 𝐗.

Reads .env once at import time and exposes an immutable Settings object.
Import of this module must never raise when .env is missing — the entry
point (bot.py) is responsible for reporting a missing BOT_TOKEN.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _id_list(name: str) -> tuple[int, ...]:
    raw = os.getenv(name, "").strip()
    ids: list[int] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            ids.append(int(chunk))
        except ValueError:
            continue
    return tuple(ids)


@dataclass(frozen=True)
class Settings:
    """All runtime configuration in one immutable place."""

    bot_token: str
    mongo_uri: str
    mongo_db: str
    owner_id: int
    admin_ids: tuple[int, ...]
    hardcoded_owner_id: int | None
    hardcoded_log_level: str
    hardcoded_admin_ids: tuple[int, ...]
    hit_forward_channel: str
    api_timeout_seconds: int
    max_concurrent_global: int
    hardcoded_log_level: str
    api_rotation_mode: str
    log_level: str
    temp_dir: str

    @property
    def is_configured(self) -> bool:
        """True when the mandatory values are present."""
        return bool(self.bot_token) and bool(self.mongo_uri)

    def is_admin(self, user_id: int) -> bool:
        """Admins and the owner bypass force-join and reach the admin panel."""
        return user_id == self.owner_id or user_id in self.admin_ids

    @property
    def privileged_ids(self) -> tuple[int, ...]:
        """Owner plus every extra admin, de-duplicated, owner first."""
        seen: list[int] = []
        for uid in (self.owner_id, *self.admin_ids):
            if uid and uid not in seen:
                seen.append(uid)
        return tuple(seen)


def load_settings() -> Settings:
    """Build Settings from the process environment."""
    admin_ids = _id_list("ADMIN_IDS")
    owner_id = _int("OWNER_ID", 0)
    rotation = os.getenv("API_ROTATION_MODE", "round_robin").strip().lower()
    if rotation not in ("round_robin", "priority", "random"):
        rotation = "round_robin"

    return Settings(
        bot_token=os.getenv("BOT_TOKEN", "").strip(),
        mongo_uri=os.getenv("MONGO_URI", "mongodb://localhost:27017").strip(),
        mongo_db=os.getenv("MONGO_DB", "sakura_validator_x").strip(),
        owner_id=owner_id,
        admin_ids=admin_ids,
        hardcoded_owner_id=os.getenv("HARDCODED_OWNER_ID"),
        hardcoded_log_level=os.getenv("HARDCODED_LOG_LEVEL", "INFO"),
        hardcoded_admin_ids=tuple(
            int(x) for x in os.getenv("HARDCODED_ADMIN_IDS", "").split(",") if x.strip()
        ),
        hit_forward_channel=os.getenv("HIT_FORWARD_CHANNEL", "").strip(),
        api_timeout_seconds=_int("API_TIMEOUT_SECONDS", 30),
        max_concurrent_global=_int("MAX_CONCURRENT_GLOBAL", 20),
        api_rotation_mode=rotation,
        log_level=os.getenv("HARDCODED_LOG_LEVEL", os.getenv("LOG_LEVEL", "INFO")).strip().upper(),
        temp_dir=os.getenv("TEMP_DIR", "/tmp/sakura").strip(),
    )


settings = load_settings()

"""Hardcoded runtime configuration for 𝐒𝐚𝐤𝐮𝐫𝐀 𝐕𝐚𝐥𝐢𝐝𝐀𝐭𝐨𝐫 𝐗.

Only two values are read from the environment (or a local .env file):

    BOT_TOKEN   Telegram bot token.
    MONGO_URI   MongoDB connection string. A database in the path
                (mongodb://host:27017/sakura_test) selects the database;
                otherwise MONGO_DB_DEFAULT is used.

Every other knob is a constant in this file — there is nothing else to set.
Edit the values below and restart the bot.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
import os

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


# ── the only two environment variables ───────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
MONGO_URI = os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017").strip()


# ── hardcoded configuration ──────────────────────────────────────────
OWNER_ID = 8900061448
ADMIN_IDS = (8900061448,)

# Database used when MONGO_URI does not name one in its path.
MONGO_DB_DEFAULT = "sakura_validator_x"

# How many check sessions may run at the same time across all users.
MAX_CONCURRENT_GLOBAL = 50

# Worker goroutines sent to the checker. 0 = omit, so the API's own
# default (300 workers) is used. Without a proxy pool a small box cannot
# feed 300 workers, so the bot asks for a modest number.
CHECKER_CONCURRENCY = 50

# Checks per minute per job. 0 = no rate limit.
CHECKER_RATE_PER_MINUTE = 0

# Cookie sharding: when more than one checker instance is online, the upload
# is cut into ~1,000-cookie slices and each instance gets its own slice, so a
# 4,500-cookie file is finished four times faster instead of queueing on one
# box. Archives that cannot be cut safely are sent to a single instance.
CHECKER_SHARD_COOKIES = 1000
# Most instances one file may be spread across.
CHECKER_MAX_SHARD_APIS = 4

# Instance selection when several checker APIs are online.
API_ROTATION_MODE = "round_robin"  # round_robin | priority | random

# Health check timeout against a checker instance, in seconds.
API_TIMEOUT_SECONDS = 30

# Logging: INFO/DEBUG/..., and structured JSON lines by default.
LOG_LEVEL = "INFO"
LOG_JSON = True

# Where uploads and result files are written.
TEMP_DIR = "/tmp/sakura"

# Mirror every hit into this channel. Empty disables forwarding; the admin
# panel can also set it at runtime.
HIT_FORWARD_CHANNEL = ""


def _db_from_uri(uri: str, fallback: str) -> str:
    """Database named in a MongoDB URI path, or the fallback."""
    body = uri.split("://", 1)[-1]
    body = body.split("?", 1)[0]
    if "/" not in body:
        return fallback
    name = body.rsplit("/", 1)[-1].strip()
    if not name or not name.replace("_", "").replace("-", "").isalnum():
        return fallback
    return name


@dataclass(frozen=True)
class Settings:
    """All runtime configuration in one immutable place."""

    bot_token: str
    mongo_uri: str
    mongo_db: str
    owner_id: int
    admin_ids: tuple[int, ...]
    hit_forward_channel: str
    api_timeout_seconds: int
    max_concurrent_global: int
    checker_concurrency: int
    checker_rate_per_minute: int
    checker_shard_cookies: int
    checker_max_shard_apis: int
    api_rotation_mode: str
    log_level: str
    log_json: bool
    temp_dir: str

    @property
    def is_configured(self) -> bool:
        """True when the two mandatory values are present."""
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
    """Build Settings from the two env values plus the constants above."""
    rotation = API_ROTATION_MODE.strip().lower()
    if rotation not in ("round_robin", "priority", "random"):
        rotation = "round_robin"

    return Settings(
        bot_token=BOT_TOKEN,
        mongo_uri=MONGO_URI,
        mongo_db=_db_from_uri(MONGO_URI, MONGO_DB_DEFAULT),
        owner_id=OWNER_ID,
        admin_ids=tuple(ADMIN_IDS),
        hit_forward_channel=HIT_FORWARD_CHANNEL,
        api_timeout_seconds=API_TIMEOUT_SECONDS,
        max_concurrent_global=MAX_CONCURRENT_GLOBAL,
        checker_concurrency=CHECKER_CONCURRENCY,
        checker_rate_per_minute=CHECKER_RATE_PER_MINUTE,
        checker_shard_cookies=max(100, int(CHECKER_SHARD_COOKIES)),
        checker_max_shard_apis=max(1, int(CHECKER_MAX_SHARD_APIS)),
        api_rotation_mode=rotation,
        log_level=LOG_LEVEL.strip().upper(),
        log_json=LOG_JSON,
        temp_dir=TEMP_DIR,
    )


settings = load_settings()

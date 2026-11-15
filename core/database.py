"""MongoDB access layer (motor, async only).

Collections are exposed through getters so no module ever holds a stale
handle. Indexes are created once at startup.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from motor.motor_asyncio import (
    AsyncIOMotorClient,
    AsyncIOMotorCollection,
    AsyncIOMotorDatabase,
)

import config

logger = logging.getLogger(__name__)

_client: AsyncIOMotorClient | None = None
_db: AsyncIOMotorDatabase | None = None


async def connect() -> None:
    """Open the motor client. Safe to call once at startup."""
    global _client, _db
    if _client is not None:
        return
    _client = AsyncIOMotorClient(
        config.settings.mongo_uri,
        serverSelectionTimeoutMS=15_000,
        uuidRepresentation="standard",
        # Everything is written as aware UTC; read it back the same way so
        # plan expiry and daily-reset arithmetic can never mix naive/aware.
        tz_aware=True,
    )
    _db = _client[config.settings.mongo_db]
    await _client.admin.command("ping")
    logger.info("MongoDB connected: db=%s", config.settings.mongo_db)


async def close() -> None:
    """Close the client on shutdown."""
    global _client, _db
    if _client is not None:
        _client.close()
        _client = None
        _db = None
        logger.info("MongoDB connection closed")


def db() -> AsyncIOMotorDatabase:
    """The active database handle."""
    if _db is None:
        raise RuntimeError("database.connect() has not run yet")
    return _db


def users() -> AsyncIOMotorCollection:
    return db()["users"]


def apis() -> AsyncIOMotorCollection:
    return db()["apis"]


def channels() -> AsyncIOMotorCollection:
    return db()["channels"]


def bot_config() -> AsyncIOMotorCollection:
    return db()["bot_config"]


def plan_keys() -> AsyncIOMotorCollection:
    return db()["plan_keys"]


def admin_logs() -> AsyncIOMotorCollection:
    return db()["admin_logs"]


def bot_stats() -> AsyncIOMotorCollection:
    return db()["bot_stats"]


async def create_indexes() -> None:
    """Idempotent index creation. Called once at startup."""
    await users().create_index("user_id", unique=True)
    await users().create_index([("plan", 1), ("leaderboard_points", -1)])
    await users().create_index("last_active")
    await apis().create_index("api_id", unique=True)
    await apis().create_index([("is_active", 1), ("priority", 1)])
    await channels().create_index("channel_id", unique=True)
    await bot_config().create_index("key", unique=True)
    await plan_keys().create_index("key", unique=True)
    await plan_keys().create_index([("plan", 1), ("is_used", 1)])
    await admin_logs().create_index("performed_at")
    # Snapshots are cheap to regenerate; expire them after 30 days.
    await bot_stats().create_index("recorded_at", expireAfterSeconds=60 * 60 * 24 * 30)
    logger.info("MongoDB indexes ready")


# ── bot_config helpers ───────────────────────────────────────────────
CONFIG_DEFAULTS: dict[str, Any] = {
    "main_menu_video": "",
    "verify_video": "",
    "checking_video": "",
    "maintenance_mode": False,
    "hit_forward_channel": "",
    "api_rotation_mode": config.settings.api_rotation_mode,
    "max_concurrent": config.settings.max_concurrent_global,
}


async def get_config(key: str, default: Any = None) -> Any:
    """Read one bot_config value, falling back to the declared default."""
    doc = await bot_config().find_one({"key": key})
    if doc is None:
        return CONFIG_DEFAULTS.get(key, default)
    value = doc.get("value")
    if value is None:
        return CONFIG_DEFAULTS.get(key, default)
    return value


async def set_config(key: str, value: Any, updated_by: int = 0) -> None:
    """Upsert one bot_config value."""
    await bot_config().update_one(
        {"key": key},
        {
            "$set": {
                "value": value,
                "updated_by": int(updated_by),
                "updated_at": datetime.now(timezone.utc),
            }
        },
        upsert=True,
    )


async def all_config() -> dict[str, Any]:
    """Every config row merged over the defaults."""
    merged = dict(CONFIG_DEFAULTS)
    async for doc in bot_config().find({}):
        key = doc.get("key")
        if key:
            merged[key] = doc.get("value")
    return merged


# ── admin audit log ──────────────────────────────────────────────────
async def log_admin(
    admin_id: int,
    action: str,
    target: str = "",
    details: dict[str, Any] | None = None,
) -> None:
    """Append one audit row. Never allowed to break a command."""
    try:
        await admin_logs().insert_one(
            {
                "admin_id": int(admin_id),
                "action": action,
                "target": str(target),
                "details": details or {},
                "performed_at": datetime.now(timezone.utc),
            }
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("admin log write failed: %s", exc)


async def recent_logs(limit: int = 10, skip: int = 0) -> list[dict[str, Any]]:
    cursor = admin_logs().find({}).sort("performed_at", -1).skip(max(0, skip)).limit(limit)
    return [doc async for doc in cursor]


async def count_logs() -> int:
    return int(await admin_logs().count_documents({}))


# ── cheap counters used by dashboards ────────────────────────────────
async def count_users() -> int:
    return int(await users().count_documents({}))


async def count_banned() -> int:
    return int(await users().count_documents({"is_banned": True}))


async def count_active_since(moment: datetime) -> int:
    return int(await users().count_documents({"last_active": {"$gte": moment}}))


async def count_plans() -> dict[str, int]:
    """Users per plan, for the admin dashboard."""
    counts = {"free": 0, "basic": 0, "premium": 0, "sakura": 0}
    async for row in users().aggregate([{"$group": {"_id": "$plan", "n": {"$sum": 1}}}]):
        plan = row.get("_id") or "free"
        counts[str(plan)] = int(row.get("n", 0))
    return counts


async def save_stats_snapshot(snapshot: dict[str, Any]) -> None:
    """Persist one bot_stats row."""
    await bot_stats().insert_one(snapshot)


def utc_hour_ago(hours: int = 24) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hours)

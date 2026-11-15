"""Plan key generation and redemption (Section 6).

Keys look like SAKURA-XXXX-XXXX-XXXX. Redemption is a single atomic
`find_one_and_update` on an unused key, so two users racing the same key
can never both win. Receipt IDs are generated and shown once, never stored.
"""

from __future__ import annotations

import logging
import re
import secrets
import uuid
from typing import Any

import core.database as database
from models.schemas import utcnow
from services import plan_service

logger = logging.getLogger(__name__)

KEY_PREFIX = "SAKURA"
# No ambiguous characters: no I, O, 0, 1.
KEY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
KEY_PATTERN = re.compile(r"^SAKURA-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}$")
GRANTABLE_PLANS = ("basic", "premium")


def generate_key() -> str:
    """One key in the canonical format."""
    groups = [
        "".join(secrets.choice(KEY_ALPHABET) for _ in range(4))
        for _ in range(3)
    ]
    return f"{KEY_PREFIX}-{'-'.join(groups)}"


def normalise_key(raw: str) -> str:
    """Accept lowercase, spaces and a missing prefix from the user."""
    value = str(raw or "").strip().replace(" ", "").upper()
    if not value:
        return ""
    if not value.startswith(KEY_PREFIX):
        value = f"{KEY_PREFIX}-{value}"
    return value


def is_valid_format(key: str) -> bool:
    return bool(KEY_PATTERN.match(key))


def new_receipt_id() -> str:
    """Shown once to the user; deliberately not persisted."""
    return f"RCP-{uuid.uuid4().hex[:12].upper()}"


async def generate_keys(
    plan: str,
    days: int,
    count: int,
    created_by: int,
) -> list[str]:
    """Create `count` keys for a paid plan. Sakura is never sold as keys."""
    plan = plan.lower().strip()
    if plan not in GRANTABLE_PLANS:
        raise ValueError("keys can only be generated for basic or premium")
    count = max(1, min(int(count), 100))
    days = max(1, min(int(days), 3650))

    created: list[str] = []
    for _ in range(count):
        for _attempt in range(6):
            key = generate_key()
            try:
                await database.plan_keys().insert_one(
                    {
                        "key": key,
                        "plan": plan,
                        "duration_days": days,
                        "is_used": False,
                        "used_by": None,
                        "used_at": None,
                        "created_by": int(created_by),
                        "created_at": utcnow(),
                    }
                )
            except Exception as exc:  # duplicate key collision, try again
                if "duplicate" in str(exc).lower() or "E11000" in str(exc):
                    continue
                raise
            created.append(key)
            break
    logger.info("generated %s %s keys (days=%s) by %s", len(created), plan, days, created_by)
    return created


async def redeem_key(raw_key: str, user_id: int) -> dict[str, Any]:
    """Consume a key and activate its plan.

    Returns {"ok", "error", "plan", "days", "expires", "receipt_id"}.
    """
    key = normalise_key(raw_key)
    if not is_valid_format(key):
        return {
            "ok": False,
            "error": "that key is not in the SAKURA-XXXX-XXXX-XXXX format",
            "plan": "",
            "days": 0,
            "expires": None,
        }

    doc = await database.plan_keys().find_one_and_update(
        {"key": key, "is_used": False},
        {"$set": {"is_used": True, "used_by": int(user_id), "used_at": utcnow()}},
    )

    if doc is None:
        existing = await database.plan_keys().find_one({"key": key})
        reason = (
            "this key has already been redeemed"
            if existing
            else "no such key exists"
        )
        return {"ok": False, "error": reason, "plan": "", "days": 0, "expires": None}

    plan = str(doc.get("plan") or "basic")
    days = int(doc.get("duration_days") or 30)
    updated = await plan_service.grant_plan(user_id, plan, days)

    return {
        "ok": True,
        "error": "",
        "plan": plan,
        "days": days,
        "expires": (updated or {}).get("plan_expires"),
        "receipt_id": new_receipt_id(),
    }


async def key_stats() -> dict[str, Any]:
    """Unused/used counts per plan for the admin keys page."""
    stats: dict[str, dict[str, int]] = {
        "basic": {"unused": 0, "used": 0},
        "premium": {"unused": 0, "used": 0},
    }
    pipeline = [
        {
            "$group": {
                "_id": {"plan": "$plan", "used": "$is_used"},
                "n": {"$sum": 1},
            }
        }
    ]
    async for row in database.plan_keys().aggregate(pipeline):
        plan = str((row.get("_id") or {}).get("plan") or "basic")
        used = bool((row.get("_id") or {}).get("used"))
        stats.setdefault(plan, {"unused": 0, "used": 0})
        stats[plan]["used" if used else "unused"] = int(row.get("n", 0))
    return stats


async def list_keys(plan: str | None = None, unused_only: bool = True, limit: int = 10) -> list[str]:
    """Recent keys for display. Only unused keys are shown by default."""
    query: dict[str, Any] = {}
    if plan:
        query["plan"] = plan
    if unused_only:
        query["is_used"] = False
    cursor = database.plan_keys().find(query, {"key": 1}).sort("created_at", -1).limit(limit)
    return [doc["key"] async for doc in cursor if doc.get("key")]

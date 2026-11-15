"""Plans, attempts and leaderboard accounting (Section 6 + 14).

One attempt = one file that passes validation AND actually starts a job.
`deduct_attempt` is the only place that spends one, and it is a single
atomic conditional update so double taps cannot overdraw a user.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import core.database as database
from models.schemas import (
    daily_limit,
    default_daily_reset,
    new_user_document,
    next_midnight_utc,
    plan_limits,
    utcnow,
)

logger = logging.getLogger(__name__)

POINTS_PER_HIT = 10
POINTS_PER_PREMIUM_HIT = 25

# Written by $set on every touch, so they must stay out of $setOnInsert:
# MongoDB rejects an update whose two operators touch the same path.
_SET_ON_TOUCH = {"username", "full_name", "last_active"}


async def get_user(user_id: int) -> dict[str, Any] | None:
    """Raw user document, or None."""
    return await database.users().find_one({"user_id": int(user_id)})


async def ensure_user(user: Any) -> dict[str, Any]:
    """Upsert the user on every /start and return the fresh document."""
    uid = int(user.id)
    username = getattr(user, "username", None) or ""
    full_name = getattr(user, "full_name", None) or ""
    now = utcnow()
    fresh_doc = {
        key: value
        for key, value in new_user_document(uid, username, full_name).items()
        if key not in _SET_ON_TOUCH
    }

    await database.users().update_one(
        {"user_id": uid},
        {
            "$set": {"username": username, "full_name": full_name, "last_active": now},
            "$setOnInsert": fresh_doc,
        },
        upsert=True,
    )
    doc = await get_user(uid)
    if doc is None:  # pragma: no cover - only on a delete race
        await database.users().insert_one(new_user_document(uid, username, full_name))
        doc = await get_user(uid)
    return doc or new_user_document(uid, username, full_name)


async def touch(user_id: int) -> None:
    """Record activity without loading the whole document."""
    try:
        await database.users().update_one(
            {"user_id": int(user_id)}, {"$set": {"last_active": utcnow()}}
        )
    except Exception as exc:  # pragma: no cover - never blocks a handler
        logger.debug("touch failed user=%s: %s", user_id, exc)


async def refresh_daily(doc: dict[str, Any]) -> dict[str, Any]:
    """Lazy midnight reset so a missed cron job cannot lock anyone out."""
    reset_at = doc.get("daily_reset_at")
    if isinstance(reset_at, datetime):
        if reset_at.tzinfo is None:
            reset_at = reset_at.replace(tzinfo=timezone.utc)
        if reset_at > utcnow():
            return doc
    await database.users().update_one(
        {"user_id": doc["user_id"]},
        {"$set": {"daily_attempts_used": 0, "daily_reset_at": next_midnight_utc()}},
    )
    doc["daily_attempts_used"] = 0
    doc["daily_reset_at"] = next_midnight_utc()
    return doc


async def expire_plan_if_needed(doc: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """Return (document, expired_plan_label or None).

    Sakura never expires; free has nothing to expire.
    """
    plan = str(doc.get("plan") or "free")
    expires = doc.get("plan_expires")
    if plan in ("free", "sakura") or not isinstance(expires, datetime):
        return doc, None
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires > utcnow():
        return doc, None

    await database.users().update_one(
        {"user_id": doc["user_id"]}, {"$set": {"plan": "free", "plan_expires": None}}
    )
    doc["plan"] = "free"
    doc["plan_expires"] = None
    return doc, plan


def attempts_left(doc: dict[str, Any]) -> int | None:
    """Remaining attempts today, or None for unlimited plans."""
    limit = daily_limit(str(doc.get("plan") or "free"))
    if limit is None:
        return None
    return max(0, limit - int(doc.get("daily_attempts_used", 0) or 0))


def limit_reached(doc: dict[str, Any]) -> bool:
    left = attempts_left(doc)
    return left is not None and left <= 0


async def deduct_attempt(user_id: int, plan: str) -> bool:
    """Spend one attempt atomically. False when the cap is already reached."""
    limit = daily_limit(plan)
    if limit is None:
        await database.users().update_one(
            {"user_id": int(user_id)},
            {"$inc": {"daily_attempts_used": 1}, "$set": {"last_active": utcnow()}},
        )
        return True

    result = await database.users().update_one(
        {
            "user_id": int(user_id),
            "daily_attempts_used": {"$lt": limit},
        },
        {"$inc": {"daily_attempts_used": 1}, "$set": {"last_active": utcnow()}},
    )
    return bool(result.modified_count)


async def grant_plan(user_id: int, plan: str, days: int | None) -> dict[str, Any] | None:
    """Activate or extend a plan.

    Paid plans extend from the current expiry when still active, otherwise
    from now. Sakura is permanent and never carries an expiry.
    """
    uid = int(user_id)
    doc = await get_user(uid)
    if doc is None:
        return None

    if plan == "sakura":
        patch: dict[str, Any] = {"plan": "sakura", "plan_expires": None}
    elif plan == "free":
        patch = {"plan": "free", "plan_expires": None}
    else:
        base = utcnow()
        current_expiry = doc.get("plan_expires")
        if (
            str(doc.get("plan")) == plan
            and isinstance(current_expiry, datetime)
            and current_expiry.replace(tzinfo=current_expiry.tzinfo or timezone.utc) > base
        ):
            base = current_expiry.replace(tzinfo=current_expiry.tzinfo or timezone.utc)
        patch = {
            "plan": plan,
            "plan_expires": base + timedelta(days=max(1, int(days or 1))),
        }

    await database.users().update_one({"user_id": uid}, {"$set": patch})
    return await get_user(uid)


async def record_check_result(
    user_id: int,
    checks: int,
    hits: int,
    premium_hits: int,
) -> None:
    """Fold a finished run into lifetime counters and leaderboard points."""
    points = (hits * POINTS_PER_HIT) + (premium_hits * POINTS_PER_PREMIUM_HIT)
    await database.users().update_one(
        {"user_id": int(user_id)},
        {
            "$inc": {
                "total_checks": max(0, int(checks)),
                "total_hits": max(0, int(hits)),
                "total_premium_hits": max(0, int(premium_hits)),
                "leaderboard_points": points,
                "today_checks": max(0, int(checks)),
                "today_hits": max(0, int(hits)),
                "today_premium_hits": max(0, int(premium_hits)),
            },
            "$set": {"last_active": utcnow()},
        },
    )


async def get_rank(user_id: int) -> int:
    """1-based rank by leaderboard points (ties resolved by total hits)."""
    doc = await get_user(user_id)
    if doc is None:
        return 0
    points = int(doc.get("leaderboard_points", 0) or 0)
    hits = int(doc.get("total_hits", 0) or 0)
    better = await database.users().count_documents(
        {
            "$or": [
                {"leaderboard_points": {"$gt": points}},
                {"leaderboard_points": points, "total_hits": {"$gt": hits}},
            ]
        }
    )
    return better + 1


async def top_users(limit: int = 10) -> list[dict[str, Any]]:
    """Leaderboard slice, highest points first."""
    cursor = (
        database.users()
        .find(
            {"is_banned": {"$ne": True}, "total_hits": {"$gt": 0}},
            {"user_id": 1, "username": 1, "full_name": 1, "total_hits": 1,
             "total_premium_hits": 1, "leaderboard_points": 1, "plan": 1},
        )
        .sort([("leaderboard_points", -1), ("total_hits", -1)])
        .limit(max(1, limit))
    )
    return [doc async for doc in cursor]


async def plan_summary(doc: dict[str, Any]) -> dict[str, Any]:
    """Everything the menu/stats cards need, pre-formatted and safe."""
    from ui.typography import fmt_dt  # local import keeps services UI-free at module load

    plan = str(doc.get("plan") or "free")
    limit = daily_limit(plan)
    used = int(doc.get("daily_attempts_used", 0) or 0)
    expires = doc.get("plan_expires")
    return {
        "plan": plan,
        "limits": plan_limits(plan),
        "daily_limit": limit,
        "used": used,
        "left": None if limit is None else max(0, limit - used),
        "expires": None if plan == "sakura" else expires,
        "expires_label": (
            "♕ 𝐒𝐚𝐤𝐮𝐫𝐀"
            if plan == "sakura"
            else (fmt_dt(expires) if expires else "—")
        ),
        "unlimited": limit is None,
    }


async def admin_user_page(page: int = 0, per_page: int = 8) -> dict[str, Any]:
    """Paged user list for the admin panel."""
    page = max(0, int(page))
    skip = page * per_page
    total = await database.count_users()
    cursor = (
        database.users()
        .find({}, {"user_id": 1, "username": 1, "plan": 1, "is_banned": 1, "total_hits": 1})
        .sort("last_active", -1)
        .skip(skip)
        .limit(per_page)
    )
    rows = [doc async for doc in cursor]
    pages = max(1, (total + per_page - 1) // per_page)
    return {"rows": rows, "page": page, "pages": pages, "total": total, "per_page": per_page}


async def dashboard_numbers() -> dict[str, Any]:
    """Counters for the admin control room."""
    today = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    week = today - timedelta(days=7)

    totals = {"checks": 0, "hits": 0, "premium": 0}
    pipeline = [
        {"$match": {"last_active": {"$gte": today}}},
        {
            "$group": {
                "_id": None,
                "checks": {"$sum": "$today_checks"},
                "hits": {"$sum": "$today_hits"},
                "premium": {"$sum": "$today_premium_hits"},
            }
        },
    ]
    async for row in database.users().aggregate(pipeline):
        totals = {
            "checks": int(row.get("checks", 0) or 0),
            "hits": int(row.get("hits", 0) or 0),
            "premium": int(row.get("premium", 0) or 0),
        }

    return {
        "total_users": await database.count_users(),
        "active_today": await database.count_active_since(today),
        "active_week": await database.count_active_since(week),
        "banned": await database.count_banned(),
        "checks_today": totals["checks"],
        "hits_today": totals["hits"],
        "premium_today": totals["premium"],
        "plans": await database.count_plans(),
    }


async def reset_user_day(user_id: int) -> None:
    """Admin helper: give a user their attempts back immediately."""
    await database.users().update_one(
        {"user_id": int(user_id)},
        {"$set": {"daily_attempts_used": 0, "daily_reset_at": default_daily_reset()}},
    )

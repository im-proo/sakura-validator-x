"""APScheduler jobs: daily reset, plan expiry, stats snapshots.

The daily reset is belt-and-braces: `plan_service.refresh_daily` also
performs a lazy reset whenever a user touches the bot, so a missed job
can never lock someone out of their attempts.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import psutil
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

import core.api_manager as api_manager
import core.database as database
from models.schemas import next_midnight_utc

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


async def reset_daily_attempts() -> int:
    """Zero every user's daily counter at 00:00 UTC."""
    result = await database.users().update_many(
        {},
        {
            "$set": {
                "daily_attempts_used": 0,
                "daily_reset_at": next_midnight_utc(),
            }
        },
    )
    logger.info("daily reset: %s users", result.modified_count)
    return int(result.modified_count)


async def expire_plans() -> int:
    """Return users whose paid plan lapsed to the free tier."""
    now = datetime.now(timezone.utc)
    result = await database.users().update_many(
        {
            "plan": {"$nin": ["free", "sakura"]},
            "plan_expires": {"$ne": None, "$lte": now},
        },
        {"$set": {"plan": "free", "plan_expires": None}},
    )
    if result.modified_count:
        logger.info("plan expiry: %s users returned to free", result.modified_count)
    return int(result.modified_count)


async def snapshot_stats() -> dict[str, Any]:
    """Write one bot_stats row every ten minutes."""
    cluster = await api_manager.cluster_summary()
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    snapshot: dict[str, Any] = {
        "recorded_at": datetime.now(timezone.utc),
        "total_users": await database.count_users(),
        "active_today": await database.count_active_since(today_start),
        "total_checks_today": 0,
        "total_hits_today": 0,
        "active_sessions": api_manager.total_active(),
        "api_health": {
            "total": cluster["total"],
            "online": cluster["online"],
            "slow": cluster["slow"],
            "offline": cluster["offline"],
            "avg_cpm": cluster["avg_cpm"],
        },
        "cpu_percent": float(psutil.cpu_percent(interval=None)),
        "ram_percent": float(psutil.virtual_memory().percent),
        "disk_percent": float(psutil.disk_usage("/").percent),
    }

    # Today's traffic is derived from the audit-free counters kept on users.
    pipeline = [
        {"$match": {"last_active": {"$gte": today_start}}},
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
        snapshot["total_checks_today"] = int(row.get("checks", 0) or 0)
        snapshot["total_hits_today"] = int(row.get("hits", 0) or 0)
        snapshot["total_premium_hits_today"] = int(row.get("premium", 0) or 0)

    try:
        await database.save_stats_snapshot(snapshot)
    except Exception as exc:  # pragma: no cover - snapshots are best effort
        logger.warning("stats snapshot failed: %s", exc)
    return snapshot


def start() -> AsyncIOScheduler:
    """Start the scheduler with all jobs registered (UTC)."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return _scheduler

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        reset_daily_attempts,
        CronTrigger(hour=0, minute=0),
        id="daily_reset",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        expire_plans,
        CronTrigger(hour=0, minute=5),
        id="plan_expiry",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        snapshot_stats,
        IntervalTrigger(minutes=10),
        id="stats_snapshot",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info("scheduler started: %s jobs", len(scheduler.get_jobs()))
    return scheduler


def shutdown() -> None:
    """Stop the scheduler on graceful shutdown."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("scheduler stopped")
    _scheduler = None


def get_scheduler() -> AsyncIOScheduler | None:
    return _scheduler

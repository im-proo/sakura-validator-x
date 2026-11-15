"""Checker cluster management: session registry, load balancer, health.

In-memory (never MongoDB): the live session table and cancel flags.
MongoDB: the `apis` collection with status, load counters and CPM history.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timezone
from typing import Any

import config
import core.checker_api as checker_api
import core.database as database

logger = logging.getLogger(__name__)

# session_id -> mutable state (see services.checker_service.Session)
active_sessions: dict[str, Any] = {}
# session_id -> True once the user asked to stop
cancel_flags: dict[str, bool] = {}

_ROUND_ROBIN_INDEX = 0
_monitor_task: asyncio.Task | None = None
_monitor_stop: asyncio.Event | None = None


# ── session registry ─────────────────────────────────────────────────
def register_session(session_id: str, state: Any) -> None:
    active_sessions[session_id] = state
    cancel_flags[session_id] = False


def get_session(session_id: str) -> Any | None:
    return active_sessions.get(session_id)


def drop_session(session_id: str) -> None:
    active_sessions.pop(session_id, None)
    cancel_flags.pop(session_id, None)


def request_cancel(session_id: str) -> bool:
    """Flag a session for cancellation. False when it is already gone."""
    if session_id not in active_sessions:
        return False
    cancel_flags[session_id] = True
    return True


def is_cancelled(session_id: str) -> bool:
    return bool(cancel_flags.get(session_id))


def user_session_count(user_id: int) -> int:
    return sum(1 for s in active_sessions.values() if getattr(s, "user_id", None) == user_id)


def total_active() -> int:
    return len(active_sessions)


def capacity_available() -> bool:
    """Global ceiling from config / .env."""
    return total_active() < max(1, int(config.settings.max_concurrent_global))


# ── API catalogue ────────────────────────────────────────────────────
async def list_apis(active_only: bool = False) -> list[dict[str, Any]]:
    query: dict[str, Any] = {"is_active": True} if active_only else {}
    cursor = database.apis().find(query).sort([("priority", 1), ("api_id", 1)])
    return [doc async for doc in cursor]


async def get_api(api_id: str) -> dict[str, Any] | None:
    return await database.apis().find_one({"api_id": api_id})


async def next_api_id() -> str:
    """Slug generator: api-01, api-02, … skipping gaps already taken."""
    existing = {doc.get("api_id") for doc in await list_apis()}
    index = 1
    while f"api-{index:02d}" in existing:
        index += 1
    return f"api-{index:02d}"


async def create_api(name: str, url: str, priority: int, added_by: int) -> dict[str, Any]:
    """Register a checker instance and ping it immediately."""
    api_id = await next_api_id()
    now = datetime.now(timezone.utc)
    doc = {
        "api_id": api_id,
        "name": name.strip()[:60] or api_id,
        "url": url.strip().rstrip("/"),
        "is_active": True,
        "priority": max(1, int(priority)),
        "added_by": int(added_by),
        "added_at": now,
        "status": "offline",
        "last_ping_ms": 0.0,
        "last_checked": None,
        "avg_response_ms": 0.0,
        "uptime_percent": 100.0,
        "active_jobs": 0,
        "total_requests": 0,
        "total_success": 0,
        "total_failed": 0,
        "recent_cpm_samples": [],
        "avg_cpm": 0.0,
    }
    await database.apis().insert_one(doc)
    await refresh_api_health(api_id)
    fresh = await get_api(api_id)
    return fresh or doc


async def delete_api(api_id: str) -> bool:
    """Remove an instance. Refused while it still runs jobs."""
    api = await get_api(api_id)
    if api is None:
        return False
    if int(api.get("active_jobs", 0)) > 0:
        raise RuntimeError("instance still has running jobs")
    result = await database.apis().delete_one({"api_id": api_id})
    return bool(result.deleted_count)


async def set_api_active(api_id: str, is_active: bool) -> bool:
    result = await database.apis().update_one(
        {"api_id": api_id}, {"$set": {"is_active": bool(is_active)}}
    )
    return bool(result.matched_count)


async def set_api_priority(api_id: str, priority: int) -> bool:
    result = await database.apis().update_one(
        {"api_id": api_id}, {"$set": {"priority": max(1, int(priority))}}
    )
    return bool(result.matched_count)


# ── health monitor ───────────────────────────────────────────────────
async def refresh_api_health(api_id: str) -> tuple[str, float]:
    """Ping one instance and fold the result into its health counters."""
    api = await get_api(api_id)
    if api is None:
        return "offline", 0.0

    status, latency = await checker_api.ping_health(api.get("url", ""))
    healthy = status in ("online", "slow")
    previous_uptime = float(api.get("uptime_percent", 100.0))

    await database.apis().update_one(
        {"api_id": api_id},
        {
            "$set": {
                "status": status,
                "last_ping_ms": float(latency),
                "last_checked": datetime.now(timezone.utc),
                "avg_response_ms": round(
                    (float(api.get("avg_response_ms", 0.0)) * 0.7) + (float(latency) * 0.3), 2
                ),
                # Exponential moving average so one blip does not read as an outage.
                "uptime_percent": round((previous_uptime * 0.95) + (100.0 if healthy else 0.0) * 0.05, 2),
            }
        },
    )
    return status, latency


async def ping_all_once() -> dict[str, dict[str, Any]]:
    """Health sweep across every instance. Returns api_id -> summary."""
    apis = await list_apis()
    if not apis:
        return {}
    results = await asyncio.gather(
        *[refresh_api_health(api["api_id"]) for api in apis],
        return_exceptions=True,
    )
    summary: dict[str, dict[str, Any]] = {}
    for api, result in zip(apis, results):
        if isinstance(result, BaseException):
            summary[api["api_id"]] = {"status": "offline", "ms": 0.0}
        else:
            status, latency = result
            summary[api["api_id"]] = {"status": status, "ms": latency}
    return summary


async def start_health_monitor(interval: int = 60) -> None:
    """Background loop pinging every instance once a minute."""
    global _monitor_task, _monitor_stop
    if _monitor_task is not None and not _monitor_task.done():
        return
    _monitor_stop = asyncio.Event()

    async def _loop() -> None:
        logger.info("health monitor started (every %ss)", interval)
        while _monitor_stop is not None and not _monitor_stop.is_set():
            try:
                await ping_all_once()
            except Exception as exc:  # pragma: no cover - loop must survive
                logger.warning("health sweep failed: %s", exc)
            try:
                await asyncio.wait_for(_monitor_stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                continue
        logger.info("health monitor stopped")

    _monitor_task = asyncio.create_task(_loop(), name="api-health-monitor")


async def stop_health_monitor() -> None:
    global _monitor_task, _monitor_stop
    if _monitor_stop is not None:
        _monitor_stop.set()
    if _monitor_task is not None:
        try:
            await asyncio.wait_for(_monitor_task, timeout=5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            _monitor_task.cancel()
    _monitor_task = None
    _monitor_stop = None


async def cluster_summary() -> dict[str, Any]:
    """Counts and averages for the admin dashboard."""
    apis = await list_apis()
    online = [a for a in apis if a.get("status") == "online"]
    slow = [a for a in apis if a.get("status") == "slow"]
    cpms = [float(a.get("avg_cpm", 0.0)) for a in online if float(a.get("avg_cpm", 0.0)) > 0]
    return {
        "total": len(apis),
        "online": len(online),
        "slow": len(slow),
        "offline": len(apis) - len(online) - len(slow),
        "active_jobs": sum(int(a.get("active_jobs", 0)) for a in apis),
        "avg_cpm": (sum(cpms) / len(cpms)) if cpms else 0.0,
        "all_online": bool(apis) and len(online) + len(slow) == len(apis),
    }


# ── load balancer ────────────────────────────────────────────────────
class APILoadBalancer:
    """Picks the best instance for a new job and tracks its load.

    Order of concern: healthy → least active jobs → priority → CPM.
    """

    async def select_api(self) -> dict[str, Any] | None:
        global _ROUND_ROBIN_INDEX
        apis = await list_apis(active_only=True)
        online = [a for a in apis if a.get("status") in ("online", "slow")]
        if not online:
            return None

        mode = str(await database.get_config("api_rotation_mode", "round_robin"))

        if mode == "priority":
            return sorted(
                online,
                key=lambda a: (
                    int(a.get("priority", 99)),
                    int(a.get("active_jobs", 0)),
                    -float(a.get("avg_cpm", 0.0)),
                ),
            )[0]

        # round_robin / random both respect the least-loaded tier so a busy
        # instance is never picked while an idle one exists.
        online.sort(key=lambda a: (int(a.get("active_jobs", 0)), int(a.get("priority", 99))))
        min_load = int(online[0].get("active_jobs", 0))
        tier = [a for a in online if int(a.get("active_jobs", 0)) == min_load]

        if mode == "random":
            return random.choice(tier)

        picked = tier[_ROUND_ROBIN_INDEX % len(tier)]
        _ROUND_ROBIN_INDEX = (_ROUND_ROBIN_INDEX + 1) % 1_000_000
        return picked

    async def acquire(self, api_id: str) -> None:
        """Reserve one job slot (atomic increment)."""
        await database.apis().update_one({"api_id": api_id}, {"$inc": {"active_jobs": 1}})

    async def release(self, api_id: str, success: bool, final_cpm: float = 0.0) -> None:
        """Return a slot and fold the run into the instance statistics.

        Called from the check loop's finally block, so every exit path —
        done, cancelled, failed or crashed — returns its slot.
        """
        await database.apis().update_one(
            {"api_id": api_id},
            {
                "$inc": {
                    "active_jobs": -1,
                    "total_requests": 1,
                    "total_success": 1 if success else 0,
                    "total_failed": 0 if success else 1,
                },
                "$push": {
                    "recent_cpm_samples": {
                        "$each": [round(float(final_cpm or 0.0), 2)],
                        "$slice": -100,
                    }
                },
            },
        )

        api = await get_api(api_id)
        if api is None:
            return

        samples = [float(v) for v in api.get("recent_cpm_samples", []) if float(v) > 0]
        patch: dict[str, Any] = {"active_jobs": max(0, int(api.get("active_jobs", 0)))}
        if samples:
            patch["avg_cpm"] = round(sum(samples) / len(samples), 2)
        await database.apis().update_one({"api_id": api_id}, {"$set": patch})


balancer = APILoadBalancer()

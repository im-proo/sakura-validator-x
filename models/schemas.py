"""Data shapes shared by services, handlers and the renderer.

MongoDB documents are plain dicts (motor returns dicts), so these
dataclasses exist to give defaults and type clarity for *new* records
and for expensive-to-recompute values. Nothing here performs I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

# ── Plan matrix (Section 6) ──────────────────────────────────────────
PLAN_LIMITS: dict[str, dict[str, Any]] = {
    "free": {
        "daily_attempts": 5,
        "max_cookies": 1_000,
        "concurrent": 1,
        "label": "Free",
    },
    "basic": {
        "daily_attempts": 10,
        "max_cookies": 10_000,
        "concurrent": 2,
        "label": "Basic",
    },
    "premium": {
        "daily_attempts": 20,
        "max_cookies": 100_000,
        "concurrent": 3,
        "label": "Premium",
    },
    "sakura": {
        # Section 6: unlimited attempts. None means "no cap" everywhere.
        "daily_attempts": None,
        "max_cookies": 500_000,
        "concurrent": 5,
        "label": "Sakura",
    },
}

PLAN_ORDER: tuple[str, ...] = ("free", "basic", "premium", "sakura")


def plan_limits(plan: str) -> dict[str, Any]:
    """Limits for a plan slug, falling back to free for unknown values."""
    return PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])


def daily_limit(plan: str) -> int | None:
    """Attempts per day, or None when the plan is unlimited."""
    return plan_limits(plan)["daily_attempts"]


def max_cookies(plan: str) -> int:
    """Largest cookie file a plan may submit in one attempt."""
    return int(plan_limits(plan)["max_cookies"])


def concurrency(plan: str) -> int:
    """Concurrent sessions a plan may run."""
    return int(plan_limits(plan)["concurrent"])


def utcnow() -> datetime:
    """Timezone-aware UTC now — every stored timestamp uses this."""
    return datetime.now(timezone.utc)


def next_midnight_utc(now: datetime | None = None) -> datetime:
    """The next 00:00 UTC, used for the daily reset timestamp."""
    now = now or utcnow()
    tomorrow = (now + timedelta(days=1)).date()
    return datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=timezone.utc)


def default_daily_reset() -> datetime:
    """Reset marker handed to brand new users."""
    return next_midnight_utc()


def new_user_document(user_id: int, username: str | None, full_name: str) -> dict[str, Any]:
    """The document inserted on a user's very first /start."""
    now = utcnow()
    return {
        "user_id": int(user_id),
        "username": username or "",
        "full_name": full_name or "",
        "joined_at": now,
        "last_active": now,
        "is_banned": False,
        "ban_reason": "",
        "plan": "free",
        "plan_expires": None,
        "daily_attempts_used": 0,
        "daily_reset_at": default_daily_reset(),
        "total_checks": 0,
        "total_hits": 0,
        "total_premium_hits": 0,
        "leaderboard_points": 0,
    }


@dataclass(slots=True)
class SessionStats:
    """Live counters for one running check (memory only, never stored)."""

    job_id: str = ""
    total: int = 0
    checked: int = 0
    hits: int = 0
    premium: int = 0
    free: int = 0
    bad: int = 0
    errors: int = 0
    hold: int = 0
    cpm: float = 0.0
    started_at: datetime = field(default_factory=utcnow)
    finished_at: datetime | None = None
    status: str = "running"
    sent_hits: set[str] = field(default_factory=set)

    @property
    def elapsed_seconds(self) -> int:
        end = self.finished_at or utcnow()
        return max(0, int((end - self.started_at).total_seconds()))

    @property
    def elapsed_label(self) -> str:
        """Compact duration: 1m 04s / 2h 11m / 8s."""
        secs = self.elapsed_seconds
        if secs >= 3600:
            return f"{secs // 3600}h {(secs % 3600) // 60:02d}m"
        if secs >= 60:
            return f"{secs // 60}m {secs % 60:02d}s"
        return f"{secs}s"

    @property
    def percent(self) -> int:
        if self.total <= 0:
            return 0
        return min(100, int(100 * self.checked / self.total))


def hit_identity(hit: dict[str, Any]) -> str:
    """Stable key so one account is never delivered twice."""
    account = hit.get("account") or {}
    email = str(account.get("email") or "").strip().lower()
    if email:
        return email
    guid = str(account.get("user_guid") or "").strip()
    if guid:
        return f"guid:{guid}"
    return f"cookie:{str(hit.get('cookie') or '')[:64]}"


def is_premium_hit(hit: dict[str, Any]) -> bool:
    """A hit counts as premium when the account's plan is not the ad tier."""
    account = hit.get("account") or {}
    if account.get("is_premium") is True:
        return True
    plan = str(account.get("plan") or "").strip().lower()
    return "premium" in plan or "ultimate" in plan

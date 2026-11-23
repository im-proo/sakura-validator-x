"""Data shapes shared by services, handlers and the renderer.

MongoDB documents are plain dicts (motor returns dicts), so these
dataclasses exist to give defaults and type clarity for *new* records
and for expensive-to-recompute values. Nothing here performs I/O.
"""

from __future__ import annotations

import unicodedata
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
    # Valid sessions the checker reported, before the premium split.
    api_hits: int = 0
    # Hits the bot delivers: premium accounts only. A free / basic / ads
    # account is a valid session, not a hit, so it is counted in `free`.
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
    # Identities of every valid session classified so far. A hit is counted
    # the moment it is fetched, not when its card is sent, so the live card
    # never reports a Premium account as free while the message is in flight.
    seen_hits: set[str] = field(default_factory=set)
    # Identities whose card Telegram accepted (delivery dedupe).
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


def truthy(value: Any) -> bool:
    """True for real booleans and for the API's "yes"/"true"/"1" strings."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in ("yes", "true", "1", "y", "on")


def cookie_text(hit: dict[str, Any]) -> str:
    """The raw cookie line, whether the API sends a string or a CookieSet."""
    cookie = hit.get("cookie")
    if isinstance(cookie, str):
        return cookie.strip()
    if isinstance(cookie, dict):
        raw = str(cookie.get("raw") or "").strip()
        if raw:
            return raw
        parts: list[str] = []
        if cookie.get("netflix_id"):
            parts.append(f"NetflixId={cookie['netflix_id']}")
        if cookie.get("secure_netflix_id"):
            parts.append(f"SecureNetflixId={cookie['secure_netflix_id']}")
        if cookie.get("nfvdid"):
            parts.append(f"nfvdid={cookie['nfvdid']}")
        return "; ".join(parts)
    return str(cookie or "").strip()


def remaining_label(account: dict[str, Any]) -> str:
    """Validity left: the API's hours/minutes split, or a ready-made label."""
    ready = str(account.get("remaining") or "").strip()
    if ready:
        return ready
    hours = account.get("remaining_hours")
    minutes = account.get("remaining_minutes")
    if hours is None and minutes is None:
        return ""
    whole_hours = int(hours or 0)
    whole_minutes = int(minutes or 0)
    if whole_hours <= 0 and whole_minutes <= 0:
        return "0m"
    return f"{whole_hours}h {whole_minutes:02d}m"


def hit_identity(hit: dict[str, Any]) -> str:
    """Stable key so one account is never delivered twice."""
    account = hit.get("account") or {}
    email = str(account.get("email") or "").strip().lower()
    if email:
        return email
    guid = str(account.get("user_guid") or "").strip()
    if guid:
        return f"guid:{guid}"
    return f"cookie:{cookie_text(hit)[:64]}"


# ── plan-name normalisation ─────────────────────────────────────────
# Netflix names its plans in the viewer's language, so the same tier arrives
# as "Standard", "Padrão", "Estándar" or "スタンダード". Matching the English
# words alone leaves every other locale unclassified (the Go API reports
# `plan_type: unknown` for them). These token tables fold a localised name
# into a plan family and flag the ad-supported variants.
PLAN_FAMILY_TOKENS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "premium",
        (
            "premium", "premiun", "premijum", "ultimate", "премиум", "преміум",
            "премијум", "بريميوم", "پرمیوم", "プレミアム", "프리미엄", "高级",
            "高級", "פרימיום",
            # Turkish and Vietnamese keep their own words for Premium, and
            # Hindi / Thai use native scripts rather than a transliteration.
            "ozel", "cao cap", "प्रीमियम", "พรีเมียม",
        ),
    ),
    (
        "standard",
        (
            "standard", "standart", "standar", "standardowy", "padrão", "padrao",
            "estándar", "estandar", "standaard", "стандарт",
            "スタンダード", "스탠다드", "标准", "標準",
            # Vietnamese / Hebrew / Arabic / Hindi / Thai localise it too.
            "tieu chuan", "סטנדרטי", "قياسي", "मानक", "มาตรฐาน",
        ),
    ),
    (
        "basic",
        (
            "basic", "basique", "básico", "basico", "basis", "podstawowy",
            "базовый", "ベーシック", "기본", "基础", "基礎",
            # Indonesian, Turkish, Vietnamese, Hebrew, Arabic, Hindi, Thai,
            # Hungarian, Czech and Finnish each use their own word.
            "dasar", "temel", "co ban", "בסיסי", "أساسي", "बेसिक",
            "พื้นฐาน", "alap", "zakladni", "perus",
        ),
    ),
)

ADS_PLAN_TOKENS: tuple[str, ...] = (
    "ads", "ad-supported", "anúncio", "anuncio", "anoncio", "publicidad",
    # "publicit" covers the Italian "pubblicità" and French "publicité".
    "pubblicit", "publicit", "werbung", "avec pub", "annonce", "met reclame",
    "reklam", "iklan", "annonser", "annoncer", "oglas", "hirdet",
    "광고", "広告", "广告", "廣告", "реклам", "إعلان", "إعلانات",
    "פרסומות", "โฆษณา",
)


def fold_plan_name(plan: str) -> str:
    """Lower-case a plan name and strip accents.

    Netflix localises the same tier with a diacritic in some markets —
    Hungarian "Prémium", Portuguese "Padrão" — so matching on the raw text
    treats those as unknown. Folding makes "Prémium" match "premium" and
    "cao cấp" match "cao cap".
    """
    text = unicodedata.normalize("NFKD", str(plan or "").strip().lower())
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def plan_family(plan: str) -> str:
    """Map a localised plan name to "premium" / "standard" / "basic".

    Unknown or empty names return "", so callers can fall back to the API's
    machine-readable fields.
    """
    text = fold_plan_name(plan)
    if not text:
        return ""
    for family, tokens in PLAN_FAMILY_TOKENS:
        # Tokens are folded the same way so a script that NFKD decomposes
        # (Devanagari "प्रीमियम") still matches the folded plan name.
        if any(fold_plan_name(token) in text for token in tokens):
            return family
    return ""


def is_ads_plan(plan: str) -> bool:
    """True for the ad-supported tier in any language."""
    text = fold_plan_name(plan)
    return any(fold_plan_name(token) in text for token in ADS_PLAN_TOKENS)


# Values the Go API writes when a field is missing from the membership page.
_UNKNOWN_TEXTS: frozenset[str] = frozenset(
    {"unknown", "n/a", "na", "null", "undefined", "-", "--", "—"}
)


def known_text(raw: Any, fallback: str = "—") -> str:
    """A field value, or the fallback when the checker could not read it.

    The API spells every missing value "Unknown" whatever the locale, so the
    card would otherwise show that word instead of a neutral dash.
    """
    text = str(raw if raw is not None else "").strip()
    if not text or text.lower() in _UNKNOWN_TEXTS:
        return fallback
    return text


def plan_display(account: dict[str, Any], fallback: str = "—") -> str:
    """Plan label for the card, in any language or currency.

    The localised display name wins; when the page exposes none, the English
    ``plan_code`` / ``plan_type`` the checker also extracts is used, so a
    market whose plan wording is unfamiliar still shows a real tier.
    """
    for key in ("plan", "plan_code", "plan_type"):
        text = known_text(account.get(key), "")
        if text:
            return text
    return fallback


def price_label(account: dict[str, Any], fallback: str = "—") -> str:
    """Price with its currency, whatever the market's currency is.

    Netflix renders the amount with a local symbol ("₺149,99") in most
    locales and with a bare number plus a currency code in others, so the
    code is appended only when the amount does not already carry it.
    """
    price = known_text(account.get("price"), "")
    currency = known_text(account.get("currency"), "")
    if price and currency and currency.upper() not in price.upper():
        return f"{price} {currency}"
    return price or currency or fallback


def is_premium_hit(hit: dict[str, Any]) -> bool:
    """True only for a live Premium plan.

    A cookie that still opens the membership page for a closed account
    reports no plan at all, and basic / standard / ads tiers are valid but
    not premium. The Go API reports a display `plan` ("Premium"), a machine
    `plan_type` ("premium"/"standard_with_ads"), `is_ads_tier` and
    `membership_status`, so all of them are checked. The display name is
    normalised first, so "Premium" in any locale still counts.
    """
    account = hit.get("account") or {}
    status = str(account.get("membership_status") or "").strip().upper()
    if status in ("FORMER_MEMBER", "NEVER_MEMBER"):
        return False
    if account.get("is_premium") is True:
        return True
    plan = str(account.get("plan") or "")
    # The machine-readable plan code is locale independent, so it still names
    # the tier when the localised display name uses unfamiliar wording.
    plan_code = str(account.get("plan_code") or "")
    plan_type = str(account.get("plan_type") or "").strip().lower()
    if truthy(account.get("is_ads_tier")) or is_ads_plan(plan) or is_ads_plan(plan_code):
        return False
    if plan_family(plan) == "premium" or plan_family(plan_code) == "premium":
        return True
    return plan_type in ("premium", "ultimate")

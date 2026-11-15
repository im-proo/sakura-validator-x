"""Cookie file intake and hit artefacts.

The Go checker accepts .txt/.json/.zip/.rar itself, so the bot only has to
download the upload, sanity-check the format and estimate the cookie count
to enforce plan limits. Cookie bytes never touch MongoDB.
"""

from __future__ import annotations

import logging
import os
import uuid
import zipfile
from typing import Any

import aiofiles

import config
from ui.typography import BOT_NAME_DISPLAY, BRAND_FOOTER

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = ("txt", "json", "zip", "rar")
NETFLIX_MARKER = ".netflix.com"
COOKIE_LINES_PER_SET = 4
ZIP_SCAN_LIMIT = 20 * 1024 * 1024  # read at most 20 MB when counting inside a zip


class InvalidFileError(Exception):
    """Unsupported/corrupt upload. The attempt must NOT be deducted."""


class PlanLimitError(Exception):
    """Upload is larger than the plan allows."""

    def __init__(self, message: str, estimated: int, limit: int) -> None:
        super().__init__(message)
        self.estimated = estimated
        self.limit = limit


def temp_dir() -> str:
    """Working directory for uploads and result files."""
    path = config.settings.temp_dir or "/tmp/sakura"
    os.makedirs(path, exist_ok=True)
    return path


def extension_of(file_name: str) -> str:
    """Last dot segment, lowercased. Empty when there is no extension."""
    return str(file_name or "").rsplit(".", 1)[-1].lower() if "." in str(file_name or "") else ""


def safe_temp_path(file_name: str) -> str:
    """Unique local path that keeps the original name for the API."""
    stem = os.path.basename(str(file_name or "cookies")).replace("/", "_").replace("\\", "_")
    return os.path.join(temp_dir(), f"sakura_{uuid.uuid4().hex[:12]}_{stem}")


def estimate_cookie_count(path: str, ext: str) -> int:
    """Best-effort count used only for the plan limit check."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return 1
    if size == 0:
        raise InvalidFileError("file is empty")

    if ext == "txt":
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            content = handle.read()
        occurrences = content.count(NETFLIX_MARKER)
        if occurrences == 0:
            # Not a Netflix export: fall back to non-empty line count.
            lines = [ln for ln in content.splitlines() if ln.strip()]
            if not lines:
                raise InvalidFileError("file contains no cookies")
            return max(1, len(lines))
        return max(1, occurrences // COOKIE_LINES_PER_SET)

    if ext == "json":
        # The API parses JSON itself; only the size is needed for the limit.
        return max(1, size // 1_000)

    if ext == "zip":
        try:
            with zipfile.ZipFile(path) as archive:
                total_marker = 0
                scanned = 0
                for info in archive.infolist():
                    if info.is_dir() or not info.filename.lower().endswith((".txt", ".json")):
                        continue
                    if scanned >= ZIP_SCAN_LIMIT:
                        break
                    with archive.open(info) as member:
                        chunk = member.read()
                    scanned += len(chunk)
                    total_marker += chunk.count(NETFLIX_MARKER.encode())
                if total_marker:
                    return max(1, total_marker // COOKIE_LINES_PER_SET)
        except zipfile.BadZipFile as exc:
            raise InvalidFileError("zip archive looks broken") from exc
        return max(1, size // 800)

    # .rar: no stdlib reader; estimate from compressed size.
    return max(1, size // 800)


async def handle_user_cookie_file(document: Any, bot: Any, plan_max: int) -> dict[str, Any]:
    """Download a Telegram document and validate it against the plan limit.

    Raises InvalidFileError / PlanLimitError without deducting an attempt.
    """
    file_name = str(getattr(document, "file_name", "") or "cookies.txt")
    ext = extension_of(file_name)
    if ext not in ALLOWED_EXTENSIONS:
        raise InvalidFileError(
            "only .txt, .json, .zip and .rar files are accepted"
        )

    file_size = int(getattr(document, "file_size", 0) or 0)
    if file_size > 50 * 1024 * 1024:
        raise InvalidFileError("file is larger than 50 MB")

    local_path = safe_temp_path(file_name)
    try:
        remote = await bot.get_file(document.file_id)
        await bot.download_file(remote.file_path, destination=local_path)
    except Exception as exc:
        logger.error("download failed for %s: %s", file_name, exc)
        _silent_remove(local_path)
        raise InvalidFileError("could not download the file, please resend it") from exc

    try:
        estimated = estimate_cookie_count(local_path, ext)
    except InvalidFileError:
        _silent_remove(local_path)
        raise

    if estimated > plan_max:
        _silent_remove(local_path)
        raise PlanLimitError(
            f"estimated {estimated:,} cookies exceeds the {plan_max:,} cookie limit",
            estimated=estimated,
            limit=plan_max,
        )

    return {
        "file_path": local_path,
        "file_name": file_name,
        "extension": ext,
        "estimated_count": estimated,
        "file_size": file_size,
    }


def _value(account: dict[str, Any], key: str, fallback: str) -> str:
    value = account.get(key)
    if value is None or str(value).strip() == "" or str(value).strip().lower() == "none":
        return fallback
    return str(value).strip()


def build_hit_text(hit: dict[str, Any]) -> str:
    """One hit as the buyer-facing .txt payload (Section 9.6 format)."""
    account = hit.get("account") or {}
    profiles = account.get("profiles") or []
    profile_text = ", ".join(str(p) for p in profiles) if profiles else "—"
    hold = bool(account.get("is_on_hold")) or str(account.get("hold_status", "")).lower() in (
        "yes",
        "true",
    )
    extra = str(account.get("extra_member", "")).lower() in ("yes", "true")
    verified = bool(account.get("email_verified"))

    lines = [
        "✅ NETFLIX HIT",
        "=" * 40,
        f"Name: {_value(account, 'name', '—')}",
        f"Email: {_value(account, 'email', '—')}",
        f"Country: {_value(account, 'country', '—')} {account.get('country_flag', '')}".rstrip(),
        f"Plan: {_value(account, 'plan', '—')}",
        f"Member Since: {_value(account, 'member_since', '—')}",
        f"Next Billing: {_value(account, 'next_billing', '—')}",
        f"Payment: {_value(account, 'payment', '—')}",
        f"Card: {_value(account, 'card', '—')}",
        f"Phone: {_value(account, 'phone', '—')}",
        f"Quality: {_value(account, 'video_quality', _value(account, 'quality', '—'))}",
        f"Streams: {_value(account, 'streams', '—')}",
        f"Price: {_value(account, 'price', '—')}",
        f"Hold Status: {'Yes' if hold else 'No'}",
        f"Extra Member: {'Yes' if extra else 'No'}",
        f"Email Verified: {'Yes' if verified else 'No'}",
        f"Membership Status: {_value(account, 'membership_status', '—')}",
        f"Profiles: {profile_text}",
        f"User GUID: {_value(account, 'user_guid', '—')}",
        f"Valid Till (UTC): {_value(account, 'valid_till_utc', _value(account, 'valid_till', '—'))}"
        f" (Remaining: {_value(account, 'remaining', '—')})",
        "=" * 40,
        "",
        "🍪 Netflix COOKIE:",
        str(hit.get("cookie") or "").strip(),
        "",
    ]
    return "\n".join(lines)


def hit_file_name(hit: dict[str, Any]) -> str:
    """Filename for a single hit document."""
    account = hit.get("account") or {}
    email = str(account.get("email") or "hit").strip().replace("/", "_")
    return f"sakura_hit_{email}.txt"


def bundle_file_names(kind: str) -> str:
    """Fixed names used for the end-of-run deliverables."""
    if kind == "zip":
        return "𝐒𝐚𝐤𝐮𝐫𝐚_Premium_Hits.zip"
    return "𝐒𝐚𝐤𝐮𝐫𝐚_Premium_Hits.txt"


async def build_hits_bundle(hits: list[dict[str, Any]], token: str | None = None) -> dict[str, Any]:
    """Write the combined .txt and a .zip of per-account files.

    Returns {"txt": path|None, "zip": path|None, "count": int}.
    """
    if not hits:
        return {"txt": None, "zip": None, "count": 0}

    token = token or uuid.uuid4().hex[:10]
    base = temp_dir()
    txt_path = os.path.join(base, f"hits_{token}.txt")
    zip_path = os.path.join(base, f"hits_{token}.zip")

    header = (
        f"{BOT_NAME_DISPLAY}\n"
        f"{BRAND_FOOTER}\n"
        f"Hits: {len(hits)}\n"
        + "=" * 40
        + "\n\n"
    )
    combined = header + "\n".join(build_hit_text(hit) for hit in hits)

    async with aiofiles.open(txt_path, "w", encoding="utf-8") as handle:
        await handle.write(combined)

    used_names: set[str] = set()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(bundle_file_names("txt"), combined)
        for hit in hits:
            name = hit_file_name(hit)
            if name in used_names:
                stem, dot, ext = name.rpartition(".")
                name = f"{stem}_{len(used_names)}{dot}{ext}"
            used_names.add(name)
            archive.writestr(name, build_hit_text(hit))

    return {"txt": txt_path, "zip": zip_path, "count": len(hits)}


def _silent_remove(path: str) -> None:
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def cleanup(*paths: str | None) -> None:
    """Remove temporary artefacts. Never raises."""
    for path in paths:
        if path:
            _silent_remove(path)


def purge_stale_files(max_age_seconds: int = 3_600) -> int:
    """Delete leftovers from previous runs older than an hour."""
    import time

    removed = 0
    cutoff = time.time() - max_age_seconds
    try:
        for entry in os.listdir(temp_dir()):
            full = os.path.join(temp_dir(), entry)
            if os.path.isfile(full) and os.path.getmtime(full) < cutoff:
                _silent_remove(full)
                removed += 1
    except OSError as exc:  # pragma: no cover - best effort
        logger.warning("temp purge failed: %s", exc)
    return removed

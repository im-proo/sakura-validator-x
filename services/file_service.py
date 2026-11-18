"""Cookie file intake and hit artefacts.

The Go checker accepts .txt/.json/.zip/.rar itself, so the bot only has to
download the upload, sniff the container type and estimate the cookie count
to enforce plan limits. Cookie bytes never touch MongoDB.

The pre-flight is deliberately conservative: it only rejects an upload when
it is *certain* there is nothing to check (an empty file, or plain text that
mentions Netflix nowhere). Anything wrapped, renamed or unreadable is handed
to the checker, whose magic-byte sniffing knows more about archives than we
do. A false "no cookies" here would reject a good file before the API ever
sees it.
"""

from __future__ import annotations

import logging
import os
import re
import uuid
import zipfile
from typing import Any

import aiofiles

import config
from models.schemas import cookie_text, remaining_label, truthy
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


NETFLIX_ID_RE = re.compile(rb"(?<!Secure)NetflixId", re.IGNORECASE)
NETFLIX_WORD_RE = re.compile(rb"netflix", re.IGNORECASE)
NETFLIX_MARKER_BYTES = NETFLIX_MARKER.encode()

# Magic bytes of every container the Go checker can unpack itself.
ARCHIVE_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"PK\x03\x04", "zip"),
    (b"PK\x05\x06", "zip"),
    (b"PK\x07\x08", "zip"),
    (b"Rar!\x1a\x07", "rar"),
    (b"\x1f\x8b", "gzip"),
    (b"7z\xbc\xaf\x27\x1c", "7z"),
    (b"BZh", "bzip2"),
)
OPAQUE_KINDS = ("rar", "gzip", "7z", "bzip2")


def sniff_kind(header: bytes) -> str:
    """Container type from magic bytes — the test the Go extractor uses."""
    for magic, kind in ARCHIVE_MAGIC:
        if header.startswith(magic):
            return kind
    return ""


def resolve_kind(path: str, ext: str) -> str:
    """Real container type: trust the bytes first, then the extension."""
    try:
        with open(path, "rb") as handle:
            header = handle.read(16)
    except OSError:
        return ext
    return sniff_kind(header) or ext


def is_text_bytes(data: bytes) -> bool:
    """False for binary payloads (NUL bytes or mostly non-printable data)."""
    if b"\x00" in data:
        return False
    sample = data[:4096]
    if not sample:
        return True
    printable = sum(1 for byte in sample if 32 <= byte < 127 or byte in (9, 10, 13))
    return printable / len(sample) > 0.85


def _count_in_text(data: bytes) -> int:
    """One NetflixId per cookie set; SecureNetflixId must not be counted."""
    return len(NETFLIX_ID_RE.findall(data))


def _count_in_zip(path: str) -> int | None:
    """Cookie sets inside a readable zip, or None when it cannot be judged."""
    try:
        with zipfile.ZipFile(path) as archive:
            total = 0
            for info in archive.infolist():
                if info.is_dir():
                    continue
                try:
                    with archive.open(info) as member:
                        chunk = member.read(ZIP_SCAN_LIMIT)
                except (RuntimeError, zipfile.BadZipFile, OSError):
                    # Encrypted or damaged member: the API may still read it.
                    return None
                if not is_text_bytes(chunk):
                    # Nested archive / binary blob — leave it to the checker.
                    return None
                total += _count_in_text(chunk)
            return total
    except (zipfile.BadZipFile, OSError):
        return None


def count_netflix_cookies(path: str, ext: str) -> int | None:
    """Cookie sets in the upload, or None when only the API can tell.

    0 is returned only when the file is plain text we could read in full and
    it contains no NetflixId *and* no mention of Netflix at all. Every other
    uncertain case — renamed zip, nested archive, rar/gzip/7z, binary or
    unreadable payload — returns None and is submitted as-is, because the Go
    extractor sniffs magic bytes and supports layouts this module does not.
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    if size == 0:
        return 0

    kind = resolve_kind(path, ext)
    if kind == "zip":
        return _count_in_zip(path)
    if kind in OPAQUE_KINDS:
        return None

    try:
        with open(path, "rb") as handle:
            data = handle.read(ZIP_SCAN_LIMIT)
    except OSError:
        return None
    if not is_text_bytes(data):
        return None

    found = _count_in_text(data)
    if found:
        return found
    # No “NetflixId” but the file talks about Netflix: some layout we do not
    # model (escaped keys, wrapped values) — the checker decides.
    if NETFLIX_WORD_RE.search(data):
        return None
    return 0


def estimate_cookie_count(path: str, ext: str) -> int:
    """Best-effort count used only for the plan limit check.

    Never rejects an unreadable or renamed archive: a broken zip still gets
    a size-based estimate so the API can sniff it and answer for itself.
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return 1
    if size == 0:
        raise InvalidFileError("file is empty")

    kind = resolve_kind(path, ext)

    if kind == "zip":
        try:
            with zipfile.ZipFile(path) as archive:
                total_marker = 0
                scanned = 0
                for info in archive.infolist():
                    if info.is_dir() or not info.filename.lower().endswith(
                        (".txt", ".json", ".csv")
                    ):
                        continue
                    if scanned >= ZIP_SCAN_LIMIT:
                        break
                    with archive.open(info) as member:
                        chunk = member.read()
                    scanned += len(chunk)
                    total_marker += chunk.count(NETFLIX_MARKER_BYTES)
                if total_marker:
                    return max(1, total_marker // COOKIE_LINES_PER_SET)
        except (zipfile.BadZipFile, OSError):
            # Renamed or damaged archive: fall through to a size estimate.
            pass
        return max(1, size // 800)

    if kind in OPAQUE_KINDS:
        # No stdlib reader for these; compressed size is all we have.
        return max(1, size // 800)

    if kind == "json":
        # The API parses JSON itself; only the size is needed for the limit.
        return max(1, size // 1_000)

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


async def handle_user_cookie_file(document: Any, bot: Any, plan_max: int) -> dict[str, Any]:
    """Download a Telegram document and validate it against the plan limit.

    Raises InvalidFileError / PlanLimitError without deducting an attempt.
    """
    file_name = str(getattr(document, "file_name", "") or "cookies.txt")
    ext = extension_of(file_name)

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

    # The extension is a hint, never a verdict: the checker sniffs the bytes.
    kind = resolve_kind(local_path, ext)
    if ext not in ALLOWED_EXTENSIONS and kind not in (
        "txt", "json", "zip", "rar", *OPAQUE_KINDS
    ):
        _silent_remove(local_path)
        raise InvalidFileError("only .txt, .json, .zip and .rar files are accepted")

    try:
        estimated = estimate_cookie_count(local_path, ext)
    except InvalidFileError:
        _silent_remove(local_path)
        raise
    except OSError as exc:
        _silent_remove(local_path)
        raise InvalidFileError("could not read the file, please resend it") from exc

    found = count_netflix_cookies(local_path, ext)
    if found == 0:
        # Certain: plain text that never mentions Netflix.
        _silent_remove(local_path)
        raise InvalidFileError(
            "this file has no Netflix cookies inside it - export the cookies "
            "again (.txt, or a .zip of .txt files) and send it"
        )
    if found:
        estimated = max(estimated, found)

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
        "extension": kind,
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
    hold = truthy(account.get("is_on_hold")) or truthy(account.get("hold_status"))
    extra = truthy(account.get("extra_member"))
    verified = truthy(account.get("email_verified"))

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
        f" (Remaining: {_value(account, 'remaining', remaining_label(account) or '—')})",
        "=" * 40,
        "",
        "🍪 Netflix COOKIE:",
        cookie_text(hit),
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
        return "premium_accounts.zip"
    return "premium_accounts.txt"


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

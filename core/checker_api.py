"""HTTP client for the Go checker instances (Section 10, job-based API).

The API is NOT streaming: a job is submitted, its status is polled, hits
are fetched page by page, and the job can be cancelled. No auth is used.

All calls accept the bare instance URL (e.g. https://nf-01.example.com)
and append the documented paths internally.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiofiles
import aiohttp

import config

logger = logging.getLogger(__name__)

API_PREFIX = "/api/v1"

_session: aiohttp.ClientSession | None = None
_session_loop: asyncio.AbstractEventLoop | None = None


class CheckerAPIError(Exception):
    """Raised when a checker instance returns an unusable response."""


class CheckerUnavailable(CheckerAPIError):
    """Raised when the instance cannot be reached at all."""


def _get_session() -> aiohttp.ClientSession:
    """Shared session, tied to the loop that created it.

    aiohttp sessions cannot cross event loops, so a new loop (tests, a
    restarted poller) transparently gets a fresh session.
    """
    global _session, _session_loop
    try:
        loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
    except RuntimeError:  # pragma: no cover - called outside a loop
        loop = None
    if _session is None or _session.closed or _session_loop is not loop:
        _session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=None),
            connector=aiohttp.TCPConnector(limit=64, ttl_dns_cache=300),
        )
        _session_loop = loop
    return _session


async def close_session() -> None:
    """Close the shared session during shutdown."""
    global _session, _session_loop
    if _session is not None and not _session.closed:
        await _session.close()
    _session = None
    _session_loop = None


def _base(api_url: str) -> str:
    """Normalise an instance URL: no trailing slash, no duplicated prefix."""
    url = (api_url or "").strip().rstrip("/")
    if url.endswith(API_PREFIX):
        url = url[: -len(API_PREFIX)]
    return url


def _short(text: str, limit: int = 200) -> str:
    text = " ".join(str(text).split())
    return text[:limit]


async def _read_json(resp: aiohttp.ClientResponse) -> dict[str, Any]:
    """Decode a JSON body, turning garbage into a typed error."""
    raw = await resp.text()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CheckerAPIError(f"invalid JSON from checker ({resp.status}): {_short(raw)}") from exc
    if not isinstance(data, dict):
        raise CheckerAPIError(f"unexpected checker payload: {_short(raw)}")
    return data


# ── jobs ─────────────────────────────────────────────────────────────
async def submit_job_file(
    api_url: str,
    file_path: str,
    file_name: str,
    concurrency: int = 400,
    rate_per_minute: int = 0,
) -> dict[str, Any]:
    """POST /api/v1/jobs as multipart. The API extracts cookies itself.

    Supports .txt, .json, .zip and .rar uploads, so the bot never has to
    unzip or parse the user's file.
    """
    async with aiofiles.open(file_path, "rb") as handle:
        payload = await handle.read()

    form = aiohttp.FormData()
    form.add_field(
        "file",
        payload,
        filename=file_name,
        content_type="application/octet-stream",
    )
    form.add_field("concurrency", str(int(concurrency)))
    form.add_field("rate_per_minute", str(int(rate_per_minute)))
    form.add_field("retry_on_proxy", "true")

    try:
        async with _get_session().post(
            f"{_base(api_url)}{API_PREFIX}/jobs",
            data=form,
            timeout=aiohttp.ClientTimeout(total=120),
        ) as resp:
            body = await _read_json(resp)
            if resp.status == 400:
                raise CheckerAPIError(_short(body.get("error") or "job rejected by checker"))
            if resp.status not in (200, 201, 202):
                raise CheckerAPIError(
                    f"checker returned {resp.status}: {_short(body.get('error') or body)}"
                )
            if not body.get("job_id"):
                raise CheckerAPIError("checker did not return a job id")
            return body
    except asyncio.TimeoutError as exc:
        raise CheckerUnavailable("checker timed out accepting the job") from exc
    except aiohttp.ClientError as exc:
        raise CheckerUnavailable(_short(str(exc))) from exc


async def submit_job_json(
    api_url: str,
    cookies: list[str],
    concurrency: int = 400,
    rate_per_minute: int = 0,
) -> dict[str, Any]:
    """POST /api/v1/jobs as JSON when cookies are already split."""
    payload = {
        "cookies": list(cookies),
        "concurrency": int(concurrency),
        "rate_per_minute": int(rate_per_minute),
        "retry_on_proxy": True,
    }
    try:
        async with _get_session().post(
            f"{_base(api_url)}{API_PREFIX}/jobs",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=120),
        ) as resp:
            body = await _read_json(resp)
            if resp.status not in (200, 201, 202):
                raise CheckerAPIError(
                    f"checker returned {resp.status}: {_short(body.get('error') or body)}"
                )
            return body
    except asyncio.TimeoutError as exc:
        raise CheckerUnavailable("checker timed out accepting the job") from exc
    except aiohttp.ClientError as exc:
        raise CheckerUnavailable(_short(str(exc))) from exc


async def get_job_status(api_url: str, job_id: str) -> dict[str, Any]:
    """GET /api/v1/jobs/{id} — polled every 3 seconds by the check loop."""
    try:
        async with _get_session().get(
            f"{_base(api_url)}{API_PREFIX}/jobs/{job_id}",
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            body = await _read_json(resp)
            if resp.status == 404:
                raise CheckerAPIError("job not found on this checker")
            if resp.status != 200:
                raise CheckerAPIError(f"status {resp.status}: {_short(body)}")
            return body
    except asyncio.TimeoutError as exc:
        raise CheckerUnavailable("checker status timeout") from exc
    except aiohttp.ClientError as exc:
        raise CheckerUnavailable(_short(str(exc))) from exc


async def get_job_hits(
    api_url: str,
    job_id: str,
    page: int = 1,
    page_size: int = 100,
    plan_filter: str = "",
    hold_filter: str = "",
) -> dict[str, Any]:
    """GET /api/v1/jobs/{id}/hits with pagination and optional filters."""
    params: dict[str, Any] = {"page": max(1, int(page)), "page_size": min(int(page_size), 1000)}
    if plan_filter:
        params["plan"] = plan_filter
    if hold_filter:
        params["hold"] = hold_filter

    try:
        async with _get_session().get(
            f"{_base(api_url)}{API_PREFIX}/jobs/{job_id}/hits",
            params=params,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            body = await _read_json(resp)
            if resp.status != 200:
                raise CheckerAPIError(f"hits request failed ({resp.status}): {_short(body)}")
            if not isinstance(body.get("hits"), list):
                body["hits"] = []
            return body
    except asyncio.TimeoutError as exc:
        raise CheckerUnavailable("checker hits timeout") from exc
    except aiohttp.ClientError as exc:
        raise CheckerUnavailable(_short(str(exc))) from exc


async def cancel_job(api_url: str, job_id: str) -> dict[str, Any]:
    """POST /api/v1/jobs/{id}/cancel. Best effort — never raises."""
    try:
        async with _get_session().post(
            f"{_base(api_url)}{API_PREFIX}/jobs/{job_id}/cancel",
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status >= 400:
                logger.warning("cancel_job %s returned %s", job_id, resp.status)
                return {"status": "unknown"}
            return await _read_json(resp)
    except (asyncio.TimeoutError, aiohttp.ClientError, CheckerAPIError) as exc:
        logger.warning("cancel_job %s failed: %s", job_id, exc)
        return {"status": "unknown"}


async def export_hits(api_url: str, job_id: str, fmt: str = "txt") -> bytes:
    """GET /api/v1/jobs/{id}/export — formatted hit file straight from the API."""
    fmt = fmt if fmt in ("txt", "json") else "txt"
    try:
        async with _get_session().get(
            f"{_base(api_url)}{API_PREFIX}/jobs/{job_id}/export",
            params={"format": fmt},
            timeout=aiohttp.ClientTimeout(total=90),
        ) as resp:
            if resp.status != 200:
                raise CheckerAPIError(f"export failed ({resp.status})")
            return await resp.read()
    except asyncio.TimeoutError as exc:
        raise CheckerUnavailable("checker export timeout") from exc
    except aiohttp.ClientError as exc:
        raise CheckerUnavailable(_short(str(exc))) from exc


# ── instance telemetry / admin ───────────────────────────────────────
async def server_stats(api_url: str) -> dict[str, Any]:
    """GET /api/v1/stats — goroutines, memory, proxy pool, job counts."""
    try:
        async with _get_session().get(
            f"{_base(api_url)}{API_PREFIX}/stats",
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                raise CheckerAPIError(f"stats failed ({resp.status})")
            return await _read_json(resp)
    except asyncio.TimeoutError as exc:
        raise CheckerUnavailable("checker stats timeout") from exc
    except aiohttp.ClientError as exc:
        raise CheckerUnavailable(_short(str(exc))) from exc


async def proxies_status(api_url: str) -> dict[str, Any]:
    """GET /api/v1/proxies/status — pool size and alive count."""
    try:
        async with _get_session().get(
            f"{_base(api_url)}{API_PREFIX}/proxies/status",
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                raise CheckerAPIError(f"proxy status failed ({resp.status})")
            return await _read_json(resp)
    except asyncio.TimeoutError as exc:
        raise CheckerUnavailable("checker proxy status timeout") from exc
    except aiohttp.ClientError as exc:
        raise CheckerUnavailable(_short(str(exc))) from exc


async def upload_proxies(api_url: str, proxy_text: str) -> dict[str, Any]:
    """POST /api/v1/proxies as a text/plain body."""
    try:
        async with _get_session().post(
            f"{_base(api_url)}{API_PREFIX}/proxies",
            data=proxy_text.encode("utf-8"),
            headers={"Content-Type": "text/plain"},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            body = await _read_json(resp)
            if resp.status not in (200, 201, 202):
                raise CheckerAPIError(f"proxy upload failed ({resp.status}): {_short(body)}")
            return body
    except asyncio.TimeoutError as exc:
        raise CheckerUnavailable("checker proxy upload timeout") from exc
    except aiohttp.ClientError as exc:
        raise CheckerUnavailable(_short(str(exc))) from exc


async def ping_health(api_url: str) -> tuple[str, float]:
    """GET /health. Returns (status, latency_ms).

    status is 'online' (<2s), 'slow' (<timeout) or 'offline'.
    Used by the load balancer's health monitor every minute.
    """
    started = asyncio.get_event_loop().time()
    timeout = max(3, int(config.settings.api_timeout_seconds))
    try:
        async with _get_session().get(
            f"{_base(api_url)}/health",
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            elapsed_ms = (asyncio.get_event_loop().time() - started) * 1000.0
            if resp.status == 200:
                return ("online" if elapsed_ms < 2000 else "slow"), round(elapsed_ms, 2)
            return "error", round(elapsed_ms, 2)
    except (asyncio.TimeoutError, aiohttp.ClientError):
        return "offline", 0.0

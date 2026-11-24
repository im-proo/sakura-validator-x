"""Orchestrates one check session against the job-based Go API (Section 10.11).

Lifecycle:
    submit multipart job → poll /jobs/{id} every 3s → fetch new hits →
    deliver hit cards → on done/cancel/failed finalize → always release.

An attempt is spent only after the API has accepted the job, and it is
never refunded once the job is running (Section 14).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile

import config
import core.api_manager as api_manager
import core.checker_api as checker_api
import core.database as database
from models.schemas import (
    SessionStats,
    hit_identity,
    truthy,
    is_premium_hit,
    utcnow,
)
from services import file_service, plan_service
from ui import keyboards, renderer
from ui.states import LOADING_TEXT

logger = logging.getLogger(__name__)

POLL_INTERVAL = 3.0
MIN_EDIT_INTERVAL = 3.0
MAX_PAGES_PER_SWEEP = 20
HITS_PAGE_SIZE = 100
TRANSIENT_RETRIES = 5

# Plan names already reported as "not premium" in this process, so a batch of
# free accounts logs each distinct plan once instead of once per cookie.
_logged_plans: set[tuple[str, str]] = set()


@dataclass
class Shard:
    """One checker instance working on one slice of the uploaded file."""

    api_id: str
    api_url: str
    job_id: str = ""
    total: int = 0
    checked: int = 0
    hits: int = 0
    bad: int = 0
    errors: int = 0
    hold: int = 0
    cpm: float = 0.0
    poll_failures: int = 0
    # pending | running | done | cancelled | failed
    state: str = "pending"
    # False once the reserved slot was handed back without running a job.
    acquired: bool = True

    @property
    def active(self) -> bool:
        return self.state in ("pending", "running")


@dataclass
class CheckSession:
    """Live state of one running job. Memory only — never persisted.

    A session owns one shard per checker instance. With a single instance it
    is exactly the old one-job flow; with several, each one checks its own
    slice of the upload and the counters below are their sum.
    """

    session_id: str
    user_id: int
    chat_id: int
    message_id: int
    api_id: str
    api_url: str
    hit_mode: str
    plan: str
    video: str = ""
    total: int = 0
    job_id: str = ""
    stats: SessionStats = field(default_factory=SessionStats)
    last_edit_at: float = 0.0
    delivered: list[dict[str, Any]] = field(default_factory=list)
    shards: list[Shard] = field(default_factory=list)

    @property
    def premium_only(self) -> bool:
        return self.hit_mode == "premium_only"

    def shard_list(self) -> list[Shard]:
        """The slices of this run; a plain session gets its primary one."""
        if not self.shards:
            self.shards = [Shard(api_id=self.api_id, api_url=self.api_url)]
        return self.shards

    def active_shards(self) -> list[Shard]:
        """Slices that have a job and have not finished yet."""
        return [s for s in self.shard_list() if s.job_id and s.active]


def new_session_id() -> str:
    import uuid

    return f"job-{uuid.uuid4().hex[:12]}"


# ── card editing ─────────────────────────────────────────────────────
async def _edit_live_card(bot: Bot, session: CheckSession, force: bool = False) -> None:
    """Edit the live dashboard, respecting the 3 second rate limit."""
    now = time.monotonic()
    if not force and (now - session.last_edit_at) < MIN_EDIT_INTERVAL:
        return
    session.last_edit_at = now
    try:
        await bot.edit_message_text(
            chat_id=session.chat_id,
            message_id=session.message_id,
            rich_message=renderer.render_live_card(session, finished=False, video=session.video),
            reply_markup=keyboards.live_check_keyboard(session.session_id),
        )
    except TelegramBadRequest as exc:
        message = str(exc).lower()
        if "not modified" in message or "message is not modified" in message:
            return
        logger.debug("live card edit rejected session=%s: %s", session.session_id, exc)
    except Exception as exc:  # pragma: no cover - network hiccup
        logger.debug("live card edit failed session=%s: %s", session.session_id, exc)


async def _finish_card(bot: Bot, session: CheckSession) -> None:
    """Leave the dashboard in its finished state."""
    try:
        await bot.edit_message_text(
            chat_id=session.chat_id,
            message_id=session.message_id,
            rich_message=renderer.render_live_card(session, finished=True),
        )
    except Exception as exc:
        logger.debug("finish card edit failed session=%s: %s", session.session_id, exc)


# ── hits ─────────────────────────────────────────────────────────────
def _login_token(hit: dict[str, Any]) -> dict[str, Any]:
    """Phone / PC / TV links, whichever key the checker API puts them under."""
    for key in ("nf_token", "token", "links", "login_links", "nf_links"):
        value = hit.get(key)
        if isinstance(value, dict) and value:
            return value
    return {k: hit[k] for k in ("phone_link", "pc_link", "tv_link") if hit.get(k)}


async def _send_hit(bot: Bot, session: CheckSession, hit: dict[str, Any]) -> bool:
    """One message per hit: the card, the login buttons and the cookie file.

    The .txt is embedded as a quoted document block inside the card, so the
    account details, the cookie file and the Phone / PC / TV buttons all
    arrive together in a single message.
    """
    account = hit.get("account") or {}
    if not account:
        return False

    file_name = file_service.hit_file_name(hit)
    document = BufferedInputFile(
        file_service.build_hit_text(hit).encode("utf-8"),
        filename=file_name,
    )

    try:
        sent = await bot.send_rich_message(
            chat_id=session.chat_id,
            rich_message=renderer.render_hit_card(hit, document=document, file_name=file_name),
            reply_markup=keyboards.hit_login_keyboard(_login_token(hit)),
        )
    except Exception as exc:
        logger.error("hit card send failed session=%s: %s", session.session_id, exc)
        return False

    session.delivered.append(hit)
    if truthy(account.get("is_on_hold")) or truthy(account.get("hold_status")):
        session.stats.hold += 1

    await forward_hit(bot, sent.message_id, session.chat_id)
    return True


async def forward_hit(bot: Bot, message_id: int, from_chat_id: int) -> None:
    """Mirror a hit into the configured forward channel."""
    try:
        channel = str(await database.get_config("hit_forward_channel", "") or "").strip()
        if not channel:
            return
        await bot.copy_message(
            chat_id=channel,
            from_chat_id=from_chat_id,
            message_id=message_id,
        )
    except Exception as exc:
        logger.debug("hit forward failed: %s", exc)


async def _fetch_new_hits(bot: Bot, session: CheckSession) -> None:
    """Pull every page of every slice, deliver hits not delivered yet."""
    seen = 0
    for shard in session.shard_list():
        if not shard.job_id:
            continue
        seen += await _fetch_shard_hits(bot, session, shard)
    if seen:
        logger.debug("session=%s delivered %s hits", session.session_id, seen)


async def _fetch_shard_hits(bot: Bot, session: CheckSession, shard: Shard) -> int:
    """Deliver the premium hits of one slice. Returns how many were sent."""
    seen = 0
    for page in range(1, MAX_PAGES_PER_SWEEP + 1):
        try:
            payload = await checker_api.get_job_hits(
                shard.api_url,
                shard.job_id,
                page=page,
                page_size=HITS_PAGE_SIZE,
                # Never let the API pre-filter by plan: its filter is a raw
                # substring match on the display name, so a localised Premium
                # name it does not know is dropped server-side and the bot can
                # only count it as `free`. Pulling every valid session lets
                # the classifier below decide, and log the names it rejects.
                plan_filter="",
            )
        except checker_api.CheckerAPIError as exc:
            logger.warning(
                "hit fetch failed session=%s api=%s page=%s: %s",
                session.session_id, shard.api_id, page, exc,
            )
            return seen

        hits = payload.get("hits") or []
        if not hits:
            return seen

        for hit in hits:
            if str(hit.get("status") or "hit") != "hit":
                continue
            # Count each valid session once, the moment it is fetched: the
            # live card then shows a Premium account as a hit even before its
            # Telegram message has been sent. Delivery is deduped separately.
            identity = hit_identity(hit)
            premium = is_premium_hit(hit)
            if identity not in session.stats.seen_hits:
                session.stats.seen_hits.add(identity)
                if premium:
                    session.stats.premium += 1
            # Both modes deliver hits as premium accounts only. All mode
            # simply asks the API for every valid session, so basic /
            # standard / ads accounts are counted (as free) but never sent.
            if not premium:
                # Record what the API reported, so an unrecognised localised
                # plan name is visible instead of silently disappearing into
                # `free`. Logged once per plan name, not once per cookie.
                account = hit.get("account") or {}
                marker = (
                    str(account.get("plan") or ""),
                    str(account.get("plan_code") or ""),
                    str(account.get("plan_type") or ""),
                )
                if marker not in _logged_plans:
                    _logged_plans.add(marker)
                    logger.info(
                        "not a premium hit: plan=%r plan_code=%r plan_type=%r ads=%r membership=%r",
                        account.get("plan"),
                        account.get("plan_code"),
                        account.get("plan_type"),
                        account.get("is_ads_tier"),
                        account.get("membership_status"),
                    )
                continue
            if identity in session.stats.sent_hits:
                continue
            # Only mark delivered once Telegram accepted the card, so a failed
            # send is retried on the next sweep instead of being lost.
            if await _send_hit(bot, session, hit):
                session.stats.sent_hits.add(identity)
                seen += 1

        total_pages = int(payload.get("total_pages") or 1)
        if page >= total_pages:
            return seen

    return seen


# ── main loop ────────────────────────────────────────────────────────
async def _plan_uploads(
    session: CheckSession,
    file_path: str,
    file_name: str,
) -> list[tuple[Shard, str, str]]:
    """One (instance, file, name) per slice, or a single whole-file upload.

    The upload is only cut when it can be cut exactly — every cookie lands in
    one slice and none is dropped. Anything else (RAR, 7z, JSON, a damaged
    archive) goes to one instance exactly as before.
    """
    shards = session.shard_list()
    if len(shards) > 1:
        try:
            slices = await file_service.split_cookie_file(
                file_path,
                file_name,
                chunk_size=config.settings.checker_shard_cookies,
                max_shards=len(shards),
            )
        except Exception as exc:  # pragma: no cover - splitting is best effort
            logger.warning("sharding failed session=%s: %s", session.session_id, exc)
            slices = []
        if slices:
            uploads = [
                (shard, item["file_path"], item["file_name"])
                for shard, item in zip(shards, slices)
            ]
            for shard in shards[len(uploads):]:
                shard.acquired = False
                await api_manager.balancer.abandon(shard.api_id)
            logger.info(
                "session=%s sharded into %s slices across %s instances",
                session.session_id, len(uploads), len(uploads),
            )
            return uploads

    for shard in shards[1:]:
        shard.acquired = False
        await api_manager.balancer.abandon(shard.api_id)
    return [(shards[0], file_path, file_name)]


async def run_check(bot: Bot, session: CheckSession, file_path: str, file_name: str) -> None:
    """The background task for one job. Never raises.

    A big upload is cut into slices and every online instance checks one at
    the same time; counters and hits of all slices are merged into this one
    session, so the dashboard and the delivered bundle look like one run.
    """
    shards = session.shard_list()
    chunk_files: list[str] = []
    try:
        uploads = await _plan_uploads(session, file_path, file_name)
        chunk_files = [path for _, path, _ in uploads if path != file_path]

        submitted = await asyncio.gather(
            *[
                checker_api.submit_job_file(
                    api_url=shard.api_url,
                    file_path=path,
                    file_name=name,
                    concurrency=config.settings.checker_concurrency,
                    rate_per_minute=config.settings.checker_rate_per_minute,
                )
                for shard, path, name in uploads
            ],
            return_exceptions=True,
        )

        accepted = 0
        for (shard, _, _), result in zip(uploads, submitted):
            if isinstance(result, BaseException):
                logger.error(
                    "job submit failed session=%s api=%s: %s",
                    session.session_id, shard.api_id, result,
                )
                shard.state = "failed"
                shard.acquired = False
                await api_manager.balancer.abandon(shard.api_id)
                continue
            shard.job_id = str(result.get("job_id") or "")
            shard.total = int(result.get("total") or 0)
            shard.state = "running" if shard.job_id else "failed"
            accepted += 1

        if not accepted:
            detail = str(submitted[0]) if submitted else "no checker accepted the job"
            await _fail(bot, session, detail)
            return

        session.job_id = next(s.job_id for s in shards if s.job_id)
        session.stats.job_id = session.job_id
        _aggregate(session)

        # The attempt is spent here: at least one instance accepted a slice.
        if not await plan_service.deduct_attempt(session.user_id, session.plan):
            session.stats.status = "blocked"
            for shard in session.active_shards():
                await checker_api.cancel_job(shard.api_url, shard.job_id)
            user = await plan_service.get_user(session.user_id)
            await _edit_to(
                bot,
                session,
                renderer.render_daily_limit_card(user or {}),
                keyboards.daily_limit_keyboard(),
            )
            return

        await _edit_live_card(bot, session, force=True)

        cancelled = False
        while True:
            if api_manager.is_cancelled(session.session_id):
                cancelled = True
                for shard in session.active_shards():
                    await checker_api.cancel_job(shard.api_url, shard.job_id)
                    shard.state = "cancelled"
                break

            for shard in list(session.active_shards()):
                await _poll_shard(session, shard)

            _aggregate(session)
            # Fetch before drawing: a hit delivered on this sweep must already
            # be counted, otherwise the card shows it as free until the next
            # poll (three seconds later) repaints with the same numbers.
            await _fetch_new_hits(bot, session)
            _split_counts(session)
            await _edit_live_card(bot, session)

            if not any(shard.active for shard in shards):
                break
            await asyncio.sleep(POLL_INTERVAL)

        if cancelled:
            session.stats.status = "cancelled"
        elif any(shard.state == "done" for shard in shards):
            session.stats.status = "done"
        else:
            session.stats.status = "failed"
            await _fail(bot, session, "every checker instance failed")
            return

        await _finalize(bot, session)

    except asyncio.CancelledError:
        logger.info("check task cancelled session=%s", session.session_id)
        raise
    except Exception as exc:  # pragma: no cover - last line of defence
        logger.exception("check crashed session=%s: %s", session.session_id, exc)
        await _fail(bot, session, "unexpected error while checking")
    finally:
        # Only a clean run or a user cancel leaves the dashboard in the
        # finished state; failure and limit cards must not be overwritten.
        if session.stats.status in ("done", "cancelled"):
            await _finish_card(bot, session)
        for shard in shards:
            if not shard.acquired:
                continue
            await api_manager.balancer.release(
                shard.api_id,
                success=shard.state in ("done", "cancelled"),
                final_cpm=shard.cpm,
            )
        api_manager.drop_session(session.session_id)
        file_service.cleanup(file_path, *chunk_files)
        logger.info(
            "session=%s closed status=%s checked=%s hits=%s used=%s",
            session.session_id,
            session.stats.status,
            session.stats.checked,
            session.stats.hits,
            len([s for s in shards if s.job_id]),
        )


async def _poll_shard(session: CheckSession, shard: Shard) -> None:
    """One status poll for one slice; a dead instance retires its slice."""
    try:
        status = await checker_api.get_job_status(shard.api_url, shard.job_id)
        shard.poll_failures = 0
    except checker_api.CheckerAPIError as exc:
        shard.poll_failures += 1
        logger.warning(
            "status poll failed session=%s api=%s (%s/%s): %s",
            session.session_id, shard.api_id, shard.poll_failures, TRANSIENT_RETRIES, exc,
        )
        if shard.poll_failures >= TRANSIENT_RETRIES:
            shard.state = "failed"
        return

    _apply_shard_status(shard, status)
    state = str(status.get("status") or "running").lower()
    if state in ("done", "cancelled", "failed"):
        shard.state = state


def _apply_shard_status(shard: Shard, status: dict[str, Any]) -> None:
    """Fold one poll payload into that slice's counters."""
    shard.checked = int(status.get("checked") or 0)
    shard.hits = int(status.get("hits") or 0)
    # The Go API counts non-hits as "expired" and exposes RPM as "current_rpm".
    shard.bad = int(status.get("expired") or status.get("bad") or 0)
    shard.errors = int(status.get("errors") or 0)
    shard.hold = int(status.get("hold") or 0)
    shard.cpm = float(status.get("current_rpm") or status.get("cpm") or 0.0)
    total = int(status.get("total") or 0)
    if total:
        shard.total = total


def _aggregate(session: CheckSession) -> None:
    """Sum every slice into the counters the dashboard renders."""
    shards = session.shard_list()
    stats = session.stats
    stats.total = sum(shard.total for shard in shards)
    stats.checked = sum(shard.checked for shard in shards)
    stats.api_hits = sum(shard.hits for shard in shards)
    stats.bad = sum(shard.bad for shard in shards)
    stats.errors = sum(shard.errors for shard in shards)
    stats.hold = sum(shard.hold for shard in shards)
    stats.cpm = sum(shard.cpm for shard in shards)
    session.total = stats.total
    _split_counts(session)


def _apply_status(session: Any, status: dict[str, Any]) -> None:
    """Fold one status payload into a one-slice session.

    Works on anything carrying `stats` (the tests use a lightweight stand-in),
    and is the single-shard view of what `_poll_shard` does per slice.
    """
    stats = session.stats
    shard = Shard(api_id="", api_url="")
    _apply_shard_status(shard, status)
    stats.checked = shard.checked
    stats.api_hits = shard.hits
    stats.bad = shard.bad
    stats.errors = shard.errors
    stats.hold = shard.hold
    stats.cpm = shard.cpm
    if shard.total:
        stats.total = shard.total
        session.total = shard.total
    _split_counts(session)


def _split_counts(session: CheckSession) -> None:
    """Premium accounts are hits; every other valid session is free."""
    premium = min(session.stats.premium, session.stats.api_hits)
    session.stats.hits = premium
    session.stats.free = max(0, session.stats.api_hits - premium)


async def _finalize(bot: Bot, session: CheckSession) -> None:
    """Deliver leftovers, summary card and the result files."""
    await _fetch_new_hits(bot, session)
    session.stats.finished_at = utcnow()

    premium_hits = [h for h in session.delivered if is_premium_hit(h)]
    await plan_service.record_check_result(
        session.user_id,
        checks=session.stats.checked,
        hits=len(premium_hits),
        premium_hits=len(premium_hits),
    )

    try:
        await bot.send_rich_message(
            chat_id=session.chat_id,
            rich_message=renderer.render_complete_card(session),
            reply_markup=keyboards.check_complete_keyboard(),
        )
    except Exception as exc:
        logger.warning("complete card failed session=%s: %s", session.session_id, exc)

    if not premium_hits:
        return

    bundle = await file_service.build_hits_bundle(premium_hits, token=session.session_id)
    if bundle.get("zip"):
        await _send_file(bot, session.chat_id, bundle["zip"], file_service.bundle_file_names("zip"))
    if bundle.get("txt"):
        await _send_file(bot, session.chat_id, bundle["txt"], file_service.bundle_file_names("txt"))
    file_service.cleanup(bundle.get("zip"), bundle.get("txt"))


async def _send_file(bot: Bot, chat_id: int, path: str, file_name: str) -> None:
    try:
        with open(path, "rb") as handle:
            payload = handle.read()
        # aiogram takes the filename on the InputFile, not as a keyword.
        await bot.send_document(
            chat_id=chat_id,
            document=BufferedInputFile(payload, filename=file_name),
        )
    except Exception as exc:
        logger.error("result file send failed: %s", exc)


async def _edit_to(bot: Bot, session: CheckSession, rich: Any, reply_markup: Any) -> None:
    """Replace the dashboard with a terminal card (limit reached, error…)."""
    try:
        await bot.edit_message_text(
            chat_id=session.chat_id,
            message_id=session.message_id,
            rich_message=rich,
            reply_markup=reply_markup,
        )
    except Exception as exc:
        logger.debug("terminal edit failed session=%s: %s", session.session_id, exc)


async def _fail(bot: Bot, session: CheckSession, detail: str) -> None:
    """Report a terminal failure on the dashboard."""
    session.stats.status = "failed"
    await _edit_to(
        bot,
        session,
        renderer.render_error_card("check failed", detail),
        keyboards.back_to_menu_keyboard(),
    )


# ── entry points used by handlers ────────────────────────────────────
async def create_session(
    user: dict[str, Any],
    apis: list[dict[str, Any]],
    hit_mode: str,
    chat_id: int,
) -> CheckSession:
    """Register a session and reserve a slot on every instance it may use.

    The handler sends the dashboard card next (so the checking video can be
    embedded in it), fills `message_id`, then calls `launch`. Instances that
    end up unused are handed back by run_check before it finishes.
    """
    if not apis:
        raise ValueError("create_session needs at least one checker instance")

    shards = [
        Shard(api_id=str(api["api_id"]), api_url=str(api["url"])) for api in apis
    ]
    session = CheckSession(
        session_id=new_session_id(),
        user_id=int(user["user_id"]),
        chat_id=chat_id,
        message_id=0,
        api_id=shards[0].api_id,
        api_url=shards[0].api_url,
        hit_mode=hit_mode,
        plan=str(user.get("plan") or "free"),
        stats=SessionStats(status="running"),
        shards=shards,
    )
    api_manager.register_session(session.session_id, session)
    for shard in shards:
        await api_manager.balancer.acquire(shard.api_id)
    return session


async def launch(bot: Bot, session: CheckSession, file_info: dict[str, Any]) -> None:
    """Start the check loop in the background.

    run_check is a coroutine, so launch() must be async and use
    create_task; a plain def would schedule a coroutine object that never
    runs and the check would never start (the "can't fetch cookies from a zip"
    symptom).
    """
    asyncio.create_task(
        run_check(bot, session, file_info["file_path"], file_info["file_name"]),
        name=f"check:{session.session_id}",
    )
    logger.info(
        "session=%s started user=%s api=%s mode=%s",
        session.session_id, session.user_id, session.api_id, session.hit_mode,
    )


def loading_rich() -> Any:
    """Placeholder card shown while the job is being accepted."""
    return renderer.render_loading_card(LOADING_TEXT)


def active_count() -> int:
    return api_manager.total_active()


def has_capacity() -> bool:
    return api_manager.capacity_available()


def global_cap() -> int:
    return int(config.settings.max_concurrent_global)

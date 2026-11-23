"""Update middlewares: ban gate, maintenance gate, force-join gate.

Registration order matters (outermost first): ban → maintenance → join,
so a banned user is told they are banned instead of being asked to join
channels.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any, Awaitable, Callable

import config
import core.database as database
from models.schemas import utcnow
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery
from ui import keyboards, renderer
from ui.states import CB

logger = logging.getLogger(__name__)

# chat_id is treated as joined when the status is one of these
JOINED_STATUSES = {"member", "administrator", "creator", "owner"}

_maintenance_cache: dict[str, Any] = {"value": False, "checked_at": 0.0}
_MAINTENANCE_TTL = 30.0


def _from_user(event: Any) -> Any | None:
    return getattr(event, "from_user", None)


async def _deliver(bot: Any, event: Any, rich: Any, reply_markup: Any = None) -> None:
    """Send a card for a message, or re-edit the card for a callback."""
    if isinstance(event, CallbackQuery):
        try:
            await event.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await bot.send_rich_message(
            chat_id=event.message.chat.id,
            rich_message=rich,
            reply_markup=reply_markup,
        )
        return
    await bot.send_rich_message(
        chat_id=event.chat.id,
        rich_message=rich,
        reply_markup=reply_markup,
    )


async def maintenance_enabled() -> bool:
    """Cached maintenance flag — one Mongo read per 30s, not per update."""
    now = time.monotonic()
    if now - _maintenance_cache["checked_at"] < _MAINTENANCE_TTL:
        return bool(_maintenance_cache["value"])
    try:
        value = bool(await database.get_config("maintenance_mode", False))
    except Exception as exc:  # database hiccup must not lock the bot
        logger.warning("maintenance flag read failed: %s", exc)
        value = bool(_maintenance_cache["value"])
    _maintenance_cache["value"] = value
    _maintenance_cache["checked_at"] = now
    return value


def invalidate_maintenance_cache() -> None:
    """Called by the admin config page after toggling the switch."""
    _maintenance_cache["checked_at"] = 0.0


class BanCheckMiddleware(BaseMiddleware):
    """Silently stop banned users before any handler runs."""

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        user = _from_user(event)
        if user is None or config.settings.is_admin(user.id):
            return await handler(event, data)

        try:
            doc = await database.users().find_one(
                {"user_id": user.id}, {"is_banned": 1, "ban_reason": 1}
            )
        except Exception as exc:
            logger.warning("ban check failed user=%s: %s", user.id, exc)
            return await handler(event, data)

        if doc and doc.get("is_banned"):
            bot = data["bot"]
            if isinstance(event, CallbackQuery):
                await event.answer()
            await _deliver(
                bot,
                event,
                renderer.render_banned_card(str(doc.get("ban_reason") or "")),
                None,
            )
            return None

        return await handler(event, data)


class MaintenanceMiddleware(BaseMiddleware):
    """Owners and admins keep working while the bot is under maintenance."""

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        user = _from_user(event)
        if user is not None and config.settings.is_admin(user.id):
            return await handler(event, data)
        if not await maintenance_enabled():
            return await handler(event, data)

        if isinstance(event, CallbackQuery):
            await event.answer(renderer.sc("maintenance in progress"), show_alert=True)
            return None

        await _deliver(data["bot"], event, renderer.render_maintenance_card(), None)
        return None


class ForceJoinMiddleware(BaseMiddleware):
    """Require membership of every active channel before using the bot."""

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        user = _from_user(event)
        if user is None or config.settings.is_admin(user.id):
            return await handler(event, data)

        # The verify button must reach its handler or nobody could ever pass,
        # and a running check must always be cancellable.
        if isinstance(event, CallbackQuery):
            if event.data == CB.VERIFY:
                return await handler(event, data)
            if str(event.data or "").startswith(f"{CB.CHECK_CANCEL}:"):
                return await handler(event, data)

        channels = await active_channels()
        if not channels:
            return await handler(event, data)

        fingerprint = channel_fingerprint(channels)

        # Verified once for exactly this channel set: never probe again until
        # an admin adds (or removes) a channel, which changes the fingerprint.
        if await is_verified(user.id, fingerprint):
            return await handler(event, data)

        unjoined = await unjoined_channels(data["bot"], user.id, channels)
        if not unjoined:
            await mark_verified(user.id, fingerprint)
            return await handler(event, data)

        if isinstance(event, CallbackQuery):
            await event.answer()
        # Every channel is rendered, not just the probeable ones, so a single
        # unreachable channel can never shrink the keyboard to one button.
        card, keyboard = await build_verify_screen(data["bot"], channels)
        await _deliver(data["bot"], event, card, keyboard)
        return None


def channel_fingerprint(channels: list[dict[str, Any]]) -> str:
    """Stable id of the current channel set (order-independent)."""
    ids = sorted(str(c.get("channel_id") or "").strip() for c in channels if c.get("channel_id"))
    return hashlib.sha1("|".join(ids).encode("utf-8")).hexdigest()[:16] if ids else ""


_verified_cache: dict[int, tuple[str, float]] = {}
_VERIFIED_TTL = 300.0


async def is_verified(user_id: int, fingerprint: str) -> bool:
    """True when this user already passed the gate for this channel set."""
    if not fingerprint:
        return True
    now = time.monotonic()
    cached = _verified_cache.get(user_id)
    if cached and cached[0] == fingerprint and now - cached[1] < _VERIFIED_TTL:
        return True
    try:
        doc = await database.users().find_one(
            {"user_id": int(user_id)}, {"verified_channels": 1}
        )
    except Exception as exc:
        logger.debug("verified flag read failed user=%s: %s", user_id, exc)
        return False
    stored = str((doc or {}).get("verified_channels") or "")
    if stored and stored == fingerprint:
        _verified_cache[user_id] = (fingerprint, now)
        return True
    return False


async def mark_verified(user_id: int, fingerprint: str) -> None:
    """Remember that the user passed the gate for this exact channel set."""
    _verified_cache[user_id] = (fingerprint, time.monotonic())
    try:
        await database.users().update_one(
            {"user_id": int(user_id)},
            {"$set": {"verified_channels": fingerprint, "verified_at": utcnow()}},
        )
    except Exception as exc:
        logger.warning("verified flag write failed user=%s: %s", user_id, exc)


async def active_channels() -> list[dict[str, Any]]:
    """Every channel the bot gates on, in display order.

    The Mongo collection is the catalogue, but channels listed in
    ``config.FORCE_JOIN_CHANNELS`` are always gated too — that way a fresh
    database, or one where only some rows were added, still enforces the
    full set. Rows an admin disabled are left out.
    """
    docs: list[dict[str, Any]] = []
    try:
        docs = [doc async for doc in database.channels().find({}).sort("order", 1)]
    except Exception as exc:
        logger.warning("channel list read failed: %s", exc)

    channels = [dict(doc) for doc in docs if doc.get("is_active")]
    known = {str(doc.get("channel_id") or "").strip() for doc in docs}

    for channel in channels:
        if not str(channel.get("channel_link") or "").strip():
            channel_id = str(channel.get("channel_id") or "").strip()
            fallback = str(config.FORCE_JOIN_LINKS.get(channel_id, "") or "").strip()
            if fallback:
                channel["channel_link"] = fallback

    for index, chat_id in enumerate(config.FORCE_JOIN_CHANNELS, start=len(channels) + 1):
        chat_id = str(chat_id).strip()
        if not chat_id or chat_id in known:
            continue
        channels.append(
            {
                "channel_id": chat_id,
                "channel_name": chat_id,
                "channel_link": str(config.FORCE_JOIN_LINKS.get(chat_id, "") or ""),
                "is_active": True,
                "order": index,
            }
        )
    return channels


_link_cache: dict[str, tuple[str, float]] = {}
_LINK_TTL = 600.0


async def _lookup_channel_link(bot: Any, chat_id: str) -> str:
    """Public link, stored invite link, or a freshly minted invite link."""
    try:
        chat = await bot.get_chat(chat_id)
    except Exception as exc:
        logger.warning("channel link lookup failed channel=%s: %s", chat_id, exc)
        return ""

    username = str(getattr(chat, "username", "") or "").strip()
    if username:
        return f"https://t.me/{username}"
    link = str(getattr(chat, "invite_link", "") or "").strip()
    if link:
        return link
    try:
        invite = await bot.create_chat_invite_link(chat_id)
        return str(getattr(invite, "invite_link", "") or "")
    except Exception as exc:
        logger.warning("invite link creation failed channel=%s: %s", chat_id, exc)
        return ""


async def resolve_channel_links(
    bot: Any, channels: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Give every channel a join link, so none is dropped from the keyboard."""
    resolved: list[dict[str, Any]] = []
    now = time.monotonic()
    for channel in channels:
        item = dict(channel)
        chat_id = str(item.get("channel_id") or "").strip()
        link = str(item.get("channel_link") or "").strip()
        if not link and chat_id:
            link = str(config.FORCE_JOIN_LINKS.get(chat_id, "") or "").strip()
        if not link and chat_id:
            cached = _link_cache.get(chat_id)
            if cached and now - cached[1] < _LINK_TTL:
                link = cached[0]
            if not link:
                link = await _lookup_channel_link(bot, chat_id)
                if link:
                    _link_cache[chat_id] = (link, now)
        item["channel_link"] = link
        item["channel_name"] = str(item.get("channel_name") or chat_id or "channel")
        resolved.append(item)
    return resolved


async def build_verify_screen(bot: Any, channels: list[dict[str, Any]]) -> tuple[Any, Any]:
    """Verification card (with its video) plus the join/verify keyboard."""
    try:
        video = str(await database.get_config("verify_video", "") or "")
    except Exception as exc:
        logger.warning("verify video read failed: %s", exc)
        video = ""
    linked = await resolve_channel_links(bot, channels)
    return renderer.render_verify_card(linked, video), keyboards.verify_keyboard(linked)


async def unjoined_channels(
    bot: Any, user_id: int, channels: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Channels the user has not joined. Probe errors count as joined."""
    missing: list[dict[str, Any]] = []
    for channel in channels:
        chat_id = str(channel.get("channel_id") or "").strip()
        if not chat_id:
            continue
        try:
            member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
            status = str(getattr(member, "status", "")).lower()
            if status not in JOINED_STATUSES:
                missing.append(channel)
        except Exception as exc:
            # Treated as joined so a Telegram hiccup cannot lock everyone out,
            # but logged loudly: it usually means the bot is not an admin there.
            logger.warning(
                "membership probe failed channel=%s (bot must be an admin there): %s",
                chat_id,
                exc,
            )
    return missing

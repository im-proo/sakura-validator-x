"""Update middlewares: ban gate, maintenance gate, force-join gate.

Registration order matters (outermost first): ban → maintenance → join,
so a banned user is told they are banned instead of being asked to join
channels.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable

import config
import core.database as database
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

        unjoined = await unjoined_channels(data["bot"], user.id, channels)
        if not unjoined:
            return await handler(event, data)

        if isinstance(event, CallbackQuery):
            await event.answer()
        await _deliver(
            data["bot"],
            event,
            renderer.render_verify_card(unjoined),
            keyboards.verify_keyboard(unjoined),
        )
        return None

async def active_channels() -> list[dict[str, Any]]:
    """Every channel the bot gates on, in display order."""
    try:
        cursor = database.channels().find({"is_active": True}).sort("order", 1)
        return [doc async for doc in cursor]
    except Exception as exc:
        logger.warning("channel list read failed: %s", exc)
        return []


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
            logger.debug("membership probe failed channel=%s: %s", chat_id, exc)
    return missing

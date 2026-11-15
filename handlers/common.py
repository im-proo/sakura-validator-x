"""Shared handler plumbing: card delivery, card editing, admin gate."""

from __future__ import annotations

import logging
from typing import Any

from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

import config
from ui import renderer

logger = logging.getLogger(__name__)


class IsAdmin(BaseFilter):
    """Router-level gate: only the owner and listed admins may pass."""

    async def __call__(self, event: Any) -> bool:
        user = getattr(event, "from_user", None)
        return bool(user and config.settings.is_admin(user.id))


def actor_id(event: Any) -> int:
    user = getattr(event, "from_user", None)
    return int(user.id) if user else 0


async def send_card(
    event: Message | CallbackQuery,
    rich: Any,
    reply_markup: Any = None,
) -> Message | None:
    """Send a rich card for whichever update type triggered the handler."""
    chat_id = event.message.chat.id if isinstance(event, CallbackQuery) else event.chat.id
    try:
        return await event.bot.send_rich_message(
            chat_id=chat_id,
            rich_message=rich,
            reply_markup=reply_markup,
        )
    except Exception as exc:
        logger.error("send_card failed chat=%s: %s", chat_id, exc)
        return None


async def edit_card(
    call: CallbackQuery,
    rich: Any,
    reply_markup: Any = None,
) -> bool:
    """Edit the card in place; fall back to a fresh message when needed."""
    try:
        await call.bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            rich_message=rich,
            reply_markup=reply_markup,
        )
        return True
    except TelegramBadRequest as exc:
        text = str(exc).lower()
        if "not modified" in text:
            if reply_markup is not None:
                try:
                    await call.message.edit_reply_markup(reply_markup=reply_markup)
                except Exception:
                    pass
            return True
        logger.debug("edit_card fallback: %s", exc)
    except Exception as exc:
        logger.debug("edit_card failed: %s", exc)

    await send_card(call, rich, reply_markup)
    return False


async def toast(call: CallbackQuery, text: str, alert: bool = False) -> None:
    """Short callback answer; never raises."""
    try:
        await call.answer(text, show_alert=alert)
    except Exception:
        pass


async def notify_error(
    event: Message | CallbackQuery,
    title: str,
    detail: str,
    reply_markup: Any = None,
) -> None:
    """Standard failure path: styled card plus a log line."""
    logger.info("user error %s: %s", title, detail)
    await send_card(event, renderer.render_error_card(title, detail), reply_markup)

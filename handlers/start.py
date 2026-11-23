"""Entry point: /start, plan-expiry notice, force-join verification."""

from __future__ import annotations

import logging
from typing import Any

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import core.database as database
from core.middlewares import (
    active_channels,
    build_verify_screen,
    channel_fingerprint,
    mark_verified,
    unjoined_channels,
)
from handlers.common import edit_card, send_card, toast
from services import plan_service
from ui import keyboards, renderer
from ui.states import CB
from ui.typography import sc

router = Router()
logger = logging.getLogger(__name__)


async def show_main_menu(event: Message | CallbackQuery, user: dict[str, Any]) -> None:
    """The home card: profile summary, feature grid, brand footer."""
    video = str(await database.get_config("main_menu_video", "") or "")
    rank = await plan_service.get_rank(int(user["user_id"]))
    card = renderer.render_main_menu_card(user, rank, video)

    if isinstance(event, CallbackQuery):
        await edit_card(event, card, keyboards.main_menu_keyboard())
    else:
        await send_card(event, card, keyboards.main_menu_keyboard())


@router.message(CommandStart())
async def handle_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    if message.from_user is None:
        return

    logger.debug("handle_start: user_id=%d", message.from_user.id)
    user = await plan_service.ensure_user(message.from_user)
    user, expired_plan = await plan_service.expire_plan_if_needed(user)
    user = await plan_service.refresh_daily(user)

    if expired_plan:
        await send_card(
            message,
            renderer.render_plan_expired_card(expired_plan),
            keyboards.plan_expired_keyboard(),
        )

    await show_main_menu(message, user)


@router.callback_query(F.data == CB.VERIFY)
async def handle_verify(call: CallbackQuery, state: FSMContext) -> None:
    """Re-check membership after the user taps Verify."""
    if call.from_user is None:
        return

    channels = await active_channels()
    missing = await unjoined_channels(call.bot, call.from_user.id, channels)

    if missing:
        # Show the full list every time, never only the channels whose probe
        # happened to answer.
        card, keyboard = await build_verify_screen(call.bot, channels)
        await edit_card(call, card, keyboard)
        await toast(call, f"◇ {sc('channels still missing')} ✦")
        return

    await mark_verified(call.from_user.id, channel_fingerprint(channels))

    await toast(call, f"✦ {sc('verified')} {sc('welcome')}")
    user = await plan_service.ensure_user(call.from_user)
    user = await plan_service.refresh_daily(user)
    await show_main_menu(call, user)


def get_router() -> Router:
    return router

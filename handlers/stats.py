"""Profile and statistics screen."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from handlers.common import edit_card, send_card, toast
from services import plan_service
from ui import keyboards, renderer
from ui.states import CB
from ui.typography import sc

router = Router()
logger = logging.getLogger(__name__)


async def _stats_payload(user_id: int) -> tuple[dict | None, int]:
    user = await plan_service.get_user(user_id)
    if user is None:
        return None, 0
    user = await plan_service.refresh_daily(user)
    rank = await plan_service.get_rank(user_id)
    return user, rank


@router.message(Command("stats"))
async def handle_stats_command(message: Message) -> None:
    if message.from_user is None:
        return
    user, rank = await _stats_payload(message.from_user.id)
    if user is None:
        user = await plan_service.ensure_user(message.from_user)
        rank = await plan_service.get_rank(user["user_id"])
    await send_card(message, renderer.render_stats_card(user, rank), keyboards.stats_keyboard())


@router.callback_query(F.data == CB.MENU_STATS)
async def handle_stats_button(call: CallbackQuery) -> None:
    if call.from_user is None:
        return
    await call.answer()

    user, rank = await _stats_payload(call.from_user.id)
    if user is None:
        await toast(call, f"◇ {sc('start the bot with /start')}", alert=True)
        return

    await edit_card(call, renderer.render_stats_card(user, rank), keyboards.stats_keyboard())


def get_router() -> Router:
    return router

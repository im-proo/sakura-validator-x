"""Top checkers board, crown for rank one."""

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


async def _board(user_id: int) -> tuple[list[dict], int]:
    rows = await plan_service.top_users(10)
    rank = await plan_service.get_rank(user_id)
    return rows, rank


@router.message(Command("leaderboard"))
async def handle_board_command(message: Message) -> None:
    if message.from_user is None:
        return
    rows, rank = await _board(message.from_user.id)
    await send_card(
        message,
        renderer.render_leaderboard_card(rows, message.from_user.id, rank),
        keyboards.leaderboard_keyboard(),
    )


@router.callback_query(F.data == CB.MENU_LEADERBOARD)
async def handle_board_button(call: CallbackQuery) -> None:
    if call.from_user is None:
        return
    await call.answer()

    rows, rank = await _board(call.from_user.id)
    if not rows and not rank:
        await toast(call, f"◇ {sc('start the bot with /start')}", alert=True)
        return

    await edit_card(
        call,
        renderer.render_leaderboard_card(rows, call.from_user.id, rank),
        keyboards.leaderboard_keyboard(),
    )


def get_router() -> Router:
    return router

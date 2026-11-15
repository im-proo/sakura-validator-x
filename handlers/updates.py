"""Changelog screen."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from handlers.common import edit_card, send_card
from ui import keyboards, renderer
from ui.states import CB

router = Router()
logger = logging.getLogger(__name__)


@router.message(Command("updates"))
async def handle_updates_command(message: Message) -> None:
    await send_card(message, renderer.render_updates_card(), keyboards.updates_keyboard())


@router.callback_query(F.data == CB.MENU_UPDATES)
async def handle_updates_button(call: CallbackQuery) -> None:
    await call.answer()
    await edit_card(call, renderer.render_updates_card(), keyboards.updates_keyboard())


def get_router() -> Router:
    return router

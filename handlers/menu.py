"""Main menu callbacks: home card and the plans card."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from handlers.common import edit_card, toast
from handlers.start import show_main_menu
from services import plan_service
from ui import keyboards, renderer
from ui.states import CB
from ui.typography import sc

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data == CB.MENU_MAIN)
async def handle_menu_home(call: CallbackQuery, state: FSMContext) -> None:
    if call.from_user is None:
        return
    await state.clear()
    await call.answer()

    user = await plan_service.get_user(call.from_user.id)
    if user is None:
        user = await plan_service.ensure_user(call.from_user)
    user = await plan_service.refresh_daily(user)
    await show_main_menu(call, user)


@router.callback_query(F.data == CB.MENU_PLANS)
async def handle_menu_plans(call: CallbackQuery) -> None:
    if call.from_user is None:
        return
    await call.answer()

    user = await plan_service.get_user(call.from_user.id)
    if user is None:
        await toast(call, f"◇ {sc('start the bot with /start')}", alert=True)
        return

    await edit_card(call, renderer.render_plans_card(user), keyboards.plans_keyboard())


def get_router() -> Router:
    return router

"""Admin audit trail."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery

import core.database as database
from handlers.common import IsAdmin, edit_card
from ui import keyboards, renderer
from ui.states import CB

router = Router()
logger = logging.getLogger(__name__)

router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

PER_PAGE = 10


def _tail(data: str, prefix: str) -> str:
    return data[len(prefix):] if data.startswith(prefix) else ""


async def _show_page(call: CallbackQuery, page: int) -> None:
    total = await database.count_logs()
    pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    page = max(0, min(page, pages - 1))
    rows = await database.recent_logs(limit=PER_PAGE, skip=page * PER_PAGE)
    await edit_card(
        call,
        renderer.render_admin_logs_card(rows, page, pages),
        keyboards.admin_logs_keyboard(page, pages),
    )


@router.callback_query(F.data == CB.ADMIN_LOGS)
async def handle_logs(call: CallbackQuery) -> None:
    await call.answer()
    await _show_page(call, 0)


@router.callback_query(F.data.startswith(CB.ADMIN_LOGS_PAGE))
async def handle_logs_page(call: CallbackQuery) -> None:
    await call.answer()
    raw = _tail(call.data, CB.ADMIN_LOGS_PAGE) or "0"
    try:
        page = int(raw)
    except ValueError:
        page = 0
    await _show_page(call, page)


def get_router() -> Router:
    return router

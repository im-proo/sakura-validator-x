"""Video slots embedded inside cards: menu, verification, checking."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import core.database as database
from handlers.common import IsAdmin, edit_card, send_card, toast
from ui import keyboards, renderer
from ui.states import AdminVideoStates, CB
from ui.typography import sc

router = Router()
logger = logging.getLogger(__name__)

router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

SLOTS = ("main_menu_video", "verify_video", "checking_video")
SLOT_LABELS = {
    "main_menu_video": "main menu",
    "verify_video": "verification",
    "checking_video": "checking",
}


def _tail(data: str, prefix: str) -> str:
    return data[len(prefix):] if data.startswith(prefix) else ""


async def _slots() -> dict[str, str]:
    return {key: str(await database.get_config(key, "") or "") for key in SLOTS}


async def _show_slots(event: Message | CallbackQuery) -> None:
    slots = await _slots()
    card = renderer.render_admin_videos_card(slots)
    keyboard = keyboards.admin_videos_keyboard(slots)
    if isinstance(event, CallbackQuery):
        await edit_card(event, card, keyboard)
    else:
        await send_card(event, card, keyboard)


@router.callback_query(F.data == CB.ADMIN_VIDEOS)
async def handle_videos(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.clear()
    await _show_slots(call)


@router.callback_query(F.data.startswith(CB.ADMIN_VIDEO_SET))
async def handle_video_slot(call: CallbackQuery, state: FSMContext) -> None:
    slot = _tail(call.data, CB.ADMIN_VIDEO_SET)
    if slot not in SLOTS:
        await toast(call, f"◇ {sc('unknown slot')}", alert=True)
        return

    await call.answer()
    await state.update_data(slot=slot)
    await state.set_state(AdminVideoStates.waiting_for_video)
    await edit_card(
        call,
        renderer.render_error_card(
            "send a video",
            f"send the video for the {SLOT_LABELS[slot]} card",
        ),
        keyboards.admin_back_keyboard(),
    )


@router.message(AdminVideoStates.waiting_for_video, F.video)
async def handle_video_upload(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    slot = str(data.get("slot") or "")
    if slot not in SLOTS or message.video is None:
        await state.clear()
        await send_card(
            message,
            renderer.render_error_card("slot lost", "start again from the videos page"),
            keyboards.admin_back_keyboard(),
        )
        return

    await database.set_config(slot, message.video.file_id, message.from_user.id)
    await database.log_admin(message.from_user.id, "video_set", slot)
    await state.clear()
    logger.info("video slot %s updated by %s", slot, message.from_user.id)
    await _show_slots(message)


@router.message(AdminVideoStates.waiting_for_video)
async def handle_video_wrong_type(message: Message) -> None:
    await send_card(
        message,
        renderer.render_error_card("video required", "send it as a video, not a file or link"),
        keyboards.admin_back_keyboard(),
    )


def get_router() -> Router:
    return router

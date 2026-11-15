"""Runtime bot configuration: maintenance, rotation, capacity, forwarding."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import core.api_manager as api_manager
import core.database as database
from core.middlewares import invalidate_maintenance_cache
from handlers.common import IsAdmin, edit_card, send_card, toast
from ui import keyboards, renderer
from ui.states import AdminConfigStates, CB
from ui.typography import sc

router = Router()
logger = logging.getLogger(__name__)

router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

ROTATION_MODES = ("round_robin", "priority", "random")
PROMPTS = {
    "hit_forward_channel": "send the channel id (-100…) or @username, or send none to disable",
    "max_concurrent": "send the global session ceiling, for example 20",
}


def _tail(data: str, prefix: str) -> str:
    return data[len(prefix):] if data.startswith(prefix) else ""


async def _show_config(event: Message | CallbackQuery) -> None:
    config = await database.all_config()
    cluster = await api_manager.cluster_summary()
    card = renderer.render_admin_config_card(config, cluster)
    keyboard = keyboards.admin_config_keyboard(config)
    if isinstance(event, CallbackQuery):
        await edit_card(event, card, keyboard)
    else:
        await send_card(event, card, keyboard)


@router.callback_query(F.data == CB.ADMIN_CONFIG)
async def handle_config(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.clear()
    await _show_config(call)


@router.callback_query(F.data.startswith(CB.ADMIN_CONFIG_TOGGLE))
async def handle_config_toggle(call: CallbackQuery) -> None:
    key = _tail(call.data, CB.ADMIN_CONFIG_TOGGLE)
    if key != "maintenance_mode":
        await toast(call, f"◇ {sc('unknown switch')}", alert=True)
        return

    current = bool(await database.get_config(key, False))
    await database.set_config(key, not current, call.from_user.id)
    invalidate_maintenance_cache()
    await database.log_admin(call.from_user.id, "config_toggle", key, {"value": not current})
    await call.answer(f"✦ {sc('maintenance on') if not current else sc('maintenance off')}")
    await _show_config(call)


@router.callback_query(F.data.startswith(CB.ADMIN_CONFIG_ROTATION))
async def handle_config_rotation(call: CallbackQuery) -> None:
    current = str(await database.get_config("api_rotation_mode", "round_robin"))
    try:
        index = ROTATION_MODES.index(current)
    except ValueError:
        index = 0
    nxt = ROTATION_MODES[(index + 1) % len(ROTATION_MODES)]

    await database.set_config("api_rotation_mode", nxt, call.from_user.id)
    await database.log_admin(call.from_user.id, "config_rotation", nxt)
    await call.answer(f"✦ {sc(nxt)}")
    await _show_config(call)


@router.callback_query(F.data.startswith(CB.ADMIN_CONFIG_SET))
async def handle_config_set_prompt(call: CallbackQuery, state: FSMContext) -> None:
    key = _tail(call.data, CB.ADMIN_CONFIG_SET)
    if key not in PROMPTS:
        await toast(call, f"◇ {sc('unknown setting')}", alert=True)
        return

    await call.answer()
    await state.update_data(key=key)
    await state.set_state(AdminConfigStates.waiting_for_value)
    await edit_card(
        call,
        renderer.render_error_card(f"set {key.replace('_', ' ')}", PROMPTS[key]),
        keyboards.admin_back_keyboard(),
    )


@router.message(AdminConfigStates.waiting_for_value)
async def handle_config_value(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    key = str(data.get("key") or "")
    raw = (message.text or "").strip()

    if key == "max_concurrent":
        if not raw.isdigit() or int(raw) < 1:
            await send_card(
                message,
                renderer.render_error_card("invalid number", "send a whole number above zero"),
                keyboards.admin_back_keyboard(),
            )
            return
        await database.set_config(key, int(raw), message.from_user.id)
        saved = int(raw)
    elif key == "hit_forward_channel":
        saved = "" if raw.lower() in ("none", "off", "-", "") else raw
        await database.set_config(key, saved, message.from_user.id)
    else:
        await state.clear()
        await send_card(
            message,
            renderer.render_error_card("setting lost", "open the config page again"),
            keyboards.admin_back_keyboard(),
        )
        return

    await state.clear()
    await database.log_admin(message.from_user.id, "config_set", key, {"value": saved})
    await _show_config(message)


def get_router() -> Router:
    return router

"""Plan key generation."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import core.database as database
from handlers.common import IsAdmin, edit_card, send_card, toast
from services import key_service
from ui import keyboards, renderer
from ui.states import AdminKeyStates, CB
from ui.typography import sc

router = Router()
logger = logging.getLogger(__name__)

router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


def _tail(data: str, prefix: str) -> str:
    return data[len(prefix):] if data.startswith(prefix) else ""


async def _show_keys(event: Message | CallbackQuery) -> None:
    stats = await key_service.key_stats()
    keys = await key_service.list_keys(limit=6)
    card = renderer.render_admin_keys_card(stats, keys)
    keyboard = keyboards.admin_keys_keyboard()
    if isinstance(event, CallbackQuery):
        await edit_card(event, card, keyboard)
    else:
        await send_card(event, card, keyboard)


@router.callback_query(F.data == CB.ADMIN_KEYS)
async def handle_keys(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.clear()
    await _show_keys(call)


@router.callback_query(F.data.startswith(CB.ADMIN_KEYS_PLAN))
async def handle_key_plan(call: CallbackQuery, state: FSMContext) -> None:
    plan = _tail(call.data, CB.ADMIN_KEYS_PLAN)
    if plan not in ("basic", "premium"):
        await toast(call, f"◇ {sc('unknown plan')}", alert=True)
        return

    await call.answer()
    await state.update_data(plan=plan)
    await edit_card(
        call,
        renderer.render_error_card("choose a duration", f"how long should {plan} keys last?"),
        keyboards.admin_key_duration_keyboard(plan),
    )


@router.callback_query(F.data.startswith(CB.ADMIN_KEYS_CONFIRM))
async def handle_key_duration(call: CallbackQuery, state: FSMContext) -> None:
    payload = _tail(call.data, CB.ADMIN_KEYS_CONFIRM)
    plan, _, raw_days = payload.partition(":")
    if plan not in ("basic", "premium") or not raw_days.isdigit():
        await toast(call, f"◇ {sc('invalid selection')}", alert=True)
        return

    days = int(raw_days)
    await call.answer()
    await state.update_data(plan=plan, days=days)
    await state.set_state(AdminKeyStates.waiting_for_count)
    await edit_card(
        call,
        renderer.render_error_card(
            "how many keys",
            f"send a number between 1 and 100 for {plan} · {days} days",
        ),
        keyboards.admin_back_keyboard(),
    )


@router.message(AdminKeyStates.waiting_for_count)
async def handle_key_count(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit() or not (1 <= int(raw) <= 100):
        await send_card(
            message,
            renderer.render_error_card("invalid count", "send a number between 1 and 100"),
            keyboards.admin_back_keyboard(),
        )
        return

    count = int(raw)
    data = await state.get_data()
    plan = str(data.get("plan") or "basic")
    days = int(data.get("days") or 30)
    await state.clear()

    try:
        keys = await key_service.generate_keys(plan, days, count, message.from_user.id)
    except ValueError as exc:
        await send_card(
            message,
            renderer.render_error_card("generation failed", str(exc)),
            keyboards.admin_back_keyboard(),
        )
        return
    except Exception as exc:
        logger.error("key generation failed: %s", exc)
        await send_card(
            message,
            renderer.render_error_card("generation failed", "database rejected the keys, try again"),
            keyboards.admin_back_keyboard(),
        )
        return

    await database.log_admin(
        message.from_user.id, "keys_generate", plan, {"count": len(keys), "days": days}
    )
    await send_card(
        message,
        renderer.render_admin_keys_result_card(plan, days, keys),
        keyboards.admin_keys_keyboard(),
    )


def get_router() -> Router:
    return router

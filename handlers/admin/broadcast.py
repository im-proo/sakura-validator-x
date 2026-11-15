"""Broadcast composer: preview first, then fan out in the background."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import core.database as database
from handlers.common import IsAdmin, edit_card, send_card, toast
from ui import keyboards, renderer
from ui.states import AdminBroadcastStates, CB
from ui.typography import fmt_num, sc

router = Router()
logger = logging.getLogger(__name__)

router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

SEND_DELAY = 0.05


async def _recipient_ids() -> list[int]:
    cursor = database.users().find({"is_banned": {"$ne": True}}, {"user_id": 1})
    return [int(doc["user_id"]) async for doc in cursor if doc.get("user_id")]


async def _run_broadcast(
    bot: Bot,
    admin_id: int,
    source_chat: int,
    source_message: int,
    total: int,
) -> None:
    """Fan the copied message out to every active user."""
    sent = 0
    failed = 0
    for user_id in await _recipient_ids():
        try:
            await bot.copy_message(
                chat_id=user_id,
                from_chat_id=source_chat,
                message_id=source_message,
            )
            sent += 1
        except Exception as exc:
            failed += 1
            logger.debug("broadcast to %s failed: %s", user_id, exc)
        await asyncio.sleep(SEND_DELAY)

    await database.log_admin(admin_id, "broadcast", f"{total} users", {"sent": sent, "failed": failed})
    try:
        await bot.send_rich_message(
            chat_id=admin_id,
            rich_message=renderer.render_error_card(
                "broadcast finished",
                f"delivered {fmt_num(sent)} · failed {fmt_num(failed)}",
            ),
            reply_markup=keyboards.admin_dashboard_keyboard(),
        )
    except Exception as exc:
        logger.warning("broadcast report failed: %s", exc)


@router.callback_query(F.data == CB.ADMIN_BROADCAST)
async def handle_broadcast_prompt(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.clear()
    await state.set_state(AdminBroadcastStates.waiting_for_message)
    await edit_card(call, renderer.render_admin_broadcast_card(), keyboards.admin_back_keyboard())


@router.message(AdminBroadcastStates.waiting_for_message)
async def handle_broadcast_message(message: Message, state: FSMContext) -> None:
    recipients = len(await _recipient_ids())
    if recipients == 0:
        await state.clear()
        await send_card(
            message,
            renderer.render_error_card("nobody to message", "the user list is empty"),
            keyboards.admin_dashboard_keyboard(),
        )
        return

    await state.update_data(
        source_chat=message.chat.id,
        source_message=message.message_id,
        recipients=recipients,
    )
    await state.set_state(AdminBroadcastStates.confirming)

    preview_text = (message.text or message.caption or "").strip() or sc("media message")

    try:
        await message.bot.copy_message(
            chat_id=message.chat.id,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
            reply_markup=keyboards.admin_broadcast_confirm_keyboard(),
        )
    except Exception as exc:
        logger.warning("broadcast preview copy failed: %s", exc)

    await send_card(
        message,
        renderer.render_admin_broadcast_preview_card(preview_text, recipients),
        keyboards.admin_broadcast_confirm_keyboard(),
    )


@router.callback_query(F.data == CB.ADMIN_BROADCAST_SEND)
async def handle_broadcast_send(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    source_chat = int(data.get("source_chat") or 0)
    source_message = int(data.get("source_message") or 0)
    recipients = int(data.get("recipients") or 0)
    await state.clear()

    if not source_chat or not source_message:
        await toast(call, f"◇ {sc('nothing to send')}", alert=True)
        return

    await call.answer(f"✦ {sc('broadcast started')}")
    asyncio.create_task(
        _run_broadcast(call.bot, call.from_user.id, source_chat, source_message, recipients),
        name="admin-broadcast",
    )
    await edit_card(
        call,
        renderer.render_error_card(
            "broadcast running",
            f"sending to {fmt_num(recipients)} users, you will get a report",
        ),
        keyboards.admin_dashboard_keyboard(),
    )


@router.callback_query(F.data == CB.ADMIN_BROADCAST_CANCEL)
async def handle_broadcast_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer(f"◇ {sc('broadcast cancelled')}")
    from handlers.admin.dashboard import dashboard_payload, dashboard_card

    numbers, cluster, system, sessions = await dashboard_payload()
    await edit_card(
        call,
        dashboard_card(numbers, cluster, system, sessions),
        keyboards.admin_dashboard_keyboard(),
    )


def get_router() -> Router:
    return router

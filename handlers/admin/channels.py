"""Force-join channel management."""

from __future__ import annotations

import logging
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import core.database as database
from handlers.common import IsAdmin, edit_card, send_card, toast
from models.schemas import utcnow
from ui import keyboards, renderer
from ui.states import AdminChannelStates, CB
from ui.typography import sc

router = Router()
logger = logging.getLogger(__name__)

router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

ADMIN_STATUSES = {"administrator", "creator", "owner"}


def _tail(data: str, prefix: str) -> str:
    return data[len(prefix):] if data.startswith(prefix) else ""


async def _list_channels(event: Message | CallbackQuery) -> None:
    cursor = database.channels().find({}).sort("order", 1)
    channels = [doc async for doc in cursor]
    card = renderer.render_admin_channels_card(channels)
    keyboard = keyboards.admin_channels_keyboard(channels)
    if isinstance(event, CallbackQuery):
        await edit_card(event, card, keyboard)
    else:
        await send_card(event, card, keyboard)


async def _insert_channel(
    channel_id: str, name: str, link: str, added_by: int
) -> dict[str, Any]:
    order = int(await database.channels().count_documents({})) + 1
    doc = {
        "channel_id": channel_id,
        "channel_name": name[:60],
        "channel_link": link,
        "is_active": True,
        "order": order,
        "added_by": int(added_by),
        "added_at": utcnow(),
    }
    await database.channels().update_one(
        {"channel_id": channel_id}, {"$set": doc}, upsert=True
    )
    return doc


@router.callback_query(F.data == CB.ADMIN_CHANNELS)
async def handle_channel_list(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.clear()
    await _list_channels(call)


@router.callback_query(F.data == CB.ADMIN_CHANNEL_ADD)
async def handle_channel_add(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.clear()
    await state.set_state(AdminChannelStates.waiting_for_channel_id)
    await edit_card(
        call,
        renderer.render_error_card(
            "add a channel",
            "send the channel @username or -100 id, and make me an admin there",
        ),
        keyboards.admin_back_keyboard(),
    )


@router.message(AdminChannelStates.waiting_for_channel_id)
async def handle_channel_id(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw:
        await send_card(
            message,
            renderer.render_error_card("channel required", "send the @username or -100 id"),
            keyboards.admin_back_keyboard(),
        )
        return

    try:
        chat = await message.bot.get_chat(raw)
    except Exception as exc:
        logger.info("channel resolve failed %s: %s", raw, exc)
        await send_card(
            message,
            renderer.render_error_card(
                "channel not reachable",
                "add me as an admin in that channel, then send the id again",
            ),
            keyboards.admin_back_keyboard(),
        )
        return

    try:
        me = await message.bot.get_me()
        member = await message.bot.get_chat_member(chat.id, me.id)
        if str(getattr(member, "status", "")).lower() not in ADMIN_STATUSES:
            await send_card(
                message,
                renderer.render_error_card(
                    "not an admin there",
                    "promote me to admin in that channel so I can verify members",
                ),
                keyboards.admin_back_keyboard(),
            )
            return
    except Exception as exc:
        logger.warning("channel permission probe failed: %s", exc)

    name = str(getattr(chat, "title", "") or raw)
    username = getattr(chat, "username", None)
    link = f"https://t.me/{username}" if username else str(getattr(chat, "invite_link", "") or "")

    if not link:
        await state.update_data(channel_id=str(chat.id), channel_name=name)
        await state.set_state(AdminChannelStates.waiting_for_channel_link)
        await send_card(
            message,
            renderer.render_error_card(
                "invite link needed",
                "the channel is private, send an invite link",
            ),
            keyboards.admin_back_keyboard(),
        )
        return

    await state.clear()
    await _insert_channel(str(chat.id), name, link, message.from_user.id)
    await database.log_admin(message.from_user.id, "channel_add", str(chat.id), {"name": name})
    await _list_channels(message)


@router.message(AdminChannelStates.waiting_for_channel_link)
async def handle_channel_link(message: Message, state: FSMContext) -> None:
    link = (message.text or "").strip()
    data = await state.get_data()
    channel_id = str(data.get("channel_id") or "")
    name = str(data.get("channel_name") or channel_id)

    if not link.startswith(("https://", "http://")):
        await send_card(
            message,
            renderer.render_error_card("invalid link", "send a full https:// invite link"),
            keyboards.admin_back_keyboard(),
        )
        return

    await state.clear()
    await _insert_channel(channel_id, name, link, message.from_user.id)
    await database.log_admin(message.from_user.id, "channel_add", channel_id, {"name": name})
    await _list_channels(message)


@router.callback_query(F.data.startswith(CB.ADMIN_CHANNEL_TOGGLE))
async def handle_channel_toggle(call: CallbackQuery) -> None:
    channel_id = _tail(call.data, CB.ADMIN_CHANNEL_TOGGLE)
    doc = await database.channels().find_one({"channel_id": channel_id})
    if doc is None:
        await toast(call, f"◇ {sc('channel not found')}", alert=True)
        return

    new_state = not bool(doc.get("is_active"))
    await database.channels().update_one(
        {"channel_id": channel_id}, {"$set": {"is_active": new_state}}
    )
    await database.log_admin(call.from_user.id, "channel_toggle", channel_id, {"active": new_state})
    await call.answer(f"✦ {sc('enabled') if new_state else sc('disabled')}")
    await _list_channels(call)


def get_router() -> Router:
    return router

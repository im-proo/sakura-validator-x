"""Admin user manager: browse, inspect, ban/unban, grant plans."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import core.database as database
from handlers.common import IsAdmin, edit_card, send_card, toast
from services import plan_service
from ui import keyboards, renderer
from ui.states import AdminUserStates, CB
from ui.typography import sc

router = Router()
logger = logging.getLogger(__name__)

router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


def _tail(data: str, prefix: str) -> str:
    return data[len(prefix):] if data.startswith(prefix) else ""


async def _show_detail(event: Message | CallbackQuery, user_id: int) -> None:
    target = await plan_service.get_user(user_id)
    if target is None:
        await send_card(
            event,
            renderer.render_error_card("user not found", "no account with that id"),
            keyboards.admin_back_keyboard(),
        )
        return
    rank = await plan_service.get_rank(user_id)
    card = renderer.render_admin_user_card(target, rank)
    keyboard = keyboards.admin_user_actions_keyboard(user_id, bool(target.get("is_banned")))
    if isinstance(event, CallbackQuery):
        await edit_card(event, card, keyboard)
    else:
        await send_card(event, card, keyboard)


@router.callback_query(F.data == CB.ADMIN_USERS)
async def handle_users_list(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.clear()
    page_data = await plan_service.admin_user_page(0)
    await edit_card(call, renderer.render_admin_users_card(page_data), keyboards.admin_users_keyboard(0, page_data["pages"]))


@router.callback_query(F.data.startswith(CB.ADMIN_USERS_PAGE))
async def handle_users_page(call: CallbackQuery) -> None:
    await call.answer()
    raw = _tail(call.data, CB.ADMIN_USERS_PAGE) or "0"
    try:
        page = max(0, int(raw))
    except ValueError:
        page = 0
    page_data = await plan_service.admin_user_page(page)
    await edit_card(
        call,
        renderer.render_admin_users_card(page_data),
        keyboards.admin_users_keyboard(page_data["page"], page_data["pages"]),
    )


@router.callback_query(F.data == CB.ADMIN_USERS_SEARCH)
async def handle_users_search(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.set_state(AdminUserStates.waiting_for_user_id)
    await edit_card(
        call,
        renderer.render_error_card("find a user", "send the numeric user id"),
        keyboards.admin_back_keyboard(),
    )


@router.message(AdminUserStates.waiting_for_user_id)
async def handle_user_id_input(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await send_card(
            message,
            renderer.render_error_card("digits only", "user ids are numeric, for example 123456789"),
            keyboards.admin_back_keyboard(),
        )
        return
    await state.clear()
    await _show_detail(message, int(raw))


@router.callback_query(F.data.startswith(CB.ADMIN_USER_BAN))
async def handle_user_ban_prompt(call: CallbackQuery, state: FSMContext) -> None:
    raw = _tail(call.data, CB.ADMIN_USER_BAN)
    if not raw.isdigit():
        await toast(call, f"◇ {sc('invalid user')}", alert=True)
        return
    await call.answer()
    await state.update_data(target=raw)
    await state.set_state(AdminUserStates.waiting_for_ban_reason)
    await edit_card(
        call,
        renderer.render_error_card("ban reason", f"send a short reason for banning {raw}"),
        keyboards.admin_back_keyboard(),
    )


@router.message(AdminUserStates.waiting_for_ban_reason)
async def handle_ban_reason(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    target = str(data.get("target") or "")
    reason = (message.text or "").strip()[:200] or "policy violation"
    await state.clear()

    if not target.isdigit():
        await send_card(
            message,
            renderer.render_error_card("ban failed", "the target user was lost, try again"),
            keyboards.admin_back_keyboard(),
        )
        return

    await database.users().update_one(
        {"user_id": int(target)}, {"$set": {"is_banned": True, "ban_reason": reason}}
    )
    await database.log_admin(message.from_user.id, "ban", target, {"reason": reason})
    logger.info("admin %s banned %s", message.from_user.id, target)
    await _show_detail(message, int(target))


@router.callback_query(F.data.startswith(CB.ADMIN_USER_UNBAN))
async def handle_user_unban(call: CallbackQuery) -> None:
    raw = _tail(call.data, CB.ADMIN_USER_UNBAN)
    if not raw.isdigit():
        await toast(call, f"◇ {sc('invalid user')}", alert=True)
        return
    await database.users().update_one(
        {"user_id": int(raw)}, {"$set": {"is_banned": False, "ban_reason": ""}}
    )
    await database.log_admin(call.from_user.id, "unban", raw)
    await call.answer(f"✦ {sc('unbanned')}")
    await _show_detail(call, int(raw))


@router.callback_query(F.data.startswith(CB.ADMIN_USER_PLAN_SET))
async def handle_user_plan_set(call: CallbackQuery) -> None:
    payload = _tail(call.data, CB.ADMIN_USER_PLAN_SET)
    parts = payload.split(":")
    if len(parts) != 2 or not parts[0].isdigit():
        await toast(call, f"◇ {sc('invalid selection')}", alert=True)
        return

    user_id, plan = int(parts[0]), parts[1]

    if plan == "free":
        await plan_service.grant_plan(user_id, "free", None)
        await database.log_admin(call.from_user.id, "plan_free", str(user_id))
    elif plan in ("basic", "premium"):
        await plan_service.grant_plan(user_id, plan, 30)
        await database.log_admin(call.from_user.id, "plan_grant", str(user_id), {"plan": plan, "days": 30})
    else:
        await toast(call, f"◇ {sc('unknown plan')}", alert=True)
        return

    await call.answer(f"✦ {sc(plan)} {sc('applied')}")
    await _show_detail(call, user_id)


@router.callback_query(F.data.startswith(CB.ADMIN_USER_PLAN))
async def handle_user_plan_prompt(call: CallbackQuery) -> None:
    raw = _tail(call.data, CB.ADMIN_USER_PLAN)
    if not raw.isdigit():
        await toast(call, f"◇ {sc('invalid user')}", alert=True)
        return
    await call.answer()
    await edit_card(
        call,
        renderer.render_error_card("grant a plan", f"choose a plan for {raw} (30 days)"),
        keyboards.admin_user_plan_keyboard(int(raw)),
    )


@router.callback_query(F.data.startswith(CB.ADMIN_USER))
async def handle_user_detail(call: CallbackQuery) -> None:
    raw = _tail(call.data, CB.ADMIN_USER)
    if not raw.isdigit():
        await toast(call, f"◇ {sc('invalid user')}", alert=True)
        return
    await call.answer()
    await _show_detail(call, int(raw))


def get_router() -> Router:
    return router

"""/redeem key activation and the owner-only /sakura grant."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

import config
import core.database as database
from handlers.common import send_card
from services import key_service, plan_service
from ui import keyboards, renderer

router = Router()
logger = logging.getLogger(__name__)


@router.message(Command("redeem"))
async def handle_redeem(message: Message, command: CommandObject) -> None:
    if message.from_user is None:
        return

    raw_key = (command.args or "").strip()
    if not raw_key:
        await send_card(
            message,
            renderer.render_error_card(
                "key required",
                "use /redeem SAKURA-XXXX-XXXX-XXXX",
            ),
            keyboards.back_to_menu_keyboard(),
        )
        return

    user = await plan_service.ensure_user(message.from_user)
    result = await key_service.redeem_key(raw_key, int(user["user_id"]))

    if not result.get("ok"):
        logger.info("redeem failed user=%s: %s", user["user_id"], result.get("error"))
        await send_card(
            message,
            renderer.render_error_card("redeem failed", str(result.get("error") or "try again")),
            keyboards.plans_keyboard(),
        )
        return

    fresh = await plan_service.get_user(int(user["user_id"])) or user
    await send_card(
        message,
        renderer.render_receipt_card(result, fresh),
        keyboards.receipt_keyboard(),
    )
    logger.info(
        "redeem ok user=%s plan=%s days=%s",
        user["user_id"], result.get("plan"), result.get("days"),
    )


@router.message(Command("sakura"), F.from_user.id == config.settings.owner_id)
async def handle_sakura_grant(message: Message, command: CommandObject) -> None:
    """Owner grants the permanent Sakura plan: /sakura <user_id>."""
    if message.from_user is None:
        return

    raw = (command.args or "").strip()
    try:
        target_id = int(raw.split()[0])
    except (IndexError, ValueError):
        await send_card(
            message,
            renderer.render_error_card("user id required", "use /sakura 123456789"),
            keyboards.admin_back_keyboard(),
        )
        return

    target = await plan_service.get_user(target_id)
    if target is None:
        await send_card(
            message,
            renderer.render_error_card(
                "user not found",
                "the user must open the bot with /start first",
            ),
            keyboards.admin_back_keyboard(),
        )
        return

    await plan_service.grant_plan(target_id, "sakura", None)
    fresh = await plan_service.get_user(target_id) or target

    result = {
        "ok": True,
        "plan": "sakura",
        "days": 0,
        "expires": None,
        "receipt_id": key_service.new_receipt_id(),
    }
    await send_card(message, renderer.render_receipt_card(result, fresh), keyboards.receipt_keyboard())

    try:
        await message.bot.send_rich_message(
            chat_id=target_id,
            rich_message=renderer.render_receipt_card(result, fresh),
            reply_markup=keyboards.receipt_keyboard(),
        )
    except Exception as exc:
        logger.warning("sakura notice failed target=%s: %s", target_id, exc)

    await database.log_admin(
        message.from_user.id,
        "sakura_grant",
        str(target_id),
        {"plan": "sakura"},
    )
    logger.info("sakura granted to %s by %s", target_id, message.from_user.id)


def get_router() -> Router:
    return router

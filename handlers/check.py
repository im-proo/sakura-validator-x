"""Cookie submission and check launch (Section 14 state machine).

IDLE → waiting_for_file → waiting_for_mode → checking

No attempt is spent until the checker API has accepted the job, and only
the service spends it (see services/checker_service.run_check).
"""

from __future__ import annotations

import logging
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import core.api_manager as api_manager
import core.database as database
from handlers.common import edit_card, send_card, toast
from handlers.start import show_main_menu
from models.schemas import concurrency as plan_concurrency
from models.schemas import max_cookies
from services import checker_service, file_service, plan_service
from ui import keyboards, renderer
from ui.states import CB, CheckStates
from ui.typography import fmt_num, sc

router = Router()
logger = logging.getLogger(__name__)


async def _current_user(event: Message | CallbackQuery) -> dict[str, Any] | None:
    """Load, lazily reset and return the caller's document."""
    if event.from_user is None:
        return None
    user = await plan_service.get_user(event.from_user.id)
    if user is None:
        user = await plan_service.ensure_user(event.from_user)
    return await plan_service.refresh_daily(user)


async def _open_instructions(event: Message | CallbackQuery, state: FSMContext) -> None:
    """Show the upload instructions and arm the file state."""
    user = await _current_user(event)
    if user is None:
        return

    if plan_service.limit_reached(user):
        await send_card(
            event,
            renderer.render_daily_limit_card(user),
            keyboards.daily_limit_keyboard(),
        )
        return

    if api_manager.user_session_count(int(user["user_id"])) > 0:
        await send_card(
            event,
            renderer.render_error_card("check already running", "wait for it to finish or cancel it"),
            keyboards.back_to_menu_keyboard(),
        )
        return

    # Drop any leftover upload from an abandoned flow.
    previous = (await state.get_data()).get("file_path")
    if previous:
        file_service.cleanup(previous)

    await state.clear()
    await state.set_state(CheckStates.waiting_for_file)

    card = renderer.render_check_instructions_card(user)
    if isinstance(event, CallbackQuery):
        await edit_card(event, card, keyboards.check_instructions_keyboard())
    else:
        await send_card(event, card, keyboards.check_instructions_keyboard())


@router.message(Command("check"))
async def handle_check_command(message: Message, state: FSMContext) -> None:
    await _open_instructions(message, state)


@router.callback_query(F.data == CB.MENU_CHECK)
async def handle_check_button(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await _open_instructions(call, state)


@router.message(CheckStates.waiting_for_file, F.document)
async def handle_cookie_document(message: Message, state: FSMContext) -> None:
    """Validate the upload; never deduct an attempt here."""
    user = await _current_user(message)
    if user is None:
        return

    if plan_service.limit_reached(user):
        await state.clear()
        await send_card(
            message,
            renderer.render_daily_limit_card(user),
            keyboards.daily_limit_keyboard(),
        )
        return

    plan = str(user.get("plan") or "free")
    document = message.document
    try:
        file_info = await file_service.handle_user_cookie_file(
            document, message.bot, max_cookies(plan)
        )
    except file_service.InvalidFileError as exc:
        logger.info("invalid upload user=%s: %s", user["user_id"], exc)
        await send_card(
            message,
            renderer.render_error_card("invalid file", str(exc)),
            keyboards.check_instructions_keyboard(),
        )
        return
    except file_service.PlanLimitError as exc:
        logger.info("plan limit hit user=%s: %s", user["user_id"], exc)
        await state.clear()
        await send_card(
            message,
            renderer.render_error_card(
                "plan limit",
                f"file holds about {fmt_num(exc.estimated)} cookies, your plan allows "
                f"{fmt_num(exc.limit)}",
            ),
            keyboards.daily_limit_keyboard(),
        )
        return
    except Exception as exc:  # pragma: no cover - network/driver failure
        logger.error("upload handling failed user=%s: %s", user["user_id"], exc)
        await send_card(
            message,
            renderer.render_error_card("upload failed", "please resend the file"),
            keyboards.check_instructions_keyboard(),
        )
        return

    await state.update_data(
        file_path=file_info["file_path"],
        file_name=file_info["file_name"],
        estimated_count=file_info["estimated_count"],
        extension=file_info["extension"],
    )
    await state.set_state(CheckStates.waiting_for_mode)

    await send_card(
        message,
        renderer.render_cookie_confirm_card(user, file_info),
        keyboards.cookie_confirm_keyboard(),
    )


@router.message(CheckStates.waiting_for_file, ~F.text.startswith("/"))
async def handle_wrong_upload(message: Message, state: FSMContext) -> None:
    """Anything that is not a document gets a guiding card, state preserved.

    Commands are excluded so /start, /stats and admin commands still work
    while the file state is armed.
    """
    await send_card(
        message,
        renderer.render_error_card(
            "document required",
            "send the cookies file as a document, not text or a photo",
        ),
        keyboards.check_instructions_keyboard(),
    )


@router.message(CheckStates.waiting_for_mode, F.document)
async def handle_second_upload(message: Message, state: FSMContext) -> None:
    await send_card(
        message,
        renderer.render_error_card(
            "file already loaded",
            "pick a hit mode below, or cancel and send a different file",
        ),
        keyboards.cookie_confirm_keyboard(),
    )


async def _start_check(call: CallbackQuery, state: FSMContext, hit_mode: str) -> None:
    """Launch the job after every precondition is satisfied."""
    data = await state.get_data()
    file_path = str(data.get("file_path") or "")
    file_name = str(data.get("file_name") or "cookies.txt")

    if not file_path:
        await toast(call, f"◇ {sc('session expired, send the file again')}", alert=True)
        await state.clear()
        return

    user = await _current_user(call)
    if user is None:
        return

    if plan_service.limit_reached(user):
        file_service.cleanup(file_path)
        await state.clear()
        await edit_card(call, renderer.render_daily_limit_card(user), keyboards.daily_limit_keyboard())
        await toast(call, f"◇ {sc('daily limit reached')}")
        return

    plan = str(user.get("plan") or "free")
    if api_manager.user_session_count(int(user["user_id"])) >= plan_concurrency(plan):
        await toast(
            call,
            f"◇ {sc('your plan allows')} {plan_concurrency(plan)} {sc('checks at once')}",
            alert=True,
        )
        return

    if not api_manager.capacity_available():
        file_service.cleanup(file_path)
        await state.clear()
        await edit_card(
            call,
            renderer.render_error_card(
                "cluster busy",
                "every checker is busy right now, please try again in a minute",
            ),
            keyboards.back_to_menu_keyboard(),
        )
        await toast(call, f"◇ {sc('cluster busy')}")
        return

    api = await api_manager.balancer.select_api()
    if api is None:
        file_service.cleanup(file_path)
        await state.clear()
        await edit_card(
            call,
            renderer.render_error_card(
                "all checker nodes offline",
                "no attempt was used, please try again later",
            ),
            keyboards.back_to_menu_keyboard(),
        )
        await toast(call, f"◇ {sc('no nodes online')}")
        return

    session = await checker_service.create_session(
        user=user,
        api=api,
        hit_mode=hit_mode,
        chat_id=call.message.chat.id,
    )

    # The dashboard video: the checking slot when one is set, otherwise the
    # main-menu video, so the same clip frames every card by default.
    video = str(await database.get_config("checking_video", "") or "") or str(
        await database.get_config("main_menu_video", "") or ""
    )
    session.video = video
    dashboard = await call.bot.send_rich_message(
        chat_id=call.message.chat.id,
        rich_message=renderer.render_live_card(session, video=video),
        reply_markup=keyboards.live_check_keyboard(session.session_id),
    )
    session.message_id = dashboard.message_id

    checker_service.launch(
        call.bot,
        session,
        {"file_path": file_path, "file_name": file_name},
    )
    await state.clear()
    await toast(call, f"✦ {sc('check started')}")


@router.callback_query(F.data == CB.CHECK_MODE_PREMIUM)
async def handle_mode_premium(call: CallbackQuery, state: FSMContext) -> None:
    await _start_check(call, state, "premium_only")


@router.callback_query(F.data == CB.CHECK_MODE_ALL)
async def handle_mode_all(call: CallbackQuery, state: FSMContext) -> None:
    await _start_check(call, state, "all")


@router.callback_query(F.data == CB.CHECK_ABORT)
async def handle_abort(call: CallbackQuery, state: FSMContext) -> None:
    """Leaving before a job starts costs nothing and wipes the upload."""
    data = await state.get_data()
    file_service.cleanup(str(data.get("file_path") or ""))
    await state.clear()
    await call.answer(f"◇ {sc('cancelled')}")

    user = await _current_user(call)
    if user is not None:
        await show_main_menu(call, user)


@router.callback_query(F.data.startswith(f"{CB.CHECK_CANCEL}:"))
async def handle_cancel(call: CallbackQuery, state: FSMContext) -> None:
    """Cancel a running job. The attempt is not refunded (Section 14)."""
    session_id = call.data.split(":", 1)[1] if ":" in call.data else ""
    session = api_manager.get_session(session_id)

    if session is None or int(getattr(session, "user_id", 0)) != int(call.from_user.id):
        await toast(call, f"◇ {sc('this check already ended')}", alert=True)
        return

    api_manager.request_cancel(session_id)
    await state.clear()
    await toast(call, f"◇ {sc('cancelling')} ✦")


def get_router() -> Router:
    return router

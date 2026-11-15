"""Checker cluster administration: add, inspect, toggle, delete, proxies."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import core.api_manager as api_manager
import core.checker_api as checker_api
import core.database as database
from handlers.common import IsAdmin, edit_card, send_card, toast
from ui import keyboards, renderer
from ui.states import AdminAPIStates, CB
from ui.typography import sc

router = Router()
logger = logging.getLogger(__name__)

router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


def _tail(data: str, prefix: str) -> str:
    return data[len(prefix):] if data.startswith(prefix) else ""


async def _show_list(event: Message | CallbackQuery) -> None:
    apis = await api_manager.list_apis()
    cluster = await api_manager.cluster_summary()
    card = renderer.render_admin_apis_card(apis, cluster)
    keyboard = keyboards.admin_apis_keyboard(apis)
    if isinstance(event, CallbackQuery):
        await edit_card(event, card, keyboard)
    else:
        await send_card(event, card, keyboard)


@router.callback_query(F.data == CB.ADMIN_APIS)
async def handle_api_list(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.clear()
    await _show_list(call)


@router.callback_query(F.data == CB.ADMIN_API_ADD)
async def handle_api_add(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.clear()
    await state.set_state(AdminAPIStates.waiting_for_name)
    await edit_card(
        call,
        renderer.render_error_card("new checker node", "send a display name, for example Oracle US-01"),
        keyboards.admin_back_keyboard(),
    )


@router.message(AdminAPIStates.waiting_for_name)
async def handle_api_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()[:60]
    if len(name) < 2:
        await send_card(
            message,
            renderer.render_error_card("name too short", "send at least two characters"),
            keyboards.admin_back_keyboard(),
        )
        return
    await state.update_data(name=name)
    await state.set_state(AdminAPIStates.waiting_for_url)
    await send_card(
        message,
        renderer.render_error_card("node url", "send the base url, for example https://nf-01.example.com"),
        keyboards.admin_back_keyboard(),
    )


@router.message(AdminAPIStates.waiting_for_url)
async def handle_api_url(message: Message, state: FSMContext) -> None:
    url = (message.text or "").strip()
    if not url.startswith(("http://", "https://")):
        await send_card(
            message,
            renderer.render_error_card("invalid url", "the url must start with http:// or https://"),
            keyboards.admin_back_keyboard(),
        )
        return
    await state.update_data(url=url)
    await state.set_state(AdminAPIStates.waiting_for_priority)
    await send_card(
        message,
        renderer.render_error_card("priority", "send a number, 1 is highest (default 1)"),
        keyboards.admin_back_keyboard(),
    )


@router.message(AdminAPIStates.waiting_for_priority)
async def handle_api_priority(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    priority = int(raw) if raw.isdigit() else 1
    data = await state.get_data()
    await state.clear()

    name = str(data.get("name") or "checker")
    url = str(data.get("url") or "")
    if not url:
        await send_card(
            message,
            renderer.render_error_card("add failed", "the url was lost, start again"),
            keyboards.admin_back_keyboard(),
        )
        return

    try:
        api = await api_manager.create_api(name, url, priority, message.from_user.id)
    except Exception as exc:
        logger.error("api create failed: %s", exc)
        await send_card(
            message,
            renderer.render_error_card("add failed", "could not store the node, try again"),
            keyboards.admin_back_keyboard(),
        )
        return

    await database.log_admin(message.from_user.id, "api_add", str(api.get("api_id")), {"url": url})
    await send_card(
        message,
        renderer.render_admin_api_card(api),
        keyboards.admin_api_actions_keyboard(str(api["api_id"]), bool(api.get("is_active"))),
    )


@router.callback_query(F.data == CB.ADMIN_API_PROXIES)
async def handle_proxies_prompt(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.set_state(AdminAPIStates.waiting_for_proxies)
    await edit_card(
        call,
        renderer.render_error_card(
            "upload proxies",
            "send a .txt document or plain text, one proxy per line",
        ),
        keyboards.admin_back_keyboard(),
    )


@router.message(AdminAPIStates.waiting_for_proxies)
async def handle_proxies_upload(message: Message, state: FSMContext) -> None:
    apis = await api_manager.list_apis(active_only=True)
    if not apis:
        await state.clear()
        await send_card(
            message,
            renderer.render_error_card("no nodes", "add a checker node first"),
            keyboards.admin_back_keyboard(),
        )
        return

    content = (message.text or "").strip()
    if message.document is not None:
        try:
            buffer = await message.bot.download(message.document)
            content = buffer.read().decode("utf-8", errors="ignore").strip() if buffer else ""
        except Exception as exc:
            logger.warning("proxy file download failed: %s", exc)
            content = ""

    if not content:
        await send_card(
            message,
            renderer.render_error_card("empty upload", "send the proxy list as text or a .txt file"),
            keyboards.admin_back_keyboard(),
        )
        return

    lines = sum(1 for line in content.splitlines() if line.strip())
    results = []
    for api in apis[:3]:
        try:
            payload = await checker_api.upload_proxies(str(api["url"]), content)
            results.append(f"{api.get('name')}: {payload.get('loaded', lines)}")
        except checker_api.CheckerAPIError as exc:
            results.append(f"{api.get('name')}: {sc('failed')} ({exc})")

    await state.clear()
    await database.log_admin(
        message.from_user.id, "proxies_upload", f"{len(apis)} nodes", {"lines": lines}
    )
    await send_card(
        message,
        renderer.render_error_card("proxies pushed", " · ".join(results)),
        keyboards.admin_back_keyboard(),
    )


@router.callback_query(F.data.startswith(CB.ADMIN_API_TOGGLE))
async def handle_api_toggle(call: CallbackQuery) -> None:
    api_id = _tail(call.data, CB.ADMIN_API_TOGGLE)
    api = await api_manager.get_api(api_id)
    if api is None:
        await toast(call, f"◇ {sc('node not found')}", alert=True)
        return

    new_state = not bool(api.get("is_active"))
    await api_manager.set_api_active(api_id, new_state)
    await database.log_admin(call.from_user.id, "api_toggle", api_id, {"active": new_state})
    await call.answer(f"✦ {sc('enabled') if new_state else sc('disabled')}")
    await _show_detail(call, api_id)


@router.callback_query(F.data.startswith(CB.ADMIN_API_DELETE))
async def handle_api_delete(call: CallbackQuery) -> None:
    api_id = _tail(call.data, CB.ADMIN_API_DELETE)
    try:
        removed = await api_manager.delete_api(api_id)
    except RuntimeError:
        await toast(call, f"◇ {sc('node still has running jobs')}", alert=True)
        return

    if removed:
        await database.log_admin(call.from_user.id, "api_delete", api_id)
        await call.answer(f"◇ {sc('node removed')}")
    else:
        await call.answer(f"◇ {sc('node not found')}", alert=True)
    await _show_list(call)


async def _show_detail(call: CallbackQuery, api_id: str) -> None:
    api = await api_manager.get_api(api_id)
    if api is None:
        await toast(call, f"◇ {sc('node not found')}", alert=True)
        await _show_list(call)
        return

    stats = None
    try:
        stats = await checker_api.server_stats(str(api.get("url") or ""))
    except checker_api.CheckerAPIError as exc:
        logger.debug("node stats unavailable %s: %s", api_id, exc)

    await edit_card(
        call,
        renderer.render_admin_api_card(api, stats),
        keyboards.admin_api_actions_keyboard(api_id, bool(api.get("is_active"))),
    )


@router.callback_query(F.data.startswith(CB.ADMIN_API))
async def handle_api_detail(call: CallbackQuery) -> None:
    api_id = _tail(call.data, CB.ADMIN_API)
    if not api_id or api_id in ("add", "proxies"):
        await toast(call, f"◇ {sc('unknown node')}", alert=True)
        return
    await call.answer()
    await _show_detail(call, api_id)


def get_router() -> Router:
    return router

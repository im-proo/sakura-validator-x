"""Admin control room: cluster, traffic and host telemetry."""

from __future__ import annotations

import logging
import time
from typing import Any

import psutil
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

import core.api_manager as api_manager
from handlers.common import IsAdmin, edit_card, send_card
from services import plan_service
from ui import keyboards, renderer
from ui.states import CB
from ui.typography import minutes_label

router = Router()
logger = logging.getLogger(__name__)

router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

_PROCESS = psutil.Process()


def system_snapshot() -> dict[str, Any]:
    """Host telemetry for the dashboard card."""
    try:
        uptime = time.time() - _PROCESS.create_time()
        return {
            "cpu": psutil.cpu_percent(interval=None),
            "ram": psutil.virtual_memory().percent,
            "disk": psutil.disk_usage("/").percent,
            "uptime": minutes_label(int(uptime)),
        }
    except Exception as exc:  # pragma: no cover - exotic hosts
        logger.warning("system snapshot failed: %s", exc)
        return {"cpu": 0.0, "ram": 0.0, "disk": 0.0, "uptime": "—"}


async def dashboard_payload() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], int]:
    numbers = await plan_service.dashboard_numbers()
    cluster = await api_manager.cluster_summary()
    return numbers, cluster, system_snapshot(), api_manager.total_active()


def dashboard_card(numbers: dict, cluster: dict, system: dict, sessions: int):
    return renderer.render_admin_dashboard_card(numbers, cluster, system, sessions)


@router.message(Command("admin"))
async def handle_admin_command(message: Message) -> None:
    numbers, cluster, system, sessions = await dashboard_payload()
    await send_card(
        message,
        dashboard_card(numbers, cluster, system, sessions),
        keyboards.admin_dashboard_keyboard(),
    )


@router.callback_query(F.data.in_({CB.ADMIN_HOME, CB.ADMIN_REFRESH}))
async def handle_admin_home(call: CallbackQuery) -> None:
    await call.answer()
    numbers, cluster, system, sessions = await dashboard_payload()
    await edit_card(
        call,
        dashboard_card(numbers, cluster, system, sessions),
        keyboards.admin_dashboard_keyboard(),
    )


@router.callback_query(F.data == CB.ADMIN_BOT_STATS)
async def handle_bot_stats(call: CallbackQuery) -> None:
    await call.answer()
    from core import scheduler

    snapshot = await scheduler.snapshot_stats()
    await edit_card(
        call,
        renderer.render_admin_bot_stats_card(snapshot),
        keyboards.admin_bot_stats_keyboard(),
    )


def get_router() -> Router:
    return router

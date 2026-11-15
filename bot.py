"""𝐒𝐚𝐤𝐮𝐫𝐀 𝐕𝐚𝐥𝐢𝐝𝐀𝐭𝐨𝐫 𝐗 — entry point.

◈ Tʜᴇ ˹Nʏʀᴏ ꭙ Bᴏᴛs !! ♡
"""

from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

import config
import webserver
import core.api_manager as api_manager
import core.checker_api as checker_api
import core.database as database
from core.middlewares import BanCheckMiddleware, ForceJoinMiddleware, MaintenanceMiddleware
from core.scheduler import shutdown as scheduler_shutdown
from core.scheduler import start as scheduler_start
from handlers import (
    check,
    help as help_handler,
    leaderboard,
    menu,
    redeem,
    start,
    stats,
    updates,
)
from handlers.admin import (
    api_panel,
    broadcast,
    channels,
    config as admin_config,
    dashboard,
    keys,
    logs,
    users,
    videos,
)
from services import file_service

logger = logging.getLogger("sakura")

COMMANDS = [
    BotCommand(command="start", description="ᴏᴘᴇɴ ᴛʜᴇ ʙᴏᴛ"),
    BotCommand(command="check", description="sᴛᴀʀᴛ ᴄʜᴇᴄᴋɪɴɢ"),
    BotCommand(command="stats", description="ʏᴏᴜʀ sᴛᴀᴛɪsᴛɪᴄs"),
    BotCommand(command="leaderboard", description="ᴛᴏᴘ ᴄʜᴇᴄᴋᴇʀs"),
    BotCommand(command="redeem", description="ᴀᴄᴛɪᴠᴀᴛᴇ ᴀ ᴘʟᴀɴ"),
    BotCommand(command="help", description="ᴄᴏᴍᴍᴀɴᴅs"),
    BotCommand(command="updates", description="ʟᴀᴛᴇsᴛ ᴄʜᴀɴɢᴇs"),
]


def configure_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, config.settings.log_level, logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("pymongo").setLevel(logging.WARNING)


def build_dispatcher() -> Dispatcher:
    """Assemble middlewares and routers in a deliberate order."""
    dp = Dispatcher(storage=MemoryStorage())

    for observer in (dp.message, dp.callback_query):
        observer.middleware(BanCheckMiddleware())
        observer.middleware(MaintenanceMiddleware())
        observer.middleware(ForceJoinMiddleware())

    # user routers
    dp.include_router(start.get_router())
    dp.include_router(menu.get_router())
    dp.include_router(check.get_router())
    dp.include_router(stats.get_router())
    dp.include_router(leaderboard.get_router())
    dp.include_router(help_handler.get_router())
    dp.include_router(updates.get_router())
    dp.include_router(redeem.get_router())

    # admin routers (self-gated with IsAdmin)
    dp.include_router(dashboard.get_router())
    dp.include_router(users.get_router())
    dp.include_router(api_panel.get_router())
    dp.include_router(channels.get_router())
    dp.include_router(videos.get_router())
    dp.include_router(keys.get_router())
    dp.include_router(broadcast.get_router())
    dp.include_router(admin_config.get_router())
    dp.include_router(logs.get_router())

    return dp


async def on_startup(bot: Bot) -> None:
    await database.connect()
    await database.create_indexes()

    # Seed only rows that were never written, so admin edits always win.
    for key, value in database.CONFIG_DEFAULTS.items():
        if await database.bot_config().find_one({"key": key}) is None:
            await database.set_config(key, value, config.settings.owner_id)

    removed = file_service.purge_stale_files()
    if removed:
        logger.info("cleaned %s stale temp files", removed)

    try:
        await bot.set_my_commands(COMMANDS)
    except Exception as exc:
        logger.warning("set_my_commands failed: %s", exc)

    scheduler_start()
    await api_manager.start_health_monitor()

    cluster = await api_manager.cluster_summary()
    logger.info(
        "startup complete: users=%s apis=%s online=%s/%s",
        await database.count_users(), cluster["total"], cluster["online"], cluster["total"],
    )


async def on_shutdown(bot: Bot) -> None:
    logger.info("shutting down")
    scheduler_shutdown()
    await api_manager.stop_health_monitor()
    await checker_api.close_session()
    await database.close()


async def main() -> int:
    configure_logging()

    if not config.settings.is_configured:
        logger.error(
            "BOT_TOKEN and MONGO_URI must be set in .env — copy .env.example and fill it in"
        )
        return 1

    bot = Bot(
        token=config.settings.bot_token,
        default=DefaultBotProperties(parse_mode=None, link_preview_is_disabled=True),
    )
    dp = build_dispatcher()

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    try:
    await bot.delete_webhook(drop_pending_updates=True)
except Exception as exc:
    logger.warning("delete_webhook failed: %s", exc)

await webserver.start()

try:
    await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
finally:
    await bot.session.close()

if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        logger.info("stopped by keyboard interrupt")
        sys.exit(0)

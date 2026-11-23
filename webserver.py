"""
𝐒𝐚𝐤𝐮𝐫𝐀 𝐕𝐚𝐥𝐢𝐝𝐀𝐭𝐨𝐫 𝐗 — Health Web Server
◈ Tʜᴇ ˹Nʏʀᴏ ꭙ Bᴏᴛs !! ♡

The bot itself only talks to Telegram over long polling, so there is no
listen socket unless this module is started. Hosting platforms (Railway,
Render, Koyeb, Fly, …) probe for an open port and restart or stop the
service when they find none — this server gives them one.

Started from ``bot.on_startup`` on ``0.0.0.0:$PORT`` (default 8080) and
stopped from ``bot.on_shutdown``. Keep-alive monitors (UptimeRobot, …)
should watch ``GET /health``.
"""

from __future__ import annotations

import logging
import os
import time

from aiohttp import web

logger = logging.getLogger("sakura.web")

_STARTED_AT = time.monotonic()
_runner: web.AppRunner | None = None


async def health_handler(request: web.Request) -> web.Response:
    return web.json_response({
        "status": "ok",
        "bot": "𝐒𝐚𝐤𝐮𝐫𝐀 𝐕𝐚𝐥𝐢𝐝𝐀𝐭𝐨𝐫 𝐗",
        "brand": "◈ Tʜᴇ ˹Nʏʀᴏ ꭙ Bᴏᴛs !! ♡",
        "uptime_seconds": int(time.monotonic() - _STARTED_AT),
    })


async def start() -> bool:
    """Bind the health endpoint to 0.0.0.0:$PORT. False when it could not bind."""
    global _runner
    if _runner is not None:
        return True

    try:
        port = int(os.getenv("PORT", "8080"))
    except ValueError:
        port = 8080

    app = web.Application()
    app.router.add_get("/", health_handler)
    app.router.add_get("/health", health_handler)

    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    try:
        await site.start()
    except OSError as exc:
        # Never take the bot down because the port is taken; polling still works.
        await runner.cleanup()
        logger.error("health server could not bind 0.0.0.0:%s: %s", port, exc)
        return False

    _runner = runner
    logger.info("health server listening on 0.0.0.0:%s (GET /health)", port)
    return True


async def stop() -> None:
    """Close the listener; safe to call when it never started."""
    global _runner
    if _runner is None:
        return
    runner, _runner = _runner, None
    await runner.cleanup()
    logger.info("health server stopped")

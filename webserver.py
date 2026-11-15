"""
𝐒𝐚𝐤𝐮𝐫𝐀 𝐕𝐚𝐥𝐢𝐝𝐀𝐭𝐨𝐫 𝐗 — Health Web Server
◈ Tʜᴇ ˹Nʏʀᴏ ꭙ Bᴏᴛs !! ♡
"""

from __future__ import annotations

import logging
import os

from aiohttp import web

logger = logging.getLogger("sakura.web")


async def health_handler(request: web.Request) -> web.Response:
    return web.json_response({
        "status": "ok",
        "bot": "𝐒𝐚𝐤𝐮𝐫𝐀 𝐕𝐚𝐥𝐢𝐝𝐀𝐭𝐨𝐫 𝐗",
        "brand": "◈ Tʜᴇ ˹Nʏʀᴏ ꭙ Bᴏᴛs !! ♡",
    })


async def start() -> None:
    app = web.Application()
    app.router.add_get("/", health_handler)
    app.router.add_get("/health", health_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("health server listening on port %s", port)

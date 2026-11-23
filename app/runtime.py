from __future__ import annotations

import asyncio
import logging

from redis.asyncio import Redis
from sqlalchemy import text

from app.core.config import Settings

logger = logging.getLogger(__name__)


async def ping_database(engine) -> bool:
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return True
    except Exception:
        logger.warning("database health check failed")
        return False


async def ping_redis(redis: Redis) -> bool:
    try:
        return bool(await redis.ping())
    except Exception:
        logger.warning("redis health check failed")
        return False


async def serve_health(port: int, check) -> asyncio.base_events.Server:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            raw = await asyncio.wait_for(reader.read(1024), timeout=2)
            ok = await check() if raw.startswith(b"GET") or raw == b"" else await check()
        except Exception:
            ok = False
        body = b"ok" if ok else b"fail"
        status = b"200 OK" if ok else b"503 Service Unavailable"
        writer.write(
            b"HTTP/1.1 "
            + status
            + b"\r\nContent-Length: "
            + str(len(body)).encode()
            + b"\r\nConnection: close\r\n\r\n"
            + body
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    return await asyncio.start_server(handle, "0.0.0.0", port)


def validate_token(settings: Settings) -> None:
    raw = settings.token()
    if ":" not in raw or len(raw) < 20:
        raise SystemExit("BOT_TOKEN is missing or malformed")
    if len(settings.secret()) < 16:
        raise SystemExit("CALLBACK_SECRET must be at least 16 characters")

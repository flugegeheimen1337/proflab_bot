from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from redis.asyncio import Redis

from app.bot.factory import create_dispatcher, sync_commands, warn_if_intercept
from app.context import AppContext
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.db.session import create_db_engine, create_session_factory
from app.integrations.telegram.aiogram_gateway import AiogramGateway
from app.runtime import ping_database, ping_redis, serve_health, validate_token
from app.services.queue import ArqJobQueue

logger = logging.getLogger(__name__)


async def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    validate_token(settings)
    engine = create_db_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    bot = Bot(settings.token(), default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    me = await bot.get_me()
    queue = ArqJobQueue(settings.redis_url)
    await queue.connect()
    app = AppContext(
        settings=settings,
        session_factory=session_factory,
        redis=redis,
        queue=queue,
        telegram=AiogramGateway(bot, redis),
        bot_username=me.username or settings.bot_username,
        bot_id=me.id,
    )
    dispatcher = create_dispatcher(app)

    async def healthy() -> bool:
        return await ping_database(engine) and await ping_redis(redis)

    health = await serve_health(settings.health_port, healthy)
    logger.info("bot online username=%s", app.bot_username)
    try:
        await sync_commands(bot, settings)
        await warn_if_intercept(bot, settings)
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        health.close()
        await health.wait_closed()
        await bot.session.close()
        await queue.close()
        await redis.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

from __future__ import annotations

import logging
import uuid

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from arq import Retry
from redis.asyncio import Redis

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.db.repositories.store import Store
from app.db.session import create_db_engine, create_session_factory
from app.integrations.telegram.aiogram_gateway import AiogramGateway
from app.runtime import validate_token
from app.services.broadcasts import BroadcastService
from app.services.delivery import DeliveryService
from app.services.errors import TemporaryTelegramError
from app.services.limiter import GroupSendLimiter

logger = logging.getLogger(__name__)


class WorkerQueue:
    def __init__(self, pool) -> None:
        self.pool = pool

    async def enqueue_delivery(self, message_id: uuid.UUID) -> None:
        await self.pool.enqueue_job(
            "deliver_message",
            str(message_id),
            _job_id=f"recover-{message_id}-{uuid.uuid4().hex[:8]}",
        )

    async def enqueue_broadcast(self, broadcast_id: uuid.UUID) -> None:
        await self.pool.enqueue_job(
            "run_broadcast",
            str(broadcast_id),
            _job_id=f"broadcast-{broadcast_id}-{uuid.uuid4().hex[:8]}",
        )


async def startup(ctx) -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    validate_token(settings)
    engine = create_db_engine(settings.database_url)
    app_redis = Redis.from_url(settings.redis_url, decode_responses=True)
    bot = Bot(settings.token(), default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    me = await bot.get_me()
    ctx["settings"] = settings
    ctx["engine"] = engine
    ctx["session_factory"] = create_session_factory(engine)
    ctx["app_redis"] = app_redis
    ctx["bot"] = bot
    ctx["bot_username"] = me.username or settings.bot_username
    ctx["bot_id"] = me.id
    ctx["gateway"] = AiogramGateway(bot, app_redis)
    ctx["limiter"] = GroupSendLimiter(app_redis, settings.group_send_interval_ms, settings.group_send_per_minute)


async def shutdown(ctx) -> None:
    bot = ctx.get("bot")
    if bot is not None:
        await bot.session.close()
    redis = ctx.get("app_redis")
    if redis is not None:
        await redis.aclose()
    engine = ctx.get("engine")
    if engine is not None:
        await engine.dispose()


async def deliver_message(ctx, message_id: str) -> None:
    service = DeliveryService(
        ctx["session_factory"],
        settings=ctx["settings"],
        telegram=ctx["gateway"],
        redis=ctx["app_redis"],
        limiter=ctx["limiter"],
        bot_username=ctx["bot_username"],
    )
    try:
        await service.deliver(uuid.UUID(message_id))
    except TemporaryTelegramError as exc:
        raise Retry(defer=max(exc.retry_after, 1)) from exc


async def run_broadcast(ctx, broadcast_id: str) -> None:
    session = ctx["session_factory"]()
    service = BroadcastService(
        session,
        settings=ctx["settings"],
        telegram=ctx["gateway"],
        queue=WorkerQueue(ctx["redis"]),
        bot_username=ctx["bot_username"],
    )
    try:
        try:
            await service.execute(uuid.UUID(broadcast_id))
        except TemporaryTelegramError as exc:
            raise Retry(defer=max(exc.retry_after, 1)) from exc
    finally:
        await session.close()


async def run_scheduled_broadcasts(ctx) -> None:
    session = ctx["session_factory"]()
    try:
        service = BroadcastService(
            session,
            settings=ctx["settings"],
            telegram=ctx["gateway"],
            queue=WorkerQueue(ctx["redis"]),
            bot_username=ctx["bot_username"],
        )
        count = await service.enqueue_due()
        await session.commit()
        logger.info("scheduled broadcasts queued=%s", count)
    finally:
        await session.close()


async def recover_pending(ctx) -> None:
    session = ctx["session_factory"]()
    try:
        store = Store(session)
        queue = WorkerQueue(ctx["redis"])
        for message in await store.recoverable_messages():
            await queue.enqueue_delivery(message.id)
        await store.purge_callbacks()
        await session.commit()
    finally:
        await session.close()

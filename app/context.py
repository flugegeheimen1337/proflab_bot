from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.integrations.telegram.gateway import TelegramGateway
from app.services.limiter import RedisLike
from app.services.queue import JobQueue


@dataclass
class AppContext:
    settings: Settings
    session_factory: async_sessionmaker[AsyncSession]
    redis: RedisLike
    queue: JobQueue
    telegram: TelegramGateway
    bot_username: str
    bot_id: int

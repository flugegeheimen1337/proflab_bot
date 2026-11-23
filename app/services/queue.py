from __future__ import annotations

import logging
import uuid
from typing import Protocol

from arq import create_pool
from arq.connections import RedisSettings

from app.services.errors import QueueUnavailable

logger = logging.getLogger(__name__)


class JobQueue(Protocol):
    async def enqueue_delivery(self, message_id: uuid.UUID) -> None: ...

    async def enqueue_broadcast(self, broadcast_id: uuid.UUID) -> None: ...


class ArqJobQueue:
    def __init__(self, redis_url: str) -> None:
        self.redis_url = redis_url
        self.pool = None

    async def connect(self) -> None:
        self.pool = await create_pool(RedisSettings.from_dsn(self.redis_url))

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()

    async def enqueue_delivery(self, message_id: uuid.UUID) -> None:
        await self._enqueue("deliver_message", message_id, f"deliver-{message_id}")

    async def enqueue_broadcast(self, broadcast_id: uuid.UUID) -> None:
        await self._enqueue("run_broadcast", broadcast_id, f"broadcast-{broadcast_id}-{uuid.uuid4().hex[:8]}")

    async def _enqueue(self, function: str, entity_id: uuid.UUID, job_id: str) -> None:
        if self.pool is None:
            raise QueueUnavailable("queue is not connected")
        try:
            await self.pool.enqueue_job(function, str(entity_id), _job_id=job_id)
        except Exception as exc:
            logger.warning("enqueue failed for %s", function)
            raise QueueUnavailable("enqueue failed") from exc

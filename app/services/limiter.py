from __future__ import annotations

from typing import Protocol


class RedisLike(Protocol):
    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str, ex: int | None = None, px: int | None = None, nx: bool = False): ...

    async def incr(self, key: str) -> int: ...

    async def decr(self, key: str) -> int: ...

    async def expire(self, key: str, seconds: int) -> bool: ...

    async def delete(self, key: str) -> None: ...

    async def ping(self) -> bool: ...


class GroupSendLimiter:
    def __init__(self, redis: RedisLike, interval_ms: int, per_minute: int) -> None:
        self.redis = redis
        self.interval_ms = interval_ms
        self.per_minute = per_minute

    async def acquire(self, telegram_chat_id: int) -> bool:
        slot = f"rl:slot:{telegram_chat_id}"
        reserved = await self.redis.set(slot, "1", nx=True, px=self.interval_ms)
        if not reserved:
            return False
        minute = f"rl:min:{telegram_chat_id}"
        count = int(await self.redis.incr(minute))
        if count == 1:
            await self.redis.expire(minute, 60)
        if count > self.per_minute:
            return False
        return True


async def depth(redis: RedisLike, key: str) -> int:
    raw = await redis.get(key)
    return int(raw or 0)


async def decr_floor(redis: RedisLike, key: str) -> int:
    value = int(await redis.decr(key))
    if value < 0:
        await redis.set(key, "0")
        return 0
    return value

from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject


class DbSessionMiddleware(BaseMiddleware):
    """Open a session without Session.begin(), so services may commit before enqueue."""

    def __init__(self, session_factory) -> None:
        self.session_factory = session_factory

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data["update_id"] = getattr(event, "update_id", None)
        session = self.session_factory()
        data["session"] = session
        try:
            try:
                result = await handler(event, data)
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            return result
        finally:
            await session.close()

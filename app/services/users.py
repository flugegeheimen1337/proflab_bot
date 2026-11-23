from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import now_utc
from app.db.models import Chat, User
from app.db.repositories.store import Store


class UserService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.store = Store(session)

    async def register(self, telegram_id: int) -> tuple[User, bool, bool]:
        """Return the user, whether the row is new, and whether an erased profile was restored."""
        user = await self.store.user_by_telegram(telegram_id)
        if user is None:
            user = User(telegram_id=telegram_id, last_active_at=now_utc())
            self.session.add(user)
            await self.session.flush()
            return user, True, False
        restored = user.erased_at is not None
        user.erased_at = None
        user.bot_blocked = False
        user.last_active_at = now_utc()
        await self.session.flush()
        return user, False, restored

    async def erase(self, telegram_id: int) -> User | None:
        user = await self.store.user_by_telegram(telegram_id)
        if user is None:
            return None
        user.erased_at = now_utc()
        user.active_chat_id = None
        user.pending_reply_code = None
        await self.session.flush()
        return user

    async def purge(self, user: User) -> None:
        user.telegram_id = None
        user.erased_at = now_utc()
        user.active_chat_id = None
        user.pending_reply_code = None
        user.bot_blocked = True
        await self.session.flush()

    async def set_active_chat(self, user: User, chat: Chat) -> None:
        user.active_chat_id = chat.id
        await self.session.flush()

    async def set_pending_reply(self, user: User, code: str | None) -> None:
        user.pending_reply_code = code
        await self.session.flush()

    async def require(self, telegram_id: int) -> User:
        user = await self.store.user_by_telegram(telegram_id)
        if user is None:
            user, _, _ = await self.register(telegram_id)
            return user
        if user.erased_at is not None:
            return user
        user.last_active_at = now_utc()
        await self.session.flush()
        return user

    async def get(self, user_id: uuid.UUID) -> User | None:
        return await self.store.user_by_id(user_id)

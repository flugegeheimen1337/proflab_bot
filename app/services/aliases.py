from __future__ import annotations

import random

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Alias, Chat, User
from app.db.repositories.store import Store
from app.services.errors import AliasExhausted


class AliasService:
    def __init__(self, session: AsyncSession, rng=random.randint) -> None:
        self.session = session
        self.store = Store(session)
        self.rng = rng

    async def ensure(self, user: User, chat: Chat) -> Alias:
        existing = await self.store.alias_for_user(chat.id, user.id)
        if existing is not None:
            return existing
        for low, high, attempts in ((100, 999, 40), (1000, 999999, 40)):
            for _ in range(attempts):
                number = self.rng(low, high)
                if await self.store.number_taken(chat.id, number):
                    continue
                alias = Alias(
                    user_id=user.id,
                    chat_id=chat.id,
                    number=number,
                    label=f"Аноним #{number}",
                )
                try:
                    async with self.session.begin_nested():
                        self.session.add(alias)
                        await self.session.flush()
                except IntegrityError:
                    existing = await self.store.alias_for_user(chat.id, user.id)
                    if existing is not None:
                        return existing
                    continue
                return alias
        raise AliasExhausted()

    async def by_number(self, chat: Chat, number: int) -> Alias | None:
        return await self.store.alias_by_number(chat.id, number)

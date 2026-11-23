from __future__ import annotations

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import as_utc, now_utc
from app.db.models import Chat, ChatBan, User
from app.db.repositories.store import Store
from app.services.errors import NotFound, PermissionDenied


def assert_admin(telegram_id: int, admins: frozenset[int]) -> None:
    if telegram_id not in admins:
        raise PermissionDenied()


class ModerationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.store = Store(session)

    def globally_banned(self, user: User) -> bool:
        if not user.is_banned:
            return False
        if user.banned_until is None:
            return True
        if as_utc(user.banned_until) <= now_utc():
            user.is_banned = False
            user.banned_until = None
            return False
        return True

    async def chat_banned(self, user: User, chat: Chat) -> bool:
        return await self.store.active_chat_ban(user.id, chat.id, now_utc()) is not None

    async def temp_ban(self, user: User, chat: Chat, seconds: int, reason: str) -> None:
        self.session.add(
            ChatBan(
                user_id=user.id,
                chat_id=chat.id,
                is_active=True,
                until=now_utc() + timedelta(seconds=seconds),
                reason=reason[:300],
                created_by=0,
            )
        )
        await self.session.flush()

    async def block_alias(
        self,
        *,
        chat: Chat,
        number: int,
        admin_id: int,
        admins: frozenset[int],
        minutes: int | None,
        reason: str,
    ) -> str:
        assert_admin(admin_id, admins)
        from app.services.aliases import AliasService

        alias = await AliasService(self.session).by_number(chat, number)
        if alias is None:
            raise NotFound()
        until = None if minutes is None else now_utc() + timedelta(minutes=minutes)
        self.session.add(
            ChatBan(
                user_id=alias.user_id,
                chat_id=chat.id,
                is_active=True,
                until=until,
                reason=reason[:300] or None,
                created_by=admin_id,
            )
        )
        self.store.add_audit(
            admin_telegram_id=admin_id,
            action="block",
            alias_label=alias.label,
            chat_id=chat.id,
            detail="permanent" if minutes is None else f"minutes={minutes}",
        )
        await self.session.flush()
        return alias.label

    async def unblock_alias(self, *, chat: Chat, number: int, admin_id: int, admins: frozenset[int]) -> str:
        assert_admin(admin_id, admins)
        from app.services.aliases import AliasService

        alias = await AliasService(self.session).by_number(chat, number)
        if alias is None:
            raise NotFound()
        from sqlalchemy import select

        rows = await self.session.scalars(
            select(ChatBan).where(ChatBan.user_id == alias.user_id, ChatBan.chat_id == chat.id, ChatBan.is_active.is_(True))
        )
        for row in rows:
            row.is_active = False
        self.store.add_audit(
            admin_telegram_id=admin_id,
            action="unblock",
            alias_label=alias.label,
            chat_id=chat.id,
        )
        await self.session.flush()
        return alias.label

    async def global_block(
        self, *, chat: Chat, number: int, admin_id: int, admins: frozenset[int], minutes: int | None, reason: str
    ) -> str:
        assert_admin(admin_id, admins)
        from app.services.aliases import AliasService

        alias = await AliasService(self.session).by_number(chat, number)
        if alias is None:
            raise NotFound()
        user = await self.store.user_by_id(alias.user_id)
        if user is None:
            raise NotFound()
        user.is_banned = True
        user.banned_until = None if minutes is None else now_utc() + timedelta(minutes=minutes)
        user.ban_reason = reason[:300] or None
        self.store.add_audit(
            admin_telegram_id=admin_id,
            action="gblock",
            alias_label=alias.label,
            chat_id=chat.id,
            detail="permanent" if minutes is None else f"minutes={minutes}",
        )
        await self.session.flush()
        return alias.label

    async def global_unblock(self, *, chat: Chat, number: int, admin_id: int, admins: frozenset[int]) -> str:
        assert_admin(admin_id, admins)
        from app.services.aliases import AliasService

        alias = await AliasService(self.session).by_number(chat, number)
        if alias is None:
            raise NotFound()
        user = await self.store.user_by_id(alias.user_id)
        if user is None:
            raise NotFound()
        user.is_banned = False
        user.banned_until = None
        user.ban_reason = None
        self.store.add_audit(admin_telegram_id=admin_id, action="gunblock", alias_label=alias.label, chat_id=chat.id)
        await self.session.flush()
        return alias.label

    def reveal_text(self, user: User, label: str, *, allowed: bool) -> str:
        if not allowed or user.telegram_id is None:
            return f"{label}: Telegram ID скрыт. Включите REVEAL_TELEGRAM_IDS, чтобы показать его один раз."
        return f"{label}: {user.telegram_id}"

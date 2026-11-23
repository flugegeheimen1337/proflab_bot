from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.security import new_public_code
from app.db.models import Chat, ChatSettings
from app.db.repositories.store import Store
from app.services.errors import ContentRejected, NotFound, PermissionDenied
from app.services.moderation import assert_admin

_SPAM_RANGES = {
    "messages_per_minute": (1, 30),
    "max_message_length": (1, 4000),
    "spam_block_seconds": (30, 86_400),
    "max_queue_per_user": (1, 100),
}
_CONTENT_TYPES = {"text", "photo", "video", "document"}


ROOM_TELEGRAM_ID = 0


class ChatService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.store = Store(session)

    async def ensure_room(self) -> Chat:
        chat = await self.store.room()
        if chat is not None:
            return chat
        chat = Chat(
            telegram_chat_id=ROOM_TELEGRAM_ID,
            public_code=new_public_code(),
            title="Комната",
            is_active=True,
            is_room=True,
        )
        self.session.add(chat)
        await self.session.flush()
        self.session.add(
            ChatSettings(
                chat_id=chat.id,
                messages_per_minute=self.settings.messages_per_minute,
                max_message_length=self.settings.max_message_length,
                allowed_types=self.settings.allowed_types,
                spam_block_seconds=self.settings.spam_block_seconds,
                max_queue_per_user=self.settings.max_queue_per_user,
            )
        )
        await self.session.flush()
        return chat

    async def upsert(self, telegram_chat_id: int, title: str | None) -> Chat:
        chat = await self.store.chat_by_telegram(telegram_chat_id)
        if chat is None:
            chat = Chat(telegram_chat_id=telegram_chat_id, public_code=new_public_code(), title=title, is_active=True)
            self.session.add(chat)
            await self.session.flush()
            self.session.add(
                ChatSettings(
                    chat_id=chat.id,
                    messages_per_minute=self.settings.messages_per_minute,
                    max_message_length=self.settings.max_message_length,
                    allowed_types=self.settings.allowed_types,
                    spam_block_seconds=self.settings.spam_block_seconds,
                    max_queue_per_user=self.settings.max_queue_per_user,
                )
            )
            await self.session.flush()
            return chat
        chat.is_active = True
        if title:
            chat.title = title
        await self.session.flush()
        return chat

    async def deactivate(self, telegram_chat_id: int) -> None:
        chat = await self.store.chat_by_telegram(telegram_chat_id)
        if chat is not None:
            chat.is_active = False
            await self.session.flush()

    async def migrate(self, old_id: int, new_id: int) -> None:
        chat = await self.store.chat_by_telegram(old_id)
        if chat is None:
            return
        chat.telegram_chat_id = new_id
        await self.session.flush()

    async def resolve_for_admin(self, active_chat_id, telegram_user_id: int):
        if active_chat_id is not None:
            chat = await self.store.chat_by_id(active_chat_id)
            if chat is not None:
                return chat
        if self.settings.default_group_id is not None:
            chat = await self.store.chat_by_telegram(self.settings.default_group_id)
            if chat is not None:
                return chat
        chats = await self.store.active_chats()
        if len(chats) == 1:
            return chats[0]
        return None

    async def update_spam(self, chat: Chat, key: str, value: str, admin_id: int) -> str:
        assert_admin(admin_id, self.settings.admin_id_set)
        row = await self.store.settings_for(chat.id)
        if row is None:
            raise NotFound()
        if key == "allowed_types":
            types = [part.strip() for part in value.split(",") if part.strip()]
            if not types or any(item not in _CONTENT_TYPES for item in types):
                raise ContentRejected("type")
            row.allowed_types = ",".join(types)
            shown = row.allowed_types
        elif key in _SPAM_RANGES:
            try:
                number = int(value)
            except ValueError as exc:
                raise ContentRejected("range") from exc
            low, high = _SPAM_RANGES[key]
            if not low <= number <= high:
                raise ContentRejected("range")
            setattr(row, key, number)
            shown = str(number)
        else:
            raise NotFound()
        self.store.add_audit(
            admin_telegram_id=admin_id,
            action="spam_set",
            chat_id=chat.id,
            detail=f"{key}={shown}"[:300],
        )
        await self.session.flush()
        return shown

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.security import new_public_code
from app.core.time import now_utc
from app.db.models import Chat, ChatSettings, Message, User
from app.db.repositories.store import Store
from app.integrations.telegram.gateway import TelegramGateway
from app.services.aliases import AliasService
from app.services.errors import (
    ContentRejected,
    GroupUnavailable,
    NotAMember,
    QueueFull,
    QueueUnavailable,
    ReplyTargetMissing,
    UserBanned,
    UserErased,
    RateLimited,
)
from app.services.formatting import clean_body, ensure_length, neutral_filename
from app.services.limiter import RedisLike, depth
from app.services.moderation import ModerationService
from app.services.queue import JobQueue

PUBLISHED = "published"
QUEUED = "queued"
PENDING = "pending"
SENDING = "sending"
FAILED = "failed"
DELETED = "deleted"


@dataclass
class AcceptResult:
    message: Message
    created: bool
    alias_label: str


class MessageService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings,
        telegram: TelegramGateway,
        queue: JobQueue,
        redis: RedisLike,
        bot_id: int,
    ) -> None:
        self.session = session
        self.settings = settings
        self.telegram = telegram
        self.queue = queue
        self.redis = redis
        self.bot_id = bot_id
        self.store = Store(session)
        self.aliases = AliasService(session)
        self.moderation = ModerationService(session)

    async def accept(
        self,
        *,
        telegram_user_id: int,
        chat: Chat,
        content_type: str,
        body: str | None,
        file_id: str | None,
        original_filename: str | None,
        file_size: int | None,
        source: str,
        source_chat_id: int,
        source_message_id: int,
        reply_code: str | None = None,
        reply_to_telegram_message_id: int | None = None,
        update_id: int | None = None,
        telegram_thread_id: int | None = None,
    ) -> AcceptResult:
        if update_id is not None:
            existing = await self.store.message_by_update(update_id)
            if existing is not None:
                alias = await self.store.alias_for_user(existing.chat_id, existing.author_id)
                return AcceptResult(existing, False, alias.label if alias else "")
        existing = await self.store.message_by_source(source_chat_id, telegram_user_id, source_message_id)
        if existing is not None:
            alias = await self.store.alias_for_user(existing.chat_id, existing.author_id)
            return AcceptResult(existing, False, alias.label if alias else "")

        from app.services.users import UserService

        user = await UserService(self.session).require(telegram_user_id)
        if user.erased_at is not None:
            raise UserErased()
        if self.moderation.globally_banned(user):
            raise UserBanned()
        if await self.moderation.chat_banned(user, chat):
            raise UserBanned()

        if not chat.is_room:
            member = await self.telegram.get_member(chat.telegram_chat_id, telegram_user_id)
            if not member.allows(content_type):
                raise NotAMember()
            bot_member = await self.telegram.get_member(chat.telegram_chat_id, self.bot_id)
            if not bot_member.can_send_messages:
                raise GroupUnavailable()

        settings_row = await self._settings(chat)
        await self._antispam(user, chat, settings_row)
        if content_type not in {"text", "photo", "video", "document"} or not settings_row.allows(content_type):
            raise ContentRejected("type")
        body = clean_body(body)
        if content_type == "text" and not body:
            raise ContentRejected("empty")
        ensure_length(content_type, body, settings_row.max_message_length)
        if content_type in {"video", "document"} and file_size and file_size > self.settings.max_media_bytes:
            raise ContentRejected("file_too_large")
        if content_type != "text" and not file_id:
            raise ContentRejected("file")

        queued_now = await depth(self.redis, f"udepth:{user.id}")
        if queued_now >= settings_row.max_queue_per_user:
            raise QueueFull()

        alias = await self.aliases.ensure(user, chat)
        reply_to_id = await self._resolve_reply(chat, user, reply_code, reply_to_telegram_message_id)
        message = Message(
            public_code=new_public_code(),
            chat_id=chat.id,
            author_id=user.id,
            alias_id=alias.id,
            source=source,
            content_type=content_type,
            body=body,
            file_id=file_id,
            neutral_filename=None if content_type == "text" else neutral_filename(original_filename, kind=content_type),
            file_size=file_size,
            source_chat_id=source_chat_id,
            source_user_id=telegram_user_id,
            source_message_id=source_message_id,
            update_id=update_id,
            reply_to_id=reply_to_id,
            telegram_thread_id=telegram_thread_id,
            status=QUEUED,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(message)
                await self.session.flush()
        except IntegrityError:
            existing = await self.store.message_by_source(source_chat_id, telegram_user_id, source_message_id)
            if existing is None:
                raise
            return AcceptResult(existing, False, alias.label)

        user.last_active_at = now_utc()
        user.pending_reply_code = None
        await self.session.commit()
        try:
            await self.queue.enqueue_delivery(message.id)
        except QueueUnavailable:
            raise
        except Exception as exc:
            raise QueueUnavailable("enqueue failed") from exc
        await self.redis.incr(f"udepth:{user.id}")
        await self.redis.incr(f"cdepth:{chat.id}")
        chat_depth = await depth(self.redis, f"cdepth:{chat.id}")
        if chat_depth >= self.settings.queue_alert_depth:
            await self._alert_queue(chat, chat_depth)
        return AcceptResult(message, True, alias.label)

    async def delete_own(self, telegram_user_id: int, code: str) -> str:
        message = await self.store.message_by_code(code)
        if message is None:
            return "missing"
        user = await self.store.user_by_telegram(telegram_user_id)
        if user is None or message.author_id != user.id:
            return "forbidden"
        chat = await self.store.chat_by_id(message.chat_id)
        if chat and chat.is_room:
            for copy in await self.store.copies_for_message(message.id):
                recipient = await self.store.user_by_id(copy.user_id)
                if recipient and recipient.telegram_id:
                    await self.telegram.delete_message(recipient.telegram_id, copy.telegram_message_id)
        elif chat and message.telegram_message_id:
            await self.telegram.delete_message(chat.telegram_chat_id, message.telegram_message_id)
        message.status = DELETED
        message.updated_at = now_utc()
        await self.session.flush()
        return "deleted"

    async def _settings(self, chat: Chat) -> ChatSettings:
        row = await self.store.settings_for(chat.id)
        if row is not None:
            return row
        row = ChatSettings(
            chat_id=chat.id,
            messages_per_minute=self.settings.messages_per_minute,
            max_message_length=self.settings.max_message_length,
            allowed_types=self.settings.allowed_types,
            spam_block_seconds=self.settings.spam_block_seconds,
            max_queue_per_user=self.settings.max_queue_per_user,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def _antispam(self, user: User, chat: Chat, settings_row: ChatSettings) -> None:
        key = f"rpm:{user.id}:{chat.id}"
        count = int(await self.redis.incr(key))
        if count == 1:
            await self.redis.expire(key, 60)
        if count <= settings_row.messages_per_minute:
            return
        strikes_key = f"strikes:{user.id}:{chat.id}"
        strikes = int(await self.redis.incr(strikes_key))
        if strikes == 1:
            await self.redis.expire(strikes_key, 3600)
        if strikes >= 3:
            await self.moderation.temp_ban(user, chat, settings_row.spam_block_seconds, "spam")
        raise RateLimited()

    async def _resolve_reply(
        self,
        chat: Chat,
        user: User,
        reply_code: str | None,
        reply_to_telegram_message_id: int | None,
    ) -> uuid.UUID | None:
        if reply_to_telegram_message_id:
            parent = await self.store.message_by_telegram(chat.id, reply_to_telegram_message_id)
            return parent.id if parent else None
        code = reply_code or user.pending_reply_code
        if not code:
            return None
        parent = await self.store.message_by_code(code)
        if parent is None or parent.chat_id != chat.id:
            raise ReplyTargetMissing()
        return parent.id

    async def _alert_queue(self, chat: Chat, chat_depth: int) -> None:
        marked = await self.redis.set(f"alert:queue:{chat.id}", "1", nx=True, ex=600)
        if not marked:
            return
        title = chat.title or "группа"
        text = f"Очередь «{title}» содержит около {chat_depth} сообщений. Публикация идёт с паузой из-за лимитов Telegram."
        for admin_id in self.settings.admin_id_set:
            try:
                await self.telegram.send_dm(admin_id, text)
            except Exception:
                continue

from __future__ import annotations

import asyncio
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.time import now_utc
from app.db.models import Alias, FailedJob, MessageCopy
from app.db.repositories.store import Store
from app.integrations.telegram.gateway import Outgoing, TelegramGateway
from app.services.buttons import delete_buttons, publication_buttons
from app.services.errors import BotBlocked, TemporaryTelegramError
from app.services.formatting import render_public, safe_error
from app.services.limiter import GroupSendLimiter, decr_floor

logger = logging.getLogger(__name__)
FINISHED = {"published", "deleted", "failed"}


class DeliveryService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        settings: Settings,
        telegram: TelegramGateway,
        redis,
        limiter: GroupSendLimiter,
        bot_username: str,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.telegram = telegram
        self.redis = redis
        self.limiter = limiter
        self.bot_username = bot_username

    async def deliver(self, message_id: uuid.UUID) -> None:
        lock = f"lock:deliver:{message_id}"
        if not await self.redis.set(lock, "1", nx=True, px=60_000):
            return
        finished = False
        author_id: uuid.UUID | None = None
        chat_id: uuid.UUID | None = None
        try:
            prepared = await self._prepare(message_id)
            if prepared is None:
                return
            outgoing, meta = prepared
            author_id = meta["author_id"]
            chat_id = meta["chat_id"]
            if meta.get("is_room"):
                await self._fanout(message_id, outgoing, meta)
                finished = True
                return
            for _ in range(25):
                if await self.limiter.acquire(meta["telegram_chat_id"]):
                    break
                await asyncio.sleep(0.35)
            else:
                raise TemporaryTelegramError(1)

            session = self.session_factory()
            try:
                store = Store(session)
                message = await store.message_by_id(message_id)
                if message is None or message.status in FINISHED:
                    return
                message.status = "sending"
                message.updated_at = now_utc()
                await session.commit()
                try:
                    telegram_message_id = await self.telegram.send(outgoing)
                except TemporaryTelegramError:
                    raise
                except Exception as exc:
                    message.status = "failed"
                    message.error = safe_error(exc)
                    message.updated_at = now_utc()
                    session.add(FailedJob(job_name="deliver_message", payload=str(message_id), error=message.error))
                    await session.commit()
                    finished = True
                    await self._notify(f"Не удалось опубликовать сообщение {message.public_code}: {message.error}")
                    return
                message.telegram_message_id = telegram_message_id
                message.status = "published"
                message.published_at = now_utc()
                message.updated_at = now_utc()
                message.error = None
                await session.commit()
                finished = True
                user = await store.user_by_id(message.author_id)
                if user and user.telegram_id is not None:
                    try:
                        await self.telegram.send_dm(
                            user.telegram_id,
                            f"Опубликовано как {meta['alias_label']}.",
                            tuple(delete_buttons(message.public_code, self.settings.secret())),
                        )
                    except Exception:
                        logger.warning("publish confirmation was not delivered")
            except TemporaryTelegramError:
                raise
            finally:
                await session.close()
        finally:
            if finished and author_id is not None and chat_id is not None:
                await decr_floor(self.redis, f"udepth:{author_id}")
                await decr_floor(self.redis, f"cdepth:{chat_id}")
            await self.redis.delete(lock)

    async def _prepare(self, message_id: uuid.UUID):
        session = self.session_factory()
        try:
            store = Store(session)
            message = await store.message_by_id(message_id)
            if message is None or message.status in FINISHED:
                return None
            chat = await store.chat_by_id(message.chat_id)
            alias = await session.get(Alias, message.alias_id)
            if chat is None or alias is None:
                message.status = "failed"
                message.error = "missing chat or alias"
                message.updated_at = now_utc()
                await session.commit()
                return None
            reply_to = None
            if message.reply_to_id:
                parent = await store.message_by_id(message.reply_to_id)
                if parent and parent.telegram_message_id and parent.status == "published":
                    reply_to = parent.telegram_message_id
            outgoing = Outgoing(
                chat_id=chat.telegram_chat_id,
                kind=message.content_type,
                text=render_public(alias.label, message.body),
                file_id=message.file_id,
                filename=message.neutral_filename,
                reply_to_message_id=reply_to,
                message_thread_id=message.telegram_thread_id,
                buttons=tuple(
                    publication_buttons(message.public_code, self.bot_username, self.settings.secret())
                ),
            )
            meta = {
                "author_id": message.author_id,
                "chat_id": message.chat_id,
                "telegram_chat_id": chat.telegram_chat_id,
                "alias_label": alias.label,
                "is_room": chat.is_room,
            }
            return outgoing, meta
        finally:
            await session.close()

    async def _fanout(self, message_id: uuid.UUID, outgoing: Outgoing, meta: dict) -> None:
        from app.services.moderation import ModerationService

        session = self.session_factory()
        sent = 0
        try:
            store = Store(session)
            message = await store.message_by_id(message_id)
            chat = await store.chat_by_id(meta["chat_id"]) if message is not None else None
            if message is None or chat is None or message.status in FINISHED:
                return
            moderation = ModerationService(session)
            for user in await store.deliverable_users():
                if user.id == message.author_id or user.telegram_id is None:
                    continue
                if await moderation.chat_banned(user, chat):
                    continue
                if await store.copy_for_recipient(message.id, user.id):
                    sent += 1
                    continue
                reply_to = None
                if message.reply_to_id:
                    parent_copy = await store.copy_for_recipient(message.reply_to_id, user.id)
                    parent_message = await store.message_by_id(message.reply_to_id)
                    if parent_copy is not None:
                        reply_to = parent_copy.telegram_message_id
                    elif parent_message is not None and parent_message.source_user_id == user.telegram_id:
                        reply_to = parent_message.source_message_id
                personal = Outgoing(
                    chat_id=user.telegram_id,
                    kind=outgoing.kind,
                    text=outgoing.text,
                    file_id=outgoing.file_id,
                    filename=outgoing.filename,
                    reply_to_message_id=reply_to,
                    buttons=outgoing.buttons,
                )
                try:
                    telegram_message_id = await self.telegram.send(personal)
                except BotBlocked:
                    user.bot_blocked = True
                    await session.commit()
                    continue
                except TemporaryTelegramError:
                    await session.commit()
                    raise
                session.add(
                    MessageCopy(message_id=message.id, user_id=user.id, telegram_message_id=telegram_message_id)
                )
                await session.commit()
                sent += 1
            message.status = "published"
            message.published_at = now_utc()
            message.updated_at = now_utc()
            await session.commit()
            author = await store.user_by_id(message.author_id)
            if author and author.telegram_id:
                if sent:
                    text = f"Сообщение разослано как {meta['alias_label']}. Получателей: {sent}."
                else:
                    text = f"Вы {meta['alias_label']}. Пока получателей нет: остальные должны нажать /start."
                try:
                    await self.telegram.send_dm(author.telegram_id, text)
                except Exception:
                    logger.warning("room receipt was not delivered")
        finally:
            await session.close()

    async def _notify(self, text: str) -> None:
        for admin_id in self.settings.admin_id_set:
            try:
                await self.telegram.send_dm(admin_id, text)
            except Exception:
                logger.warning("admin error notify failed")

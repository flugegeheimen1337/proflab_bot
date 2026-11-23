from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import as_utc, now_utc
from app.db.models import (
    AdminAction,
    Alias,
    Broadcast,
    BroadcastDelivery,
    BroadcastRun,
    CallbackReceipt,
    Chat,
    ChatBan,
    ChatSettings,
    FailedJob,
    Message,
    MessageCopy,
    Report,
    User,
)


class Store:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def user_by_telegram(self, telegram_id: int) -> User | None:
        return await self.session.scalar(select(User).where(User.telegram_id == telegram_id))

    async def user_by_id(self, user_id: uuid.UUID) -> User | None:
        return await self.session.get(User, user_id)

    async def chat_by_telegram(self, telegram_chat_id: int) -> Chat | None:
        return await self.session.scalar(select(Chat).where(Chat.telegram_chat_id == telegram_chat_id))

    async def chat_by_id(self, chat_id: uuid.UUID) -> Chat | None:
        return await self.session.get(Chat, chat_id)

    async def chat_by_code(self, code: str) -> Chat | None:
        return await self.session.scalar(select(Chat).where(Chat.public_code == code))

    async def active_chats(self) -> list[Chat]:
        rows = await self.session.scalars(
            select(Chat).where(Chat.is_active.is_(True), Chat.is_room.is_(False)).order_by(Chat.created_at)
        )
        return list(rows)

    async def room(self) -> Chat | None:
        return await self.session.scalar(select(Chat).where(Chat.is_room.is_(True)))

    async def copy_for_recipient(self, message_id: uuid.UUID, user_id: uuid.UUID) -> MessageCopy | None:
        return await self.session.scalar(
            select(MessageCopy).where(MessageCopy.message_id == message_id, MessageCopy.user_id == user_id)
        )

    async def copy_by_telegram(self, user_id: uuid.UUID, telegram_message_id: int) -> MessageCopy | None:
        return await self.session.scalar(
            select(MessageCopy).where(
                MessageCopy.user_id == user_id,
                MessageCopy.telegram_message_id == telegram_message_id,
            )
        )

    async def copies_for_message(self, message_id: uuid.UUID) -> list[MessageCopy]:
        rows = await self.session.scalars(select(MessageCopy).where(MessageCopy.message_id == message_id))
        return list(rows)

    async def alias_for_user(self, chat_id: uuid.UUID, user_id: uuid.UUID) -> Alias | None:
        return await self.session.scalar(select(Alias).where(Alias.chat_id == chat_id, Alias.user_id == user_id))

    async def alias_by_number(self, chat_id: uuid.UUID, number: int) -> Alias | None:
        return await self.session.scalar(select(Alias).where(Alias.chat_id == chat_id, Alias.number == number))

    async def number_taken(self, chat_id: uuid.UUID, number: int) -> bool:
        found = await self.session.scalar(select(Alias.id).where(Alias.chat_id == chat_id, Alias.number == number))
        return found is not None

    async def settings_for(self, chat_id: uuid.UUID) -> ChatSettings | None:
        return await self.session.get(ChatSettings, chat_id)

    async def message_by_source(self, source_chat_id: int, source_user_id: int, source_message_id: int) -> Message | None:
        return await self.session.scalar(
            select(Message).where(
                Message.source_chat_id == source_chat_id,
                Message.source_user_id == source_user_id,
                Message.source_message_id == source_message_id,
            )
        )

    async def message_by_update(self, update_id: int) -> Message | None:
        return await self.session.scalar(select(Message).where(Message.update_id == update_id))

    async def message_by_code(self, code: str) -> Message | None:
        return await self.session.scalar(select(Message).where(Message.public_code == code))

    async def message_by_id(self, message_id: uuid.UUID) -> Message | None:
        return await self.session.get(Message, message_id)

    async def message_by_telegram(self, chat_id: uuid.UUID, telegram_message_id: int) -> Message | None:
        return await self.session.scalar(
            select(Message).where(Message.chat_id == chat_id, Message.telegram_message_id == telegram_message_id)
        )

    async def active_chat_ban(self, user_id: uuid.UUID, chat_id: uuid.UUID, now: datetime) -> ChatBan | None:
        rows = await self.session.scalars(
            select(ChatBan).where(ChatBan.user_id == user_id, ChatBan.chat_id == chat_id, ChatBan.is_active.is_(True))
        )
        for row in rows:
            if row.until is None or as_utc(row.until) > now:
                return row
        return None

    async def remember_callback(self, callback_id: str) -> bool:
        from sqlalchemy.exc import IntegrityError

        try:
            async with self.session.begin_nested():
                self.session.add(CallbackReceipt(id=callback_id))
                await self.session.flush()
            return True
        except IntegrityError:
            return False

    async def purge_callbacks(self, older_than_days: int = 7) -> None:
        cutoff = now_utc() - timedelta(days=older_than_days)
        rows = await self.session.scalars(select(CallbackReceipt).where(CallbackReceipt.created_at < cutoff))
        for row in rows:
            await self.session.delete(row)

    async def report_for(self, message_id: uuid.UUID, reporter_id: uuid.UUID) -> Report | None:
        return await self.session.scalar(
            select(Report).where(Report.message_id == message_id, Report.reporter_id == reporter_id)
        )

    async def open_reports(self, limit: int = 10) -> list[Report]:
        rows = await self.session.scalars(
            select(Report).where(Report.status == "open").order_by(Report.created_at.desc()).limit(limit)
        )
        return list(rows)

    async def deliverable_users(self) -> list[User]:
        rows = await self.session.scalars(
            select(User).where(
                User.telegram_id.is_not(None),
                User.erased_at.is_(None),
                User.is_banned.is_(False),
                User.bot_blocked.is_(False),
            )
        )
        return list(rows)

    async def delivery_exists(self, run_id: uuid.UUID, target_key: str) -> bool:
        found = await self.session.scalar(
            select(BroadcastDelivery.id).where(
                BroadcastDelivery.run_id == run_id,
                BroadcastDelivery.target_key == target_key,
            )
        )
        return found is not None

    async def due_broadcasts(self) -> list[Broadcast]:
        rows = await self.session.scalars(
            select(Broadcast).where(Broadcast.status.in_(("scheduled", "active"))).order_by(Broadcast.created_at)
        )
        return list(rows)

    async def broadcast_by_prefix(self, prefix: str) -> list[Broadcast]:
        rows = await self.session.scalars(select(Broadcast).order_by(Broadcast.created_at.desc()).limit(50))
        return [row for row in rows if str(row.id).startswith(prefix)]

    async def recent_failed(self, limit: int = 10) -> list[FailedJob]:
        rows = await self.session.scalars(select(FailedJob).order_by(FailedJob.created_at.desc()).limit(limit))
        return list(rows)

    async def recoverable_messages(self, older_than_seconds: int = 20) -> list[Message]:
        cutoff = now_utc() - timedelta(seconds=older_than_seconds)
        rows = await self.session.scalars(
            select(Message).where(
                Message.status.in_(("pending", "queued", "sending")),
                Message.updated_at < cutoff,
            )
        )
        return list(rows)

    async def count_users(self, *, erased: bool = False) -> int:
        stmt = select(func.count()).select_from(User)
        if not erased:
            stmt = stmt.where(User.erased_at.is_(None), User.telegram_id.is_not(None))
        return int(await self.session.scalar(stmt) or 0)

    async def count_messages(
        self,
        chat_id: uuid.UUID | None,
        start: datetime | None,
        end: datetime | None,
        *,
        replies: bool = False,
        source: str | None = None,
    ) -> int:
        stmt = select(func.count()).select_from(Message).where(Message.status != "failed")
        if chat_id is not None:
            stmt = stmt.where(Message.chat_id == chat_id)
        if start is not None:
            stmt = stmt.where(Message.created_at >= start)
        if end is not None:
            stmt = stmt.where(Message.created_at < end)
        if replies:
            stmt = stmt.where(Message.reply_to_id.is_not(None))
        if source is not None:
            stmt = stmt.where(Message.source == source)
        return int(await self.session.scalar(stmt) or 0)

    async def count_active_authors(
        self, chat_id: uuid.UUID | None, start: datetime | None, end: datetime | None
    ) -> int:
        stmt = select(func.count(func.distinct(Message.author_id))).where(Message.status != "failed")
        if chat_id is not None:
            stmt = stmt.where(Message.chat_id == chat_id)
        if start is not None:
            stmt = stmt.where(Message.created_at >= start)
        if end is not None:
            stmt = stmt.where(Message.created_at < end)
        return int(await self.session.scalar(stmt) or 0)

    async def count_aliases(self, chat_id: uuid.UUID | None, start: datetime | None, end: datetime | None) -> int:
        stmt = select(func.count()).select_from(Alias)
        if chat_id is not None:
            stmt = stmt.where(Alias.chat_id == chat_id)
        if start is not None:
            stmt = stmt.where(Alias.created_at >= start)
        if end is not None:
            stmt = stmt.where(Alias.created_at < end)
        return int(await self.session.scalar(stmt) or 0)

    async def count_reports(self, chat_id: uuid.UUID | None, start: datetime | None, end: datetime | None) -> int:
        stmt = select(func.count()).select_from(Report)
        if chat_id is not None:
            stmt = stmt.join(Message, Message.id == Report.message_id).where(Message.chat_id == chat_id)
        if start is not None:
            stmt = stmt.where(Report.created_at >= start)
        if end is not None:
            stmt = stmt.where(Report.created_at < end)
        return int(await self.session.scalar(stmt) or 0)

    async def count_bans(self, chat_id: uuid.UUID | None, start: datetime | None, end: datetime | None) -> int:
        stmt = select(func.count()).select_from(ChatBan)
        if chat_id is not None:
            stmt = stmt.where(ChatBan.chat_id == chat_id)
        if start is not None:
            stmt = stmt.where(ChatBan.created_at >= start)
        if end is not None:
            stmt = stmt.where(ChatBan.created_at < end)
        return int(await self.session.scalar(stmt) or 0)

    async def broadcast_totals(self, start: datetime | None, end: datetime | None) -> tuple[int, int, int]:
        stmt = select(
            func.coalesce(func.sum(BroadcastRun.sent), 0),
            func.coalesce(func.sum(BroadcastRun.failed), 0),
            func.coalesce(func.sum(BroadcastRun.blocked), 0),
        )
        if start is not None:
            stmt = stmt.where(BroadcastRun.started_at >= start)
        if end is not None:
            stmt = stmt.where(BroadcastRun.started_at < end)
        sent, failed, blocked = (await self.session.execute(stmt)).one()
        return int(sent or 0), int(failed or 0), int(blocked or 0)

    def add_audit(
        self,
        *,
        admin_telegram_id: int,
        action: str,
        alias_label: str | None = None,
        chat_id: uuid.UUID | None = None,
        detail: str | None = None,
    ) -> None:
        self.session.add(
            AdminAction(
                admin_telegram_id=admin_telegram_id,
                action=action,
                alias_label=alias_label,
                chat_id=chat_id,
                detail=detail,
            )
        )

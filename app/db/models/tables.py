from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import now_utc
from app.db.base import Base


def _ts() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


class Chat(Base):
    __tablename__ = "chats"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    public_code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    title: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_room: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = _ts()


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    banned_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ban_reason: Mapped[str | None] = mapped_column(String(300))
    bot_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    erased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active_chat_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("chats.id"))
    pending_reply_code: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = _ts()
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChatSettings(Base):
    __tablename__ = "chat_settings"

    chat_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("chats.id"), primary_key=True)
    messages_per_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    max_message_length: Mapped[int] = mapped_column(Integer, nullable=False)
    allowed_types: Mapped[str] = mapped_column(String(128), nullable=False)
    spam_block_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    max_queue_per_user: Mapped[int] = mapped_column(Integer, nullable=False)

    def allows(self, content_type: str) -> bool:
        allowed = {part.strip() for part in self.allowed_types.split(",") if part.strip()}
        return content_type in allowed


class Alias(Base):
    __tablename__ = "aliases"
    __table_args__ = (
        UniqueConstraint("chat_id", "user_id", name="uq_alias_user_chat"),
        UniqueConstraint("chat_id", "number", name="uq_alias_chat_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False)
    chat_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("chats.id"), nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = _ts()


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("source_chat_id", "source_user_id", "source_message_id", name="uq_message_source"),
        Index("ix_messages_chat_created", "chat_id", "created_at"),
        Index("ix_messages_status", "status"),
        Index("ix_messages_author", "author_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    public_code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    chat_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("chats.id"), nullable=False)
    author_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False)
    alias_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("aliases.id"), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    content_type: Mapped[str] = mapped_column(String(32), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    file_id: Mapped[str | None] = mapped_column(Text)
    neutral_filename: Mapped[str | None] = mapped_column(String(32))
    file_size: Mapped[int | None] = mapped_column(Integer)
    source_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    update_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    reply_to_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("messages.id"))
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    telegram_thread_id: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MessageCopy(Base):
    """One recipient's private copy of a room message. Telegram id here is the copy, not the author."""

    __tablename__ = "message_copies"
    __table_args__ = (
        UniqueConstraint("message_id", "user_id", name="uq_copy_recipient"),
        UniqueConstraint("user_id", "telegram_message_id", name="uq_copy_telegram"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("messages.id"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False)
    telegram_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = _ts()


class Report(Base):
    __tablename__ = "reports"
    __table_args__ = (UniqueConstraint("message_id", "reporter_id", name="uq_report_once"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("messages.id"), nullable=False)
    reporter_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    created_at: Mapped[datetime] = _ts()
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChatBan(Base):
    __tablename__ = "chat_bans"
    __table_args__ = (Index("ix_chat_bans_user_chat", "user_id", "chat_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    chat_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("chats.id"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(String(300))
    created_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = _ts()


class AdminAction(Base):
    __tablename__ = "admin_actions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    admin_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    alias_label: Mapped[str | None] = mapped_column(String(32))
    chat_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("chats.id"))
    detail: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = _ts()


class Broadcast(Base):
    __tablename__ = "broadcasts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    audience: Mapped[str] = mapped_column(String(16), nullable=False)
    timing: Mapped[str] = mapped_column(String(16), nullable=False)
    chat_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("chats.id"))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(String(32), nullable=False)
    file_id: Mapped[str | None] = mapped_column(Text)
    neutral_filename: Mapped[str | None] = mapped_column(String(32))
    run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recurrence: Mapped[str | None] = mapped_column(String(16))
    weekday: Mapped[int | None] = mapped_column(Integer)
    hour: Mapped[int | None] = mapped_column(Integer)
    minute: Mapped[int | None] = mapped_column(Integer)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class BroadcastRun(Base):
    __tablename__ = "broadcast_runs"
    __table_args__ = (UniqueConstraint("broadcast_id", "slot_key", name="uq_broadcast_slot"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    broadcast_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("broadcasts.id"), nullable=False)
    slot_key: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    blocked: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BroadcastDelivery(Base):
    __tablename__ = "broadcast_deliveries"
    __table_args__ = (UniqueConstraint("run_id", "target_key", name="uq_delivery_target"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("broadcast_runs.id"), nullable=False)
    target_key: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = _ts()


class FailedJob(Base):
    __tablename__ = "failed_jobs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_name: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[str] = mapped_column(String(80), nullable=False)
    error: Mapped[str] = mapped_column(String(300), nullable=False)
    created_at: Mapped[datetime] = _ts()


class CallbackReceipt(Base):
    __tablename__ = "callback_receipts"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    created_at: Mapped[datetime] = _ts()

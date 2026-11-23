"""Initial schema.

Revision ID: 20261123_0001
Revises:
Create Date: 2026-11-23
"""

from alembic import op
import sqlalchemy as sa

revision = "20261123_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chats",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("public_code", sa.String(32), nullable=False),
        sa.Column("title", sa.String(255)),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("telegram_chat_id"),
        sa.UniqueConstraint("public_code"),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("telegram_id", sa.BigInteger()),
        sa.Column("is_banned", sa.Boolean(), nullable=False),
        sa.Column("banned_until", sa.DateTime(timezone=True)),
        sa.Column("ban_reason", sa.String(300)),
        sa.Column("bot_blocked", sa.Boolean(), nullable=False),
        sa.Column("erased_at", sa.DateTime(timezone=True)),
        sa.Column("active_chat_id", sa.Uuid(), sa.ForeignKey("chats.id")),
        sa.Column("pending_reply_code", sa.String(32)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_active_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("telegram_id"),
    )
    op.create_table(
        "chat_settings",
        sa.Column("chat_id", sa.Uuid(), sa.ForeignKey("chats.id"), primary_key=True),
        sa.Column("messages_per_minute", sa.Integer(), nullable=False),
        sa.Column("max_message_length", sa.Integer(), nullable=False),
        sa.Column("allowed_types", sa.String(128), nullable=False),
        sa.Column("spam_block_seconds", sa.Integer(), nullable=False),
        sa.Column("max_queue_per_user", sa.Integer(), nullable=False),
    )
    op.create_table(
        "aliases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("chat_id", sa.Uuid(), sa.ForeignKey("chats.id"), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("chat_id", "user_id", name="uq_alias_user_chat"),
        sa.UniqueConstraint("chat_id", "number", name="uq_alias_chat_number"),
    )
    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("public_code", sa.String(32), nullable=False),
        sa.Column("chat_id", sa.Uuid(), sa.ForeignKey("chats.id"), nullable=False),
        sa.Column("author_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("alias_id", sa.Uuid(), sa.ForeignKey("aliases.id"), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("content_type", sa.String(32), nullable=False),
        sa.Column("body", sa.Text()),
        sa.Column("file_id", sa.Text()),
        sa.Column("neutral_filename", sa.String(32)),
        sa.Column("file_size", sa.Integer()),
        sa.Column("source_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("source_user_id", sa.BigInteger(), nullable=False),
        sa.Column("source_message_id", sa.BigInteger(), nullable=False),
        sa.Column("update_id", sa.BigInteger()),
        sa.Column("reply_to_id", sa.Uuid(), sa.ForeignKey("messages.id")),
        sa.Column("telegram_message_id", sa.BigInteger()),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error", sa.String(300)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("public_code"),
        sa.UniqueConstraint("update_id"),
        sa.UniqueConstraint("source_chat_id", "source_user_id", "source_message_id", name="uq_message_source"),
    )
    op.create_index("ix_messages_chat_created", "messages", ["chat_id", "created_at"])
    op.create_index("ix_messages_status", "messages", ["status"])
    op.create_index("ix_messages_author", "messages", ["author_id"])
    op.create_table(
        "reports",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("message_id", sa.Uuid(), sa.ForeignKey("messages.id"), nullable=False),
        sa.Column("reporter_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("message_id", "reporter_id", name="uq_report_once"),
    )
    op.create_table(
        "chat_bans",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("chat_id", sa.Uuid(), sa.ForeignKey("chats.id"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("until", sa.DateTime(timezone=True)),
        sa.Column("reason", sa.String(300)),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_chat_bans_user_chat", "chat_bans", ["user_id", "chat_id"])
    op.create_table(
        "admin_actions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("admin_telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("alias_label", sa.String(32)),
        sa.Column("chat_id", sa.Uuid(), sa.ForeignKey("chats.id")),
        sa.Column("detail", sa.String(300)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "broadcasts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("audience", sa.String(16), nullable=False),
        sa.Column("timing", sa.String(16), nullable=False),
        sa.Column("chat_id", sa.Uuid(), sa.ForeignKey("chats.id")),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("content_type", sa.String(32), nullable=False),
        sa.Column("file_id", sa.Text()),
        sa.Column("neutral_filename", sa.String(32)),
        sa.Column("run_at", sa.DateTime(timezone=True)),
        sa.Column("recurrence", sa.String(16)),
        sa.Column("weekday", sa.Integer()),
        sa.Column("hour", sa.Integer()),
        sa.Column("minute", sa.Integer()),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "broadcast_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("broadcast_id", sa.Uuid(), sa.ForeignKey("broadcasts.id"), nullable=False),
        sa.Column("slot_key", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("sent", sa.Integer(), nullable=False),
        sa.Column("failed", sa.Integer(), nullable=False),
        sa.Column("blocked", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("broadcast_id", "slot_key", name="uq_broadcast_slot"),
    )
    op.create_table(
        "broadcast_deliveries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("broadcast_runs.id"), nullable=False),
        sa.Column("target_key", sa.String(80), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error", sa.String(300)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "target_key", name="uq_delivery_target"),
    )
    op.create_table(
        "failed_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("job_name", sa.String(64), nullable=False),
        sa.Column("payload", sa.String(80), nullable=False),
        sa.Column("error", sa.String(300), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "callback_receipts",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    for name in (
        "callback_receipts",
        "failed_jobs",
        "broadcast_deliveries",
        "broadcast_runs",
        "broadcasts",
        "admin_actions",
        "chat_bans",
        "reports",
        "messages",
        "aliases",
        "chat_settings",
        "users",
        "chats",
    ):
        op.drop_table(name)

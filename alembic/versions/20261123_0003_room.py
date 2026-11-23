"""Direct room copies instead of a shared group message.

Revision ID: 20261123_0003
Revises: 20261123_0002
Create Date: 2026-11-23
"""

from alembic import op
import sqlalchemy as sa

revision = "20261123_0003"
down_revision = "20261123_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chats", sa.Column("is_room", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        "message_copies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("message_id", sa.Uuid(), sa.ForeignKey("messages.id"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("message_id", "user_id", name="uq_copy_recipient"),
        sa.UniqueConstraint("user_id", "telegram_message_id", name="uq_copy_telegram"),
    )


def downgrade() -> None:
    op.drop_table("message_copies")
    op.drop_column("chats", "is_room")

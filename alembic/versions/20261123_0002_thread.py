"""Store the forum topic of an anonymous message.

Revision ID: 20261123_0002
Revises: 20261123_0001
Create Date: 2026-11-23
"""

from alembic import op
import sqlalchemy as sa

revision = "20261123_0002"
down_revision = "20261123_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("telegram_thread_id", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "telegram_thread_id")

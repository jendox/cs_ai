"""add ticket comment attachments

Revision ID: f3d2b1a7c9e4
Revises: c8f14a92d6b1
Create Date: 2026-04-16 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from src import db

# revision identifiers, used by Alembic.
revision: str = "f3d2b1a7c9e4"
down_revision: str | Sequence[str] | None = "c8f14a92d6b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "ticket_comment_attachments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ticket_id", sa.BigInteger(), nullable=False),
        sa.Column("comment_id", sa.String(length=64), nullable=False),
        sa.Column("attachment_id", sa.BigInteger(), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=True),
        sa.Column("size", sa.BigInteger(), nullable=True),
        sa.Column("content_url", sa.Text(), nullable=True),
        sa.Column("mapped_content_url", sa.Text(), nullable=True),
        sa.Column("thumbnail_url", sa.Text(), nullable=True),
        sa.Column("comment_created_at", db.models.UTCDateTime(timezone=True), nullable=True),
        sa.Column("created_at", db.models.UTCDateTime(timezone=True), nullable=False),
        sa.Column("updated_at", db.models.UTCDateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["ticket_id"],
            ["tickets.ticket_id"],
            onupdate="CASCADE",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "ticket_id",
            "comment_id",
            "attachment_id",
            name="uq_ticket_comment_attachments_ticket_comment_attachment",
        ),
    )
    op.create_index(
        "idx_ticket_comment_attachments_ticket_comment",
        "ticket_comment_attachments",
        ["ticket_id", "comment_id"],
        unique=False,
    )
    op.create_index(
        "idx_ticket_comment_attachments_ticket_created",
        "ticket_comment_attachments",
        ["ticket_id", "comment_created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_ticket_comment_attachments_ticket_created", table_name="ticket_comment_attachments")
    op.drop_index("idx_ticket_comment_attachments_ticket_comment", table_name="ticket_comment_attachments")
    op.drop_table("ticket_comment_attachments")

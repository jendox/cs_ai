"""add llm playground

Revision ID: 6d52a1d7a7cc
Revises: 85ae46f57bcf
Create Date: 2026-04-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from src import db

# revision identifiers, used by Alembic.
revision: str = "6d52a1d7a7cc"
down_revision: Union[str, Sequence[str], None] = "85ae46f57bcf"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ticket_status_enum = postgresql.ENUM(
    "open",
    "closed",
    name="llm_playground_ticket_status_enum",
    create_type=False,
)
message_role_enum = postgresql.ENUM(
    "user",
    "assistant",
    "system",
    name="llm_playground_message_role_enum",
    create_type=False,
)
run_status_enum = postgresql.ENUM(
    "generated",
    "failed",
    name="llm_playground_run_status_enum",
    create_type=False,
)


def upgrade() -> None:
    """Upgrade schema."""
    ticket_status_enum.create(op.get_bind(), checkfirst=True)
    message_role_enum.create(op.get_bind(), checkfirst=True)
    run_status_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "llm_playground_tickets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("brand_id", sa.BigInteger(), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("status", ticket_status_enum, nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", db.models.UTCDateTime(timezone=True), nullable=False),
        sa.Column("updated_at", db.models.UTCDateTime(timezone=True), nullable=False),
        sa.Column("closed_at", db.models.UTCDateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_llm_playground_tickets_brand_updated",
        "llm_playground_tickets",
        ["brand_id", "updated_at"],
        unique=False,
    )
    op.create_index(
        "idx_llm_playground_tickets_status_updated",
        "llm_playground_tickets",
        ["status", "updated_at"],
        unique=False,
    )

    op.create_table(
        "llm_playground_messages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ticket_id", sa.Integer(), nullable=False),
        sa.Column("role", message_role_enum, nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("prompt_key", sa.String(length=64), nullable=True),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.Column("created_at", db.models.UTCDateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["ticket_id"],
            ["llm_playground_tickets.id"],
            onupdate="CASCADE",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_llm_playground_messages_ticket_created",
        "llm_playground_messages",
        ["ticket_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "llm_playground_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ticket_id", sa.Integer(), nullable=False),
        sa.Column("prompt_key", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("status", run_status_enum, nullable=False),
        sa.Column("input_messages", sa.JSON(), nullable=False),
        sa.Column("output_body", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", db.models.UTCDateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["ticket_id"],
            ["llm_playground_tickets.id"],
            onupdate="CASCADE",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_llm_playground_runs_status_created",
        "llm_playground_runs",
        ["status", "created_at"],
        unique=False,
    )
    op.create_index(
        "idx_llm_playground_runs_ticket_created",
        "llm_playground_runs",
        ["ticket_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_llm_playground_runs_ticket_created", table_name="llm_playground_runs")
    op.drop_index("idx_llm_playground_runs_status_created", table_name="llm_playground_runs")
    op.drop_table("llm_playground_runs")
    op.drop_index("idx_llm_playground_messages_ticket_created", table_name="llm_playground_messages")
    op.drop_table("llm_playground_messages")
    op.drop_index("idx_llm_playground_tickets_status_updated", table_name="llm_playground_tickets")
    op.drop_index("idx_llm_playground_tickets_brand_updated", table_name="llm_playground_tickets")
    op.drop_table("llm_playground_tickets")
    run_status_enum.drop(op.get_bind(), checkfirst=True)
    message_role_enum.drop(op.get_bind(), checkfirst=True)
    ticket_status_enum.drop(op.get_bind(), checkfirst=True)

"""add ticket classification audits

Revision ID: b4a2f9c1d8e0
Revises: 6d52a1d7a7cc
Create Date: 2026-04-16 00:00:00.000000

"""
from datetime import UTC, datetime
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from src import db

# revision identifiers, used by Alembic.
revision: str = "b4a2f9c1d8e0"
down_revision: Union[str, Sequence[str], None] = "6d52a1d7a7cc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    now = datetime.now(UTC)
    op.create_table(
        "ticket_classification_audits",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ticket_id", sa.BigInteger(), nullable=False),
        sa.Column("brand_id", sa.BigInteger(), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("rule", sa.String(length=64), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("llm_category", sa.String(length=64), nullable=True),
        sa.Column("llm_confidence", sa.Float(), nullable=True),
        sa.Column("threshold", sa.Float(), nullable=True),
        sa.Column("created_at", db.models.UTCDateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["ticket_id"],
            ["tickets.ticket_id"],
            onupdate="CASCADE",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_ticket_classification_brand_created",
        "ticket_classification_audits",
        ["brand_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "idx_ticket_classification_decision_created",
        "ticket_classification_audits",
        ["decision", "created_at"],
        unique=False,
    )
    op.create_index(
        "idx_ticket_classification_source_created",
        "ticket_classification_audits",
        ["source", "created_at"],
        unique=False,
    )
    op.create_index(
        "idx_ticket_classification_ticket_created",
        "ticket_classification_audits",
        ["ticket_id", "created_at"],
        unique=False,
    )

    op.bulk_insert(
        sa.table(
            "tickets_filter_rules",
            sa.column("kind", sa.String(length=50)),
            sa.column("value", sa.String(length=128)),
            sa.column("is_regex", sa.Boolean()),
            sa.column("brand_id", sa.BigInteger()),
            sa.column("via_channel", sa.String(length=32)),
            sa.column("is_active", sa.Boolean()),
            sa.column("comment", sa.Text()),
            sa.column("created_by", sa.String(length=64)),
            sa.column("updated_by", sa.String(length=64)),
            sa.column("created_at", db.models.UTCDateTime(timezone=True)),
            sa.column("updated_at", db.models.UTCDateTime(timezone=True)),
        ),
        [
            {
                "kind": "spam_body_pattern",
                "value": r"\bSEO\b.{0,80}\b(backlinks?|guest post|link insertion)\b",
                "is_regex": True,
                "brand_id": None,
                "via_channel": None,
                "is_active": True,
                "comment": "High-confidence body pattern for SEO outreach.",
                "created_by": "migration:b4a2f9c1d8e0",
                "updated_by": "migration:b4a2f9c1d8e0",
                "created_at": now,
                "updated_at": now,
            },
            {
                "kind": "spam_body_pattern",
                "value": r"\b(influencer|collaboration|partnership)\b.{0,80}\b(instagram|tiktok|youtube)\b",
                "is_regex": True,
                "brand_id": None,
                "via_channel": None,
                "is_active": True,
                "comment": "High-confidence body pattern for influencer/collaboration outreach.",
                "created_by": "migration:b4a2f9c1d8e0",
                "updated_by": "migration:b4a2f9c1d8e0",
                "created_at": now,
                "updated_at": now,
            },
            {
                "kind": "spam_body_pattern",
                "value": r"\bAmazon FBA\b.{0,80}\b(inventory|replenishment|stock)\b",
                "is_regex": True,
                "brand_id": None,
                "via_channel": None,
                "is_active": True,
                "comment": "High-confidence body pattern for FBA inventory/system outreach.",
                "created_by": "migration:b4a2f9c1d8e0",
                "updated_by": "migration:b4a2f9c1d8e0",
                "created_at": now,
                "updated_at": now,
            },
        ],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        sa.text(
            "DELETE FROM tickets_filter_rules "
            "WHERE kind = 'spam_body_pattern' AND created_by = 'migration:b4a2f9c1d8e0'",
        ),
    )
    op.drop_index("idx_ticket_classification_ticket_created", table_name="ticket_classification_audits")
    op.drop_index("idx_ticket_classification_source_created", table_name="ticket_classification_audits")
    op.drop_index("idx_ticket_classification_decision_created", table_name="ticket_classification_audits")
    op.drop_index("idx_ticket_classification_brand_created", table_name="ticket_classification_audits")
    op.drop_table("ticket_classification_audits")

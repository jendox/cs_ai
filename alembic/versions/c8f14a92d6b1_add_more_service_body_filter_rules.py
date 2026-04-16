"""add more service body filter rules

Revision ID: c8f14a92d6b1
Revises: b4a2f9c1d8e0
Create Date: 2026-04-16 00:00:00.000000

"""
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op
from src import db

# revision identifiers, used by Alembic.
revision: str = "c8f14a92d6b1"
down_revision: str | Sequence[str] | None = "b4a2f9c1d8e0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CREATED_BY = "migration:c8f14a92d6b1"


def upgrade() -> None:
    """Upgrade schema."""
    now = datetime.now(UTC)
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
                "kind": "customer_body_pattern",
                "value": r"You received new messages from.{0,4000}Reply in Chat",
                "is_regex": True,
                "brand_id": None,
                "via_channel": None,
                "is_active": True,
                "comment": "Customer exception for TikTok Shop chat notification emails.",
                "created_by": CREATED_BY,
                "updated_by": CREATED_BY,
                "created_at": now,
                "updated_at": now,
            },
            {
                "kind": "spam_body_pattern",
                "value": (
                    r"Shopify partner.{0,240}"
                    r"(optimization and growth|growth update|high-converting|store competitive)"
                ),
                "is_regex": True,
                "brand_id": None,
                "via_channel": None,
                "is_active": True,
                "comment": "High-confidence body pattern for Shopify/ecommerce growth outreach.",
                "created_by": CREATED_BY,
                "updated_by": CREATED_BY,
                "created_at": now,
                "updated_at": now,
            },
            {
                "kind": "spam_body_pattern",
                "value": r"(Country Account Manager.{0,240}Alibaba\.com|strategic partnership.{0,240}Alibaba\.com)",
                "is_regex": True,
                "brand_id": None,
                "via_channel": None,
                "is_active": True,
                "comment": "High-confidence body pattern for Alibaba partnership outreach.",
                "created_by": CREATED_BY,
                "updated_by": CREATED_BY,
                "created_at": now,
                "updated_at": now,
            },
            {
                "kind": "spam_body_pattern",
                "value": r"(capsule and powder manufacturing capabilities|stick pack machine|premium delivery formats)",
                "is_regex": True,
                "brand_id": None,
                "via_channel": None,
                "is_active": True,
                "comment": "High-confidence body pattern for supplement manufacturing outreach.",
                "created_by": CREATED_BY,
                "updated_by": CREATED_BY,
                "created_at": now,
                "updated_at": now,
            },
            {
                "kind": "spam_body_pattern",
                "value": r"Export Marketing Executive.{0,240}(Herbs|Spices|Oilseeds|agro products)",
                "is_regex": True,
                "brand_id": None,
                "via_channel": None,
                "is_active": True,
                "comment": "High-confidence body pattern for supplier/export outreach.",
                "created_by": CREATED_BY,
                "updated_by": CREATED_BY,
                "created_at": now,
                "updated_at": now,
            },
            {
                "kind": "spam_body_pattern",
                "value": r"represent \[?HONEY TRADERS\]?.{0,260}(short-form video|influencer|TikTok Shop)",
                "is_regex": True,
                "brand_id": None,
                "via_channel": None,
                "is_active": True,
                "comment": "High-confidence body pattern for influencer/trade outreach.",
                "created_by": CREATED_BY,
                "updated_by": CREATED_BY,
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
            "WHERE created_by = :created_by",
        ).bindparams(created_by=CREATED_BY),
    )

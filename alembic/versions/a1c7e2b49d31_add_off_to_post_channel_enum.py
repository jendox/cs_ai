"""Add OFF value to post_channel_enum

Revision ID: a1c7e2b49d31
Revises: f3d2b1a7c9e4
Create Date: 2026-04-17 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = "a1c7e2b49d31"
down_revision: Union[str, Sequence[str], None] = "f3d2b1a7c9e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE post_channel_enum ADD VALUE IF NOT EXISTS 'OFF'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values; rebuild the type.
    op.execute("ALTER TYPE post_channel_enum RENAME TO post_channel_enum_old")
    op.execute("CREATE TYPE post_channel_enum AS ENUM ('INTERNAL', 'PUBLIC')")
    op.execute(
        "ALTER TABLE our_posts ALTER COLUMN channel TYPE post_channel_enum "
        "USING channel::text::post_channel_enum",
    )
    op.execute(
        "ALTER TABLE ticket_reply_attempts ALTER COLUMN channel TYPE post_channel_enum "
        "USING channel::text::post_channel_enum",
    )
    op.execute(
        "ALTER TABLE zendesk_runtime_settings ALTER COLUMN review_channel TYPE post_channel_enum "
        "USING review_channel::text::post_channel_enum",
    )
    op.execute("DROP TYPE post_channel_enum_old")

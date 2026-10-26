"""Add topic discovery provenance and idempotency key.

Revision ID: 7a8c2e14d90f
Revises: 1b965b23bce5
Create Date: 2026-10-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "7a8c2e14d90f"
down_revision: str | None = "1b965b23bce5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "topics",
        sa.Column("discovery_key", sa.String(length=64), nullable=True),
    )
    op.create_index("ix_topics_discovery_key", "topics", ["discovery_key"], unique=True)
    op.add_column(
        "topics",
        sa.Column(
            "discovery_metadata",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("topics", "discovery_metadata")
    op.drop_index("ix_topics_discovery_key", table_name="topics")
    op.drop_column("topics", "discovery_key")

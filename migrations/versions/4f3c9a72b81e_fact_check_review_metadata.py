"""Persist fact-check review rationale and evidence assessment.

Revision ID: 4f3c9a72b81e
Revises: 7a8c2e14d90f
Create Date: 2026-10-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "4f3c9a72b81e"
down_revision: str | None = "7a8c2e14d90f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "claims",
        sa.Column(
            "review_metadata",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("claims", "review_metadata")

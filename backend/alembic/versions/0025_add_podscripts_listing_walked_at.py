"""Record when a podcast's podscripts listing was last walked to its end.

The column starts empty for every podcast, so the next discovery run reads
each listing in full, as before, and only then records a complete walk.

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-17 06:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op  # type: ignore[attr-defined]

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the nullable complete-walk timestamp to podcasts."""
    op.add_column(
        "podcasts",
        sa.Column(
            "podscripts_listing_walked_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Drop the complete-walk timestamp."""
    op.drop_column("podcasts", "podscripts_listing_walked_at")

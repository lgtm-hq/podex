"""Store the season that qualifies a feed's per-season episode number.

The column starts empty for every episode. Discovery fills it on a row it
creates, and on an existing row that it matches by GUID or URL with the same
episode number; a stored season is never overwritten (ADR 0020).

Revision ID: 0027
Revises: 0026
Create Date: 2026-11-17 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op  # type: ignore[attr-defined]

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the nullable season number to episodes."""
    op.add_column(
        "episodes",
        sa.Column("season_number", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    """Drop the season number."""
    op.drop_column("episodes", "season_number")

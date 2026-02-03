"""Add enrichment tracking fields.

Revision ID: 008_add_enrichment_tracking
Revises: 007_add_discovery_tracking
Create Date: 2024-02-04

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "008_add_enrichment_tracking"
down_revision: str | None = "007_add_discovery_tracking"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add enrichment tracking fields to media table."""
    op.add_column(
        "media",
        sa.Column("enriched_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "media",
        sa.Column("enrichment_source", sa.String(50), nullable=True),
    )
    op.add_column(
        "media",
        sa.Column("enrichment_confidence", sa.Float, nullable=True),
    )

    # Index for finding unenriched media
    op.create_index("ix_media_enriched_at", "media", ["enriched_at"])


def downgrade() -> None:
    """Remove enrichment tracking fields."""
    op.drop_index("ix_media_enriched_at", table_name="media")
    op.drop_column("media", "enrichment_confidence")
    op.drop_column("media", "enrichment_source")
    op.drop_column("media", "enriched_at")

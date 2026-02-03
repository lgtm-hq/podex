"""Add verification tracking fields.

Revision ID: 009_add_verification_tracking
Revises: 008_add_enrichment_tracking
Create Date: 2024-02-04

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "009_add_verification_tracking"
down_revision: str | None = "008_add_enrichment_tracking"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add multi-source verification tracking fields to media table."""
    # Add Semantic Scholar ID field
    op.add_column(
        "media",
        sa.Column("semantic_scholar_id", sa.String(50), nullable=True),
    )

    # Add fields for multi-source verification
    op.add_column(
        "media",
        sa.Column("verification_sources", sa.JSON, nullable=True),
    )
    op.add_column(
        "media",
        sa.Column("doi_verified", sa.Boolean, default=False, nullable=True),
    )


def downgrade() -> None:
    """Remove verification tracking fields."""
    op.drop_column("media", "doi_verified")
    op.drop_column("media", "verification_sources")
    op.drop_column("media", "semantic_scholar_id")

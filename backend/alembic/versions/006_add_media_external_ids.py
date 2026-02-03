"""Add external ID fields to media table.

Adds wikipedia_id, pubmed_id, and doi fields for linking to external sources.
These support the new person and place media types, as well as better
linking for studies and other media.

Revision ID: 006_add_media_external_ids
Revises: 005_add_media_composite_index
Create Date: 2026-02-04 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "006_add_media_external_ids"
down_revision: str | None = "005_add_media_composite_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add new external ID columns for linking to sources
    op.add_column(
        "media",
        sa.Column("wikipedia_id", sa.String(100), nullable=True),
    )
    op.add_column(
        "media",
        sa.Column("pubmed_id", sa.String(50), nullable=True),
    )
    op.add_column(
        "media",
        sa.Column("doi", sa.String(100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("media", "doi")
    op.drop_column("media", "pubmed_id")
    op.drop_column("media", "wikipedia_id")

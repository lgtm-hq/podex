"""Add enrichment identifier and provenance columns to media.

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-18 00:00:00.000000
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa

from alembic import op  # type: ignore[attr-defined]

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS: list["sa.Column[Any]"] = [
    sa.Column("google_books_id", sa.String(length=50), nullable=True),
    sa.Column("open_library_id", sa.String(length=50), nullable=True),
    sa.Column("imdb_id", sa.String(length=20), nullable=True),
    sa.Column("tmdb_id", sa.Integer(), nullable=True),
    sa.Column("wikipedia_id", sa.String(length=100), nullable=True),
    sa.Column("pubmed_id", sa.String(length=50), nullable=True),
    sa.Column("doi", sa.String(length=100), nullable=True),
    sa.Column("semantic_scholar_id", sa.String(length=50), nullable=True),
    sa.Column("metadata_json", sa.JSON(), nullable=True),
    sa.Column("verification_sources", sa.JSON(), nullable=True),
    sa.Column("doi_verified", sa.Boolean(), nullable=True),
    sa.Column("enriched_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("enrichment_source", sa.String(length=50), nullable=True),
    sa.Column("enrichment_confidence", sa.Float(), nullable=True),
]


def upgrade() -> None:
    """Add enrichment columns to media."""
    for column in _COLUMNS:
        op.add_column("media", column)


def downgrade() -> None:
    """Remove enrichment columns from media."""
    for column in reversed(_COLUMNS):
        op.drop_column("media", column.name)

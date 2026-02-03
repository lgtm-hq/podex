"""Add performance indexes for commonly queried columns.

Revision ID: 010_add_performance_indexes
Revises: 009_add_verification_tracking
Create Date: 2024-02-04

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "010_add_performance_indexes"
down_revision: str | None = "009_add_verification_tracking"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add performance indexes."""
    # Index on episodes.podcast_id for FK joins - currently missing
    op.create_index(
        "ix_episodes_podcast_id",
        "episodes",
        ["podcast_id"],
        unique=False,
        if_not_exists=True,
    )

    # Composite index for mentions - useful for uniqueness checks and joins
    op.create_index(
        "ix_mentions_episode_media",
        "mentions",
        ["episode_id", "media_id"],
        unique=False,
        if_not_exists=True,
    )

    # Indexes on media external IDs for lookup queries
    op.create_index(
        "ix_media_google_books_id",
        "media",
        ["google_books_id"],
        unique=False,
        if_not_exists=True,
    )

    op.create_index(
        "ix_media_imdb_id",
        "media",
        ["imdb_id"],
        unique=False,
        if_not_exists=True,
    )

    op.create_index(
        "ix_media_tmdb_id",
        "media",
        ["tmdb_id"],
        unique=False,
        if_not_exists=True,
    )

    op.create_index(
        "ix_media_doi",
        "media",
        ["doi"],
        unique=False,
        if_not_exists=True,
    )

    op.create_index(
        "ix_media_pubmed_id",
        "media",
        ["pubmed_id"],
        unique=False,
        if_not_exists=True,
    )

    op.create_index(
        "ix_media_semantic_scholar_id",
        "media",
        ["semantic_scholar_id"],
        unique=False,
        if_not_exists=True,
    )


def downgrade() -> None:
    """Remove performance indexes."""
    op.drop_index("ix_media_semantic_scholar_id", table_name="media", if_exists=True)
    op.drop_index("ix_media_pubmed_id", table_name="media", if_exists=True)
    op.drop_index("ix_media_doi", table_name="media", if_exists=True)
    op.drop_index("ix_media_tmdb_id", table_name="media", if_exists=True)
    op.drop_index("ix_media_imdb_id", table_name="media", if_exists=True)
    op.drop_index("ix_media_google_books_id", table_name="media", if_exists=True)
    op.drop_index("ix_mentions_episode_media", table_name="mentions", if_exists=True)
    op.drop_index("ix_episodes_podcast_id", table_name="episodes", if_exists=True)

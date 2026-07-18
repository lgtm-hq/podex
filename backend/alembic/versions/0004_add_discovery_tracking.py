"""Add discovery-tracking columns to podcasts and episodes.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-18 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op  # type: ignore[attr-defined]

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SOURCES = ("podscripts", "rss", "youtube", "spotify", "apple", "manual")


def _source_enum() -> sa.Enum:
    """Return the non-native enum type shared by both discovery columns."""
    return sa.Enum(*_SOURCES, native_enum=False, length=50)


def upgrade() -> None:
    """Add provider handles, source ids, and dedup indexes."""
    op.add_column("podcasts", sa.Column("rss_url", sa.String(500), nullable=True))
    op.add_column("podcasts", sa.Column("spotify_id", sa.String(50), nullable=True))
    op.add_column("podcasts", sa.Column("apple_id", sa.String(50), nullable=True))
    op.add_column(
        "podcasts",
        sa.Column("youtube_channel_id", sa.String(30), nullable=True),
    )
    op.add_column(
        "podcasts",
        sa.Column("podscripts_slug", sa.String(100), nullable=True),
    )
    op.add_column(
        "podcasts",
        sa.Column("discovery_source", _source_enum(), nullable=True),
    )

    op.add_column(
        "episodes",
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
    )
    op.add_column(
        "episodes",
        sa.Column("thumbnail_url", sa.String(1000), nullable=True),
    )
    op.add_column("episodes", sa.Column("youtube_id", sa.String(20), nullable=True))
    op.add_column("episodes", sa.Column("spotify_uri", sa.String(50), nullable=True))
    op.add_column("episodes", sa.Column("apple_id", sa.String(50), nullable=True))
    op.add_column("episodes", sa.Column("rss_guid", sa.String(500), nullable=True))
    op.add_column("episodes", sa.Column("episode_url", sa.String(500), nullable=True))
    op.add_column(
        "episodes",
        sa.Column("discovery_source", _source_enum(), nullable=True),
    )

    op.create_index("ix_podcasts_rss_url", "podcasts", ["rss_url"])
    op.create_index("ix_podcasts_spotify_id", "podcasts", ["spotify_id"])
    op.create_index("ix_podcasts_podscripts_slug", "podcasts", ["podscripts_slug"])
    op.create_index("ix_episodes_youtube_id", "episodes", ["youtube_id"])
    op.create_index("ix_episodes_spotify_uri", "episodes", ["spotify_uri"])
    op.create_index("ix_episodes_rss_guid", "episodes", ["rss_guid"])


def downgrade() -> None:
    """Remove discovery-tracking columns and their indexes."""
    op.drop_index("ix_episodes_rss_guid", table_name="episodes")
    op.drop_index("ix_episodes_spotify_uri", table_name="episodes")
    op.drop_index("ix_episodes_youtube_id", table_name="episodes")
    op.drop_index("ix_podcasts_podscripts_slug", table_name="podcasts")
    op.drop_index("ix_podcasts_spotify_id", table_name="podcasts")
    op.drop_index("ix_podcasts_rss_url", table_name="podcasts")

    op.drop_column("episodes", "discovery_source")
    op.drop_column("episodes", "episode_url")
    op.drop_column("episodes", "rss_guid")
    op.drop_column("episodes", "apple_id")
    op.drop_column("episodes", "spotify_uri")
    op.drop_column("episodes", "youtube_id")
    op.drop_column("episodes", "thumbnail_url")
    op.drop_column("episodes", "duration_seconds")

    op.drop_column("podcasts", "discovery_source")
    op.drop_column("podcasts", "podscripts_slug")
    op.drop_column("podcasts", "youtube_channel_id")
    op.drop_column("podcasts", "apple_id")
    op.drop_column("podcasts", "spotify_id")
    op.drop_column("podcasts", "rss_url")

"""Add discovery tracking fields.

Revision ID: 007_add_discovery_tracking
Revises: 006_add_media_external_ids
Create Date: 2024-02-04

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "007_add_discovery_tracking"
down_revision: str | None = "006_add_media_external_ids"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add discovery tracking fields to podcasts and episodes."""
    # Podcast table additions
    op.add_column(
        "podcasts",
        sa.Column("status", sa.String(20), server_default="watchlist", nullable=False),
    )
    op.add_column("podcasts", sa.Column("rss_url", sa.String(500), nullable=True))
    op.add_column("podcasts", sa.Column("spotify_id", sa.String(50), nullable=True))
    op.add_column("podcasts", sa.Column("apple_id", sa.String(50), nullable=True))
    op.add_column(
        "podcasts", sa.Column("youtube_channel_id", sa.String(30), nullable=True)
    )
    op.add_column(
        "podcasts", sa.Column("podscripts_slug", sa.String(100), nullable=True)
    )
    op.add_column(
        "podcasts", sa.Column("discovery_source", sa.String(50), nullable=True)
    )

    # Episode table additions
    op.add_column("episodes", sa.Column("spotify_uri", sa.String(50), nullable=True))
    op.add_column("episodes", sa.Column("apple_id", sa.String(50), nullable=True))
    op.add_column("episodes", sa.Column("rss_guid", sa.String(500), nullable=True))
    op.add_column("episodes", sa.Column("episode_url", sa.String(500), nullable=True))
    op.add_column(
        "episodes", sa.Column("discovery_source", sa.String(50), nullable=True)
    )

    # Indexes for deduplication lookups
    op.create_index("ix_episodes_spotify_uri", "episodes", ["spotify_uri"])
    op.create_index("ix_episodes_rss_guid", "episodes", ["rss_guid"])
    op.create_index("ix_podcasts_spotify_id", "podcasts", ["spotify_id"])
    op.create_index("ix_podcasts_rss_url", "podcasts", ["rss_url"])
    op.create_index("ix_podcasts_podscripts_slug", "podcasts", ["podscripts_slug"])


def downgrade() -> None:
    """Remove discovery tracking fields."""
    # Drop indexes
    op.drop_index("ix_podcasts_podscripts_slug", table_name="podcasts")
    op.drop_index("ix_podcasts_rss_url", table_name="podcasts")
    op.drop_index("ix_podcasts_spotify_id", table_name="podcasts")
    op.drop_index("ix_episodes_rss_guid", table_name="episodes")
    op.drop_index("ix_episodes_spotify_uri", table_name="episodes")

    # Drop episode columns
    op.drop_column("episodes", "discovery_source")
    op.drop_column("episodes", "episode_url")
    op.drop_column("episodes", "rss_guid")
    op.drop_column("episodes", "apple_id")
    op.drop_column("episodes", "spotify_uri")

    # Drop podcast columns
    op.drop_column("podcasts", "discovery_source")
    op.drop_column("podcasts", "podscripts_slug")
    op.drop_column("podcasts", "youtube_channel_id")
    op.drop_column("podcasts", "apple_id")
    op.drop_column("podcasts", "spotify_id")
    op.drop_column("podcasts", "rss_url")
    op.drop_column("podcasts", "status")

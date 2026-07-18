"""Add indexes for hot catalog read queries.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-18 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op  # type: ignore[attr-defined]

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add composite and supporting indexes for common list endpoints."""
    op.create_index("ix_episodes_published_at", "episodes", ["published_at"])
    op.create_index("ix_media_type_title", "media", ["type", "title"])
    op.create_index(
        "ix_mentions_media_id_episode_id",
        "mentions",
        ["media_id", "episode_id"],
    )
    op.create_index(
        "ix_mentions_episode_id_timestamp_seconds",
        "mentions",
        ["episode_id", "timestamp_seconds"],
    )


def downgrade() -> None:
    """Remove hot-query indexes."""
    op.drop_index("ix_mentions_episode_id_timestamp_seconds", table_name="mentions")
    op.drop_index("ix_mentions_media_id_episode_id", table_name="mentions")
    op.drop_index("ix_media_type_title", table_name="media")
    op.drop_index("ix_episodes_published_at", table_name="episodes")

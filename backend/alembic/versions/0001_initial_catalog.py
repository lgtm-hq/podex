"""Initial catalog schema.

Revision ID: 0001
Revises:
Create Date: 2026-07-06 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MEDIA_TYPES = (
    "book",
    "movie",
    "documentary",
    "tv_show",
    "study",
    "podcast",
    "article",
    "person",
    "place",
)


def upgrade() -> None:
    """Create the podcasts, episodes, media, and mentions tables."""
    op.create_table(
        "podcasts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_podcasts_name", "podcasts", ["name"])
    op.create_index("ix_podcasts_slug", "podcasts", ["slug"], unique=True)

    op.create_table(
        "episodes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("podcast_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("episode_number", sa.Integer(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["podcast_id"],
            ["podcasts.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_episodes_title", "episodes", ["title"])
    op.create_index(
        "ix_episodes_podcast_id_published_at",
        "episodes",
        ["podcast_id", "published_at"],
    )

    op.create_table(
        "media",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "type",
            sa.Enum(*_MEDIA_TYPES, native_enum=False, length=50),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("author", sa.String(length=255), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("description", sa.String(length=2000), nullable=True),
        sa.Column("cover_url", sa.String(length=1000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_media_type", "media", ["type"])
    op.create_index("ix_media_title", "media", ["title"])

    op.create_table(
        "mentions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("episode_id", sa.Integer(), nullable=False),
        sa.Column("media_id", sa.Integer(), nullable=False),
        sa.Column("timestamp_seconds", sa.Integer(), nullable=True),
        sa.Column("context", sa.String(length=2000), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["episode_id"],
            ["episodes.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["media_id"],
            ["media.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_mentions_episode_id", "mentions", ["episode_id"])
    op.create_index("ix_mentions_media_id", "mentions", ["media_id"])
    op.create_index(
        "ix_mentions_episode_media",
        "mentions",
        ["episode_id", "media_id"],
    )


def downgrade() -> None:
    """Drop the catalog tables."""
    op.drop_table("mentions")
    op.drop_table("media")
    op.drop_table("episodes")
    op.drop_table("podcasts")

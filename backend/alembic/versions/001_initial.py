"""Initial migration - create all tables.

Revision ID: 001_initial
Revises:
Create Date: 2024-01-01 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "podcasts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("cover_url", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_podcasts_slug"), "podcasts", ["slug"], unique=True)

    op.create_table(
        "episodes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("podcast_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("episode_number", sa.Integer(), nullable=True),
        sa.Column("youtube_id", sa.String(length=20), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("thumbnail_url", sa.String(length=500), nullable=True),
        sa.Column("transcript_status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["podcast_id"], ["podcasts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_episodes_youtube_id"), "episodes", ["youtube_id"], unique=False
    )

    op.create_table(
        "media",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("author", sa.String(length=255), nullable=True),
        sa.Column("cover_url", sa.String(length=500), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("google_books_id", sa.String(length=50), nullable=True),
        sa.Column("open_library_id", sa.String(length=50), nullable=True),
        sa.Column("imdb_id", sa.String(length=20), nullable=True),
        sa.Column("tmdb_id", sa.Integer(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_media_type"), "media", ["type"], unique=False)
    op.create_index(op.f("ix_media_title"), "media", ["title"], unique=False)

    op.create_table(
        "mentions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("episode_id", sa.Integer(), nullable=False),
        sa.Column("media_id", sa.Integer(), nullable=False),
        sa.Column("timestamp_seconds", sa.Integer(), nullable=True),
        sa.Column("context", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["episode_id"], ["episodes.id"]),
        sa.ForeignKeyConstraint(["media_id"], ["media.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_mentions_episode_id"), "mentions", ["episode_id"], unique=False
    )
    op.create_index(
        op.f("ix_mentions_media_id"), "mentions", ["media_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_mentions_media_id"), table_name="mentions")
    op.drop_index(op.f("ix_mentions_episode_id"), table_name="mentions")
    op.drop_table("mentions")

    op.drop_index(op.f("ix_media_title"), table_name="media")
    op.drop_index(op.f("ix_media_type"), table_name="media")
    op.drop_table("media")

    op.drop_index(op.f("ix_episodes_youtube_id"), table_name="episodes")
    op.drop_table("episodes")

    op.drop_index(op.f("ix_podcasts_slug"), table_name="podcasts")
    op.drop_table("podcasts")

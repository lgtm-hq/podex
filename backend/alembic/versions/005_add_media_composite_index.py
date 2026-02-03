"""Add composite index on media type and title.

Revision ID: 005_add_media_composite_index
Revises: 004_add_transcription_tracking
Create Date: 2026-02-04 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "005_add_media_composite_index"
down_revision: str | None = "004_add_transcription_tracking"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add composite index on type and title for faster lookups
    op.create_index(
        "ix_media_type_title",
        "media",
        ["type", "title"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_media_type_title", table_name="media")

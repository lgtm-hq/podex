"""Add transcripts table.

Revision ID: 002_add_transcripts
Revises: 001_initial
Create Date: 2026-02-03 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "002_add_transcripts"
down_revision: str | None = "001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "transcripts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("episode_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("segments_json", sa.JSON(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["episode_id"], ["episodes.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_transcripts_episode_id"), "transcripts", ["episode_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_transcripts_episode_id"), table_name="transcripts")
    op.drop_table("transcripts")

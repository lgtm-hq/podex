"""Add transcription tracking enhancements.

Revision ID: 004_add_transcription_tracking
Revises: 003_add_ingestion_runs
Create Date: 2026-02-03 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "004_add_transcription_tracking"
down_revision: str | None = "003_add_ingestion_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add new columns to episodes
    op.add_column(
        "episodes",
        sa.Column("extraction_status", sa.String(20), server_default="pending"),
    )
    op.add_column(
        "episodes",
        sa.Column("cleanup_status", sa.String(20), server_default="pending"),
    )

    # Add cleaned_text and cleaned_at to transcripts
    op.add_column(
        "transcripts",
        sa.Column("cleaned_text", sa.Text(), nullable=True),
    )
    op.add_column(
        "transcripts",
        sa.Column("cleaned_at", sa.DateTime(), nullable=True),
    )

    # Create transcription_jobs table for detailed tracking
    op.create_table(
        "transcription_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("episode_id", sa.Integer(), nullable=False),
        sa.Column("job_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("backend", sa.String(50), nullable=True),
        sa.Column("model", sa.String(50), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["episode_id"], ["episodes.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_transcription_jobs_episode_id",
        "transcription_jobs",
        ["episode_id"],
    )
    op.create_index(
        "ix_transcription_jobs_status",
        "transcription_jobs",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_transcription_jobs_status", table_name="transcription_jobs")
    op.drop_index("ix_transcription_jobs_episode_id", table_name="transcription_jobs")
    op.drop_table("transcription_jobs")

    op.drop_column("transcripts", "cleaned_at")
    op.drop_column("transcripts", "cleaned_text")
    op.drop_column("episodes", "cleanup_status")
    op.drop_column("episodes", "extraction_status")

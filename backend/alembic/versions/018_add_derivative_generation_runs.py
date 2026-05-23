"""Add derivative generation run records.

Revision ID: 018_add_derivative_generation_runs
Revises: 017_add_derivative_summaries
Create Date: 2026-05-22

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "018_add_derivative_generation_runs"
down_revision: str | None = "017_add_derivative_summaries"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create derivative generation run table."""
    op.create_table(
        "derivative_generation_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_key", sa.String(length=120), nullable=False),
        sa.Column("episode_id", sa.Integer(), nullable=False),
        sa.Column("transcript_id", sa.Integer(), nullable=True),
        sa.Column("episode_summary_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("pipeline_version", sa.String(length=80), nullable=False),
        sa.Column("chunk_pipeline_version", sa.String(length=80), nullable=False),
        sa.Column("summary_prompt_version", sa.String(length=80), nullable=False),
        sa.Column("summary_model", sa.String(length=120), nullable=True),
        sa.Column("source_text_hash", sa.String(length=64), nullable=False),
        sa.Column("semantic_chunks_created", sa.Integer(), nullable=False),
        sa.Column("semantic_chunks_updated", sa.Integer(), nullable=False),
        sa.Column("semantic_chunks_deleted", sa.Integer(), nullable=False),
        sa.Column("semantic_chunks_embedded", sa.Integer(), nullable=False),
        sa.Column("semantic_chunks_failed", sa.Integer(), nullable=False),
        sa.Column("media_summaries_generated", sa.Integer(), nullable=False),
        sa.Column("graph_triples_upserted", sa.Integer(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["episode_id"], ["episodes.id"]),
        sa.ForeignKeyConstraint(["episode_summary_id"], ["episode_summaries.id"]),
        sa.ForeignKeyConstraint(["transcript_id"], ["transcripts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_key",
            name="uq_derivative_generation_runs_run_key",
        ),
    )
    for column_name, unique in (
        ("run_key", True),
        ("episode_id", False),
        ("transcript_id", False),
        ("episode_summary_id", False),
        ("status", False),
        ("pipeline_version", False),
        ("chunk_pipeline_version", False),
        ("summary_prompt_version", False),
        ("summary_model", False),
        ("source_text_hash", False),
    ):
        op.create_index(
            f"ix_derivative_generation_runs_{column_name}",
            "derivative_generation_runs",
            [column_name],
            unique=unique,
        )


def downgrade() -> None:
    """Drop derivative generation run table."""
    for column_name in (
        "source_text_hash",
        "summary_model",
        "summary_prompt_version",
        "chunk_pipeline_version",
        "pipeline_version",
        "status",
        "episode_summary_id",
        "transcript_id",
        "episode_id",
        "run_key",
    ):
        op.drop_index(
            f"ix_derivative_generation_runs_{column_name}",
            table_name="derivative_generation_runs",
            if_exists=True,
        )
    op.drop_table("derivative_generation_runs")

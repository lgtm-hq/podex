"""Add episode and media derivative summaries.

Revision ID: 017_add_derivative_summaries
Revises: 016_add_graph_triples
Create Date: 2026-05-22

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "017_add_derivative_summaries"
down_revision: str | None = "016_add_graph_triples"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create derivative summary tables."""
    op.create_table(
        "episode_summaries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("episode_id", sa.Integer(), nullable=False),
        sa.Column("summary_key", sa.String(length=120), nullable=False),
        sa.Column("summary_kind", sa.String(length=32), nullable=False),
        sa.Column("pipeline_version", sa.String(length=80), nullable=False),
        sa.Column("prompt_version", sa.String(length=80), nullable=False),
        sa.Column("source_text_hash", sa.String(length=64), nullable=False),
        sa.Column("source_model", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("summary_text", sa.Text(), nullable=False),
        sa.Column("short_summary", sa.Text(), nullable=True),
        sa.Column("highlights_json", sa.JSON(), nullable=True),
        sa.Column("citations_json", sa.JSON(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["episode_id"], ["episodes.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("summary_key", name="uq_episode_summaries_summary_key"),
    )
    _create_summary_indexes(
        table_name="episode_summaries",
        resource_column="episode_id",
    )

    op.create_table(
        "media_summaries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("media_id", sa.Integer(), nullable=False),
        sa.Column("summary_key", sa.String(length=120), nullable=False),
        sa.Column("summary_kind", sa.String(length=32), nullable=False),
        sa.Column("pipeline_version", sa.String(length=80), nullable=False),
        sa.Column("prompt_version", sa.String(length=80), nullable=False),
        sa.Column("source_text_hash", sa.String(length=64), nullable=False),
        sa.Column("source_model", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("summary_text", sa.Text(), nullable=False),
        sa.Column("short_summary", sa.Text(), nullable=True),
        sa.Column("highlights_json", sa.JSON(), nullable=True),
        sa.Column("citations_json", sa.JSON(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["media_id"], ["media.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("summary_key", name="uq_media_summaries_summary_key"),
    )
    _create_summary_indexes(
        table_name="media_summaries",
        resource_column="media_id",
    )


def downgrade() -> None:
    """Drop derivative summary tables."""
    _drop_summary_indexes(
        table_name="media_summaries",
        resource_column="media_id",
    )
    op.drop_table("media_summaries")

    _drop_summary_indexes(
        table_name="episode_summaries",
        resource_column="episode_id",
    )
    op.drop_table("episode_summaries")


def _create_summary_indexes(
    *,
    table_name: str,
    resource_column: str,
) -> None:
    """Create common derivative summary indexes."""
    for column_name, unique in (
        ("summary_key", True),
        (resource_column, False),
        ("summary_kind", False),
        ("pipeline_version", False),
        ("prompt_version", False),
        ("source_text_hash", False),
        ("source_model", False),
        ("status", False),
    ):
        op.create_index(
            f"ix_{table_name}_{column_name}",
            table_name,
            [column_name],
            unique=unique,
        )


def _drop_summary_indexes(
    *,
    table_name: str,
    resource_column: str,
) -> None:
    """Drop common derivative summary indexes."""
    for column_name in (
        "status",
        "source_model",
        "source_text_hash",
        "prompt_version",
        "pipeline_version",
        "summary_kind",
        resource_column,
        "summary_key",
    ):
        op.drop_index(
            f"ix_{table_name}_{column_name}",
            table_name=table_name,
            if_exists=True,
        )

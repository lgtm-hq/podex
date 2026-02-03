"""Add search projection repair tracking.

Revision ID: 013_add_search_projection_repairs
Revises: 012_add_mention_candidate_provenance
Create Date: 2026-04-20

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "013_add_search_projection_repairs"
down_revision: str | None = "012_add_mention_candidate_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the search projection repairs table."""
    op.create_table(
        "search_projection_repairs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("resource_type", sa.String(length=32), nullable=False),
        sa.Column("resource_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("source_job_id", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("last_attempted_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["source_job_id"], ["transcription_jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_search_projection_repairs_reason",
        "search_projection_repairs",
        ["reason"],
        unique=False,
    )
    op.create_index(
        "ix_search_projection_repairs_resource_id",
        "search_projection_repairs",
        ["resource_id"],
        unique=False,
    )
    op.create_index(
        "ix_search_projection_repairs_resource_type",
        "search_projection_repairs",
        ["resource_type"],
        unique=False,
    )
    op.create_index(
        "ix_search_projection_repairs_source_job_id",
        "search_projection_repairs",
        ["source_job_id"],
        unique=False,
    )
    op.create_index(
        "ix_search_projection_repairs_status",
        "search_projection_repairs",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the search projection repairs table."""
    op.drop_index(
        "ix_search_projection_repairs_status",
        table_name="search_projection_repairs",
        if_exists=True,
    )
    op.drop_index(
        "ix_search_projection_repairs_source_job_id",
        table_name="search_projection_repairs",
        if_exists=True,
    )
    op.drop_index(
        "ix_search_projection_repairs_resource_type",
        table_name="search_projection_repairs",
        if_exists=True,
    )
    op.drop_index(
        "ix_search_projection_repairs_resource_id",
        table_name="search_projection_repairs",
        if_exists=True,
    )
    op.drop_index(
        "ix_search_projection_repairs_reason",
        table_name="search_projection_repairs",
        if_exists=True,
    )
    op.drop_table("search_projection_repairs")

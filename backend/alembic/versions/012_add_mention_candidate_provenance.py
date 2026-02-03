"""Add mention candidate provenance table.

Revision ID: 012_add_mention_candidate_provenance
Revises: 011_add_review_and_audit_models
Create Date: 2026-04-20

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "012_add_mention_candidate_provenance"
down_revision: str | None = "011_add_review_and_audit_models"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the mention candidate provenance table."""
    op.create_table(
        "mention_candidate_provenance",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("mention_candidate_id", sa.Integer(), nullable=False),
        sa.Column("source_job_id", sa.Integer(), nullable=True),
        sa.Column("media_id", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("change_summary", sa.Text(), nullable=True),
        sa.Column("raw_title", sa.String(length=500), nullable=False),
        sa.Column("normalized_title", sa.String(length=500), nullable=True),
        sa.Column("suggested_author", sa.String(length=255), nullable=True),
        sa.Column("timestamp_seconds", sa.Integer(), nullable=True),
        sa.Column("context", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("extraction_source", sa.String(length=50), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["media_id"], ["media.id"]),
        sa.ForeignKeyConstraint(["mention_candidate_id"], ["mention_candidates.id"]),
        sa.ForeignKeyConstraint(["source_job_id"], ["transcription_jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_mention_candidate_provenance_event_type",
        "mention_candidate_provenance",
        ["event_type"],
        unique=False,
    )
    op.create_index(
        "ix_mention_candidate_provenance_media_id",
        "mention_candidate_provenance",
        ["media_id"],
        unique=False,
    )
    op.create_index(
        "ix_mention_candidate_provenance_mention_candidate_id",
        "mention_candidate_provenance",
        ["mention_candidate_id"],
        unique=False,
    )
    op.create_index(
        "ix_mention_candidate_provenance_source_job_id",
        "mention_candidate_provenance",
        ["source_job_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the mention candidate provenance table."""
    op.drop_index(
        "ix_mention_candidate_provenance_source_job_id",
        table_name="mention_candidate_provenance",
        if_exists=True,
    )
    op.drop_index(
        "ix_mention_candidate_provenance_mention_candidate_id",
        table_name="mention_candidate_provenance",
        if_exists=True,
    )
    op.drop_index(
        "ix_mention_candidate_provenance_media_id",
        table_name="mention_candidate_provenance",
        if_exists=True,
    )
    op.drop_index(
        "ix_mention_candidate_provenance_event_type",
        table_name="mention_candidate_provenance",
        if_exists=True,
    )
    op.drop_table("mention_candidate_provenance")

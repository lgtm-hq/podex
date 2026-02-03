"""Add review-state and audit-state tables.

Revision ID: 011_add_review_and_audit_models
Revises: 010_add_performance_indexes
Create Date: 2026-04-19

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "011_add_review_and_audit_models"
down_revision: str | None = "010_add_performance_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create review-state and audit-state tables."""
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.Integer(), nullable=True),
        sa.Column("resource_identifier", sa.String(length=120), nullable=True),
        sa.Column("actor_name", sa.String(length=100), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_audit_logs_action",
        "audit_logs",
        ["action"],
        unique=False,
    )
    op.create_index(
        "ix_audit_logs_resource_id",
        "audit_logs",
        ["resource_id"],
        unique=False,
    )
    op.create_index(
        "ix_audit_logs_resource_type",
        "audit_logs",
        ["resource_type"],
        unique=False,
    )

    op.create_table(
        "mention_candidates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("episode_id", sa.Integer(), nullable=False),
        sa.Column("media_id", sa.Integer(), nullable=True),
        sa.Column("source_job_id", sa.Integer(), nullable=True),
        sa.Column("mention_id", sa.Integer(), nullable=True),
        sa.Column("media_type", sa.String(length=20), nullable=False),
        sa.Column("raw_title", sa.String(length=500), nullable=False),
        sa.Column("normalized_title", sa.String(length=500), nullable=True),
        sa.Column("suggested_author", sa.String(length=255), nullable=True),
        sa.Column("timestamp_seconds", sa.Integer(), nullable=True),
        sa.Column("context", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("extraction_source", sa.String(length=50), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["episode_id"], ["episodes.id"]),
        sa.ForeignKeyConstraint(["media_id"], ["media.id"]),
        sa.ForeignKeyConstraint(["mention_id"], ["mentions.id"]),
        sa.ForeignKeyConstraint(["source_job_id"], ["transcription_jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("mention_id"),
    )
    op.create_index(
        "ix_mention_candidates_episode_id",
        "mention_candidates",
        ["episode_id"],
        unique=False,
    )
    op.create_index(
        "ix_mention_candidates_media_id",
        "mention_candidates",
        ["media_id"],
        unique=False,
    )
    op.create_index(
        "ix_mention_candidates_media_type",
        "mention_candidates",
        ["media_type"],
        unique=False,
    )
    op.create_index(
        "ix_mention_candidates_mention_id",
        "mention_candidates",
        ["mention_id"],
        unique=True,
    )
    op.create_index(
        "ix_mention_candidates_source_job_id",
        "mention_candidates",
        ["source_job_id"],
        unique=False,
    )
    op.create_index(
        "ix_mention_candidates_state",
        "mention_candidates",
        ["state"],
        unique=False,
    )

    op.create_table(
        "review_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("mention_candidate_id", sa.Integer(), nullable=False),
        sa.Column("target_media_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.String(length=16), nullable=False),
        sa.Column("assigned_to", sa.String(length=100), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["mention_candidate_id"], ["mention_candidates.id"]),
        sa.ForeignKeyConstraint(["target_media_id"], ["media.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("mention_candidate_id"),
    )
    op.create_index(
        "ix_review_items_mention_candidate_id",
        "review_items",
        ["mention_candidate_id"],
        unique=True,
    )
    op.create_index(
        "ix_review_items_priority",
        "review_items",
        ["priority"],
        unique=False,
    )
    op.create_index(
        "ix_review_items_status",
        "review_items",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_review_items_target_media_id",
        "review_items",
        ["target_media_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop review-state and audit-state tables."""
    op.drop_index(
        "ix_review_items_target_media_id",
        table_name="review_items",
        if_exists=True,
    )
    op.drop_index("ix_review_items_status", table_name="review_items", if_exists=True)
    op.drop_index(
        "ix_review_items_priority",
        table_name="review_items",
        if_exists=True,
    )
    op.drop_index(
        "ix_review_items_mention_candidate_id",
        table_name="review_items",
        if_exists=True,
    )
    op.drop_table("review_items")

    op.drop_index(
        "ix_mention_candidates_state",
        table_name="mention_candidates",
        if_exists=True,
    )
    op.drop_index(
        "ix_mention_candidates_source_job_id",
        table_name="mention_candidates",
        if_exists=True,
    )
    op.drop_index(
        "ix_mention_candidates_mention_id",
        table_name="mention_candidates",
        if_exists=True,
    )
    op.drop_index(
        "ix_mention_candidates_media_type",
        table_name="mention_candidates",
        if_exists=True,
    )
    op.drop_index(
        "ix_mention_candidates_media_id",
        table_name="mention_candidates",
        if_exists=True,
    )
    op.drop_index(
        "ix_mention_candidates_episode_id",
        table_name="mention_candidates",
        if_exists=True,
    )
    op.drop_table("mention_candidates")

    op.drop_index(
        "ix_audit_logs_resource_type",
        table_name="audit_logs",
        if_exists=True,
    )
    op.drop_index(
        "ix_audit_logs_resource_id",
        table_name="audit_logs",
        if_exists=True,
    )
    op.drop_index("ix_audit_logs_action", table_name="audit_logs", if_exists=True)
    op.drop_table("audit_logs")

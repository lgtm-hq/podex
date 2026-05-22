"""Add scheduler, transcript retention, and media alias persistence.

Revision ID: 014_add_scheduler_retention_aliases
Revises: 013_add_search_projection_repairs
Create Date: 2026-05-10

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "014_add_scheduler_retention_aliases"
down_revision: str | None = "013_add_search_projection_repairs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create scheduling and alias tables and extend transcript retention state."""
    op.create_table(
        "pipeline_schedules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("schedule_key", sa.String(length=160), nullable=False),
        sa.Column("task_kind", sa.String(length=32), nullable=False),
        sa.Column("interval_minutes", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("last_scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_pipeline_schedules_enabled",
        "pipeline_schedules",
        ["enabled"],
        unique=False,
    )
    op.create_index(
        "ix_pipeline_schedules_schedule_key",
        "pipeline_schedules",
        ["schedule_key"],
        unique=True,
    )
    op.create_index(
        "ix_pipeline_schedules_task_kind",
        "pipeline_schedules",
        ["task_kind"],
        unique=False,
    )

    op.create_table(
        "scheduled_work_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("schedule_id", sa.Integer(), nullable=False),
        sa.Column("ingestion_run_id", sa.Integer(), nullable=True),
        sa.Column("schedule_key", sa.String(length=160), nullable=False),
        sa.Column("work_key", sa.String(length=240), nullable=False),
        sa.Column("task_kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("interval_minutes", sa.Integer(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["ingestion_run_id"], ["ingestion_runs.id"]),
        sa.ForeignKeyConstraint(["schedule_id"], ["pipeline_schedules.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("work_key", name="uq_scheduled_work_items_work_key"),
    )
    op.create_index(
        "ix_scheduled_work_items_due_at",
        "scheduled_work_items",
        ["due_at"],
        unique=False,
    )
    op.create_index(
        "ix_scheduled_work_items_ingestion_run_id",
        "scheduled_work_items",
        ["ingestion_run_id"],
        unique=False,
    )
    op.create_index(
        "ix_scheduled_work_items_schedule_id",
        "scheduled_work_items",
        ["schedule_id"],
        unique=False,
    )
    op.create_index(
        "ix_scheduled_work_items_schedule_key",
        "scheduled_work_items",
        ["schedule_key"],
        unique=False,
    )
    op.create_index(
        "ix_scheduled_work_items_status",
        "scheduled_work_items",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_scheduled_work_items_task_kind",
        "scheduled_work_items",
        ["task_kind"],
        unique=False,
    )
    op.create_index(
        "ix_scheduled_work_items_work_key",
        "scheduled_work_items",
        ["work_key"],
        unique=True,
    )

    op.create_table(
        "media_aliases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("media_id", sa.Integer(), nullable=False),
        sa.Column("alias", sa.String(length=500), nullable=False),
        sa.Column("normalized_alias", sa.String(length=500), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["media_id"], ["media.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "media_id",
            "normalized_alias",
            name="uq_media_aliases_media_normalized_alias",
        ),
    )
    op.create_index(
        "ix_media_aliases_media_id",
        "media_aliases",
        ["media_id"],
        unique=False,
    )
    op.create_index(
        "ix_media_aliases_normalized_alias",
        "media_aliases",
        ["normalized_alias"],
        unique=False,
    )
    op.create_index(
        "ix_media_aliases_source",
        "media_aliases",
        ["source"],
        unique=False,
    )

    op.add_column(
        "transcripts",
        sa.Column(
            "retention_tier", sa.String(length=32), nullable=False, server_default="hot"
        ),
    )
    op.add_column(
        "transcripts",
        sa.Column("retention_policy_version", sa.String(length=80), nullable=True),
    )
    op.add_column(
        "transcripts",
        sa.Column("retention_evaluated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "transcripts",
        sa.Column(
            "retention_exempt_sample",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "transcripts",
        sa.Column(
            "source_retention_opt_out",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "transcripts",
        sa.Column("retention_blockers_json", sa.JSON(), nullable=True),
    )
    op.add_column(
        "transcripts",
        sa.Column("digest_text", sa.Text(), nullable=True),
    )
    op.add_column(
        "transcripts",
        sa.Column("digest_created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "transcripts",
        sa.Column("purge_eligible_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "transcripts",
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_transcripts_retention_tier",
        "transcripts",
        ["retention_tier"],
        unique=False,
    )


def downgrade() -> None:
    """Drop scheduling and alias tables and transcript retention columns."""
    op.drop_index(
        "ix_transcripts_retention_tier", table_name="transcripts", if_exists=True
    )
    op.drop_column("transcripts", "purged_at")
    op.drop_column("transcripts", "purge_eligible_at")
    op.drop_column("transcripts", "digest_created_at")
    op.drop_column("transcripts", "digest_text")
    op.drop_column("transcripts", "retention_blockers_json")
    op.drop_column("transcripts", "source_retention_opt_out")
    op.drop_column("transcripts", "retention_exempt_sample")
    op.drop_column("transcripts", "retention_evaluated_at")
    op.drop_column("transcripts", "retention_policy_version")
    op.drop_column("transcripts", "retention_tier")

    op.drop_index("ix_media_aliases_source", table_name="media_aliases", if_exists=True)
    op.drop_index(
        "ix_media_aliases_normalized_alias",
        table_name="media_aliases",
        if_exists=True,
    )
    op.drop_index(
        "ix_media_aliases_media_id",
        table_name="media_aliases",
        if_exists=True,
    )
    op.drop_table("media_aliases")

    op.drop_index(
        "ix_scheduled_work_items_work_key",
        table_name="scheduled_work_items",
        if_exists=True,
    )
    op.drop_index(
        "ix_scheduled_work_items_task_kind",
        table_name="scheduled_work_items",
        if_exists=True,
    )
    op.drop_index(
        "ix_scheduled_work_items_status",
        table_name="scheduled_work_items",
        if_exists=True,
    )
    op.drop_index(
        "ix_scheduled_work_items_schedule_key",
        table_name="scheduled_work_items",
        if_exists=True,
    )
    op.drop_index(
        "ix_scheduled_work_items_schedule_id",
        table_name="scheduled_work_items",
        if_exists=True,
    )
    op.drop_index(
        "ix_scheduled_work_items_ingestion_run_id",
        table_name="scheduled_work_items",
        if_exists=True,
    )
    op.drop_index(
        "ix_scheduled_work_items_due_at",
        table_name="scheduled_work_items",
        if_exists=True,
    )
    op.drop_table("scheduled_work_items")

    op.drop_index(
        "ix_pipeline_schedules_task_kind",
        table_name="pipeline_schedules",
        if_exists=True,
    )
    op.drop_index(
        "ix_pipeline_schedules_schedule_key",
        table_name="pipeline_schedules",
        if_exists=True,
    )
    op.drop_index(
        "ix_pipeline_schedules_enabled",
        table_name="pipeline_schedules",
        if_exists=True,
    )
    op.drop_table("pipeline_schedules")

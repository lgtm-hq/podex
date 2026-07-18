"""Add podcast status and scheduled-work tables.

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-18 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op  # type: ignore[attr-defined]

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PODCAST_STATUSES = ("watchlist", "active", "paused")


def upgrade() -> None:
    """Create scheduling tables and the podcast lifecycle status."""
    op.add_column(
        "podcasts",
        sa.Column(
            "status",
            sa.Enum(*_PODCAST_STATUSES, native_enum=False, length=20),
            server_default="watchlist",
            nullable=False,
        ),
    )
    op.create_index("ix_podcasts_status", "podcasts", ["status"])

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
        "ix_pipeline_schedules_schedule_key",
        "pipeline_schedules",
        ["schedule_key"],
        unique=True,
    )
    op.create_index(
        "ix_pipeline_schedules_task_kind",
        "pipeline_schedules",
        ["task_kind"],
    )
    op.create_index(
        "ix_pipeline_schedules_enabled",
        "pipeline_schedules",
        ["enabled"],
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
        sa.ForeignKeyConstraint(["schedule_id"], ["pipeline_schedules.id"]),
        sa.ForeignKeyConstraint(["ingestion_run_id"], ["ingestion_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("work_key", name="uq_scheduled_work_items_work_key"),
    )
    op.create_index(
        "ix_scheduled_work_items_schedule_id",
        "scheduled_work_items",
        ["schedule_id"],
    )
    op.create_index(
        "ix_scheduled_work_items_ingestion_run_id",
        "scheduled_work_items",
        ["ingestion_run_id"],
    )
    op.create_index(
        "ix_scheduled_work_items_schedule_key",
        "scheduled_work_items",
        ["schedule_key"],
    )
    op.create_index(
        "ix_scheduled_work_items_work_key",
        "scheduled_work_items",
        ["work_key"],
        unique=True,
    )
    op.create_index(
        "ix_scheduled_work_items_task_kind",
        "scheduled_work_items",
        ["task_kind"],
    )
    op.create_index(
        "ix_scheduled_work_items_status",
        "scheduled_work_items",
        ["status"],
    )
    op.create_index(
        "ix_scheduled_work_items_due_at",
        "scheduled_work_items",
        ["due_at"],
    )


def downgrade() -> None:
    """Drop scheduling tables and the podcast status column."""
    op.drop_table("scheduled_work_items")
    op.drop_table("pipeline_schedules")
    op.drop_index("ix_podcasts_status", table_name="podcasts")
    op.drop_column("podcasts", "status")

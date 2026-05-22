"""Scheduled pipeline work models."""

from datetime import UTC, datetime
from enum import StrEnum, auto
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from podex.models.base import Base

if TYPE_CHECKING:
    from podex.models.ingestion_run import IngestionRun


class ScheduledWorkStatus(StrEnum):
    """Lifecycle states for scheduled work items."""

    PENDING = auto()
    RUNNING = auto()
    COMPLETED = auto()
    FAILED = auto()
    SKIPPED = auto()


class PipelineSchedule(Base):
    """Recurring pipeline schedule tracked for ops visibility."""

    __tablename__ = "pipeline_schedules"

    id: Mapped[int] = mapped_column(primary_key=True)
    schedule_key: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    task_kind: Mapped[str] = mapped_column(String(32), index=True)
    interval_minutes: Mapped[int] = mapped_column()
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    last_scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    work_items: Mapped[list["ScheduledWorkItemModel"]] = relationship(
        back_populates="schedule",
    )


class ScheduledWorkItemModel(Base):
    """Persisted scheduled work item with an idempotency key."""

    __tablename__ = "scheduled_work_items"
    __table_args__ = (
        UniqueConstraint("work_key", name="uq_scheduled_work_items_work_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    schedule_id: Mapped[int] = mapped_column(
        ForeignKey("pipeline_schedules.id"),
        index=True,
    )
    ingestion_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("ingestion_runs.id"),
        index=True,
    )
    schedule_key: Mapped[str] = mapped_column(String(160), index=True)
    work_key: Mapped[str] = mapped_column(String(240), unique=True, index=True)
    task_kind: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(
        String(32),
        default=ScheduledWorkStatus.PENDING.value,
        index=True,
    )
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    interval_minutes: Mapped[int] = mapped_column()
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    schedule: Mapped[PipelineSchedule] = relationship(back_populates="work_items")
    ingestion_run: Mapped["IngestionRun | None"] = relationship()

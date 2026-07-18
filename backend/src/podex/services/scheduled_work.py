"""DB-backed scheduled pipeline work services."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from podex.models import PipelineSchedule, ScheduledWorkItemModel, ScheduledWorkStatus
from podex.services.scheduler import (
    IntervalSchedule,
    ScheduledTaskKind,
    compute_due_work_items,
)


@dataclass(frozen=True, slots=True)
class PipelineScheduleInputData:
    """Input for creating or updating a recurring pipeline schedule."""

    schedule_key: str
    task_kind: ScheduledTaskKind
    interval_minutes: int
    enabled: bool = True
    metadata_json: dict[str, object] | None = None
    start_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class PipelineScheduleSummaryData:
    """Ops-visible schedule state."""

    id: int
    schedule_key: str
    task_kind: ScheduledTaskKind
    interval_minutes: int
    enabled: bool
    metadata_json: dict[str, object] | None
    last_scheduled_at: datetime | None
    next_due_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ScheduledWorkItemSummaryData:
    """Ops-visible scheduled work item state."""

    id: int
    schedule_id: int
    ingestion_run_id: int | None
    schedule_key: str
    work_key: str
    task_kind: ScheduledTaskKind
    status: ScheduledWorkStatus
    due_at: datetime
    interval_minutes: int
    metadata_json: dict[str, object] | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


def upsert_pipeline_schedule(
    *,
    db: Session,
    payload: PipelineScheduleInputData,
    now: datetime | None = None,
) -> PipelineScheduleSummaryData:
    """Create or update a pipeline schedule.

    Args:
        db: Database session.
        payload: Desired schedule state.
        now: Timestamp used for deterministic next-due calculation.

    Returns:
        Updated schedule summary.
    """
    effective_now = now or datetime.now(UTC)
    schedule = (
        db.query(PipelineSchedule)
        .filter(PipelineSchedule.schedule_key == payload.schedule_key)
        .first()
    )

    if schedule is None:
        schedule = PipelineSchedule(schedule_key=payload.schedule_key)
        db.add(schedule)

    schedule.task_kind = payload.task_kind.value
    schedule.interval_minutes = payload.interval_minutes
    schedule.enabled = payload.enabled
    schedule.metadata_json = payload.metadata_json
    if schedule.last_scheduled_at is None and schedule.next_due_at is None:
        schedule.next_due_at = payload.start_at or effective_now
    else:
        schedule.next_due_at = _next_due_after(
            start_at=schedule.last_scheduled_at
            or payload.start_at
            or schedule.next_due_at
            or effective_now,
            interval_minutes=payload.interval_minutes,
            after=effective_now,
        )
    db.flush()
    return _to_schedule_summary(schedule=schedule)


def plan_due_scheduled_work(
    *,
    db: Session,
    now: datetime | None = None,
) -> list[ScheduledWorkItemSummaryData]:
    """Plan due scheduled work and persist idempotent work items.

    Args:
        db: Database session.
        now: Evaluation timestamp.

    Returns:
        Newly created scheduled work items.
    """
    effective_now = now or datetime.now(UTC)
    schedules = (
        db.query(PipelineSchedule)
        .filter(PipelineSchedule.enabled.is_(True))
        .order_by(PipelineSchedule.schedule_key.asc())
        .all()
    )
    interval_schedules = [
        IntervalSchedule(
            key=schedule.schedule_key,
            task_kind=ScheduledTaskKind(schedule.task_kind),
            interval_minutes=schedule.interval_minutes,
            enabled=schedule.enabled,
            last_scheduled_at=schedule.last_scheduled_at,
            start_at=schedule.next_due_at
            or schedule.last_scheduled_at
            or schedule.created_at,
        )
        for schedule in schedules
    ]
    due_items = compute_due_work_items(
        schedules=interval_schedules,
        now=effective_now,
    )
    schedules_by_key = {schedule.schedule_key: schedule for schedule in schedules}
    created_items: list[ScheduledWorkItemModel] = []

    for due_item in due_items:
        existing = (
            db.query(ScheduledWorkItemModel)
            .filter(ScheduledWorkItemModel.work_key == due_item.work_key)
            .first()
        )
        schedule = schedules_by_key[due_item.schedule_key]
        schedule.last_scheduled_at = due_item.due_at
        schedule.next_due_at = due_item.due_at + timedelta(
            minutes=due_item.interval_minutes,
        )
        if existing is not None:
            continue

        work_item = ScheduledWorkItemModel(
            schedule_id=schedule.id,
            schedule_key=schedule.schedule_key,
            work_key=due_item.work_key,
            task_kind=due_item.task_kind.value,
            status=ScheduledWorkStatus.PENDING.value,
            due_at=due_item.due_at,
            interval_minutes=due_item.interval_minutes,
            metadata_json=schedule.metadata_json,
        )
        db.add(work_item)
        created_items.append(work_item)

    db.flush()
    return [_to_work_item_summary(work_item=item) for item in created_items]


def list_pipeline_schedules(
    *,
    db: Session,
) -> list[PipelineScheduleSummaryData]:
    """List pipeline schedules for ops views.

    Args:
        db: Database session.

    Returns:
        Schedule summaries ordered by key.
    """
    schedules = (
        db.query(PipelineSchedule).order_by(PipelineSchedule.schedule_key.asc()).all()
    )
    return [_to_schedule_summary(schedule=schedule) for schedule in schedules]


def list_scheduled_work_items(
    *,
    db: Session,
    limit: int = 50,
    status: ScheduledWorkStatus | None = None,
) -> list[ScheduledWorkItemSummaryData]:
    """List scheduled work items for ops views.

    Args:
        db: Database session.
        limit: Maximum number of work items to return.
        status: Optional status filter.

    Returns:
        Scheduled work item summaries ordered by recency.
    """
    query = db.query(ScheduledWorkItemModel)
    if status is not None:
        query = query.filter(ScheduledWorkItemModel.status == status.value)

    items = (
        query.order_by(
            ScheduledWorkItemModel.due_at.desc(),
            ScheduledWorkItemModel.id.desc(),
        )
        .limit(limit)
        .all()
    )
    return [_to_work_item_summary(work_item=item) for item in items]


def _next_due_after(
    *,
    start_at: datetime,
    interval_minutes: int,
    after: datetime,
) -> datetime:
    """Calculate the next due timestamp after a reference time."""
    if start_at > after:
        return start_at
    interval = timedelta(minutes=interval_minutes)
    elapsed_intervals = ((after - start_at) // interval) + 1
    return start_at + (elapsed_intervals * interval)


def _to_schedule_summary(
    *,
    schedule: PipelineSchedule,
) -> PipelineScheduleSummaryData:
    """Convert a schedule model into a summary."""
    return PipelineScheduleSummaryData(
        id=schedule.id,
        schedule_key=schedule.schedule_key,
        task_kind=ScheduledTaskKind(schedule.task_kind),
        interval_minutes=schedule.interval_minutes,
        enabled=schedule.enabled,
        metadata_json=schedule.metadata_json,
        last_scheduled_at=schedule.last_scheduled_at,
        next_due_at=schedule.next_due_at,
        created_at=schedule.created_at,
        updated_at=schedule.updated_at,
    )


def _to_work_item_summary(
    *,
    work_item: ScheduledWorkItemModel,
) -> ScheduledWorkItemSummaryData:
    """Convert a scheduled work item model into a summary."""
    return ScheduledWorkItemSummaryData(
        id=work_item.id,
        schedule_id=work_item.schedule_id,
        ingestion_run_id=work_item.ingestion_run_id,
        schedule_key=work_item.schedule_key,
        work_key=work_item.work_key,
        task_kind=ScheduledTaskKind(work_item.task_kind),
        status=ScheduledWorkStatus(work_item.status),
        due_at=work_item.due_at,
        interval_minutes=work_item.interval_minutes,
        metadata_json=work_item.metadata_json,
        error_message=work_item.error_message,
        created_at=work_item.created_at,
        started_at=work_item.started_at,
        completed_at=work_item.completed_at,
    )

"""Pure interval scheduler planning services."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum, auto

SCHEDULER_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class ScheduledTaskKind(StrEnum):
    """Kinds of work the scheduler can plan."""

    INGESTION = auto()
    REINDEX = auto()
    DIGEST = auto()
    RETENTION = auto()


@dataclass(frozen=True, slots=True)
class IntervalSchedule:
    """Definition for a recurring interval schedule.

    Args:
        key: Stable schedule identifier used for idempotency.
        task_kind: Kind of task to plan when the schedule is due.
        interval_minutes: Interval cadence in whole minutes.
        enabled: Whether the schedule should produce work.
        last_scheduled_at: Most recent interval that was planned or enqueued.
        start_at: Anchor time used to calculate interval boundaries.

    Raises:
        ValueError: If the key is blank or the interval is invalid.
    """

    key: str
    task_kind: ScheduledTaskKind
    interval_minutes: int
    enabled: bool = True
    last_scheduled_at: datetime | None = None
    start_at: datetime = SCHEDULER_EPOCH

    def __post_init__(self) -> None:
        """Validate the schedule definition."""
        if not self.key.strip():
            raise ValueError("Schedule key must not be blank")
        if self.interval_minutes <= 0:
            raise ValueError("Schedule interval_minutes must be positive")


@dataclass(frozen=True, slots=True)
class ScheduledWorkItem:
    """A planned unit of scheduled work.

    Args:
        schedule_key: Stable identifier of the schedule that produced the item.
        work_key: Stable idempotency key for this schedule interval.
        task_kind: Kind of task to run.
        due_at: Interval boundary that made the work due.
        interval_minutes: Interval cadence from the originating schedule.
    """

    schedule_key: str
    work_key: str
    task_kind: ScheduledTaskKind
    due_at: datetime
    interval_minutes: int


def compute_due_work_items(
    *,
    schedules: Iterable[IntervalSchedule],
    now: datetime | None = None,
) -> list[ScheduledWorkItem]:
    """Compute scheduled work items due at a deterministic point in time.

    Args:
        schedules: Schedule definitions to evaluate.
        now: Time to evaluate against. Uses the current UTC time when omitted.

    Returns:
        Due work items in the same order as the input schedules.
    """
    effective_now = _as_utc(now or datetime.now(UTC))
    work_items: list[ScheduledWorkItem] = []

    for schedule in schedules:
        due_at = _due_interval_start(
            schedule=schedule,
            now=effective_now,
        )
        if due_at is None:
            continue
        if schedule.last_scheduled_at is not None:
            last_scheduled_at = _as_utc(schedule.last_scheduled_at)
            if last_scheduled_at >= due_at:
                continue

        work_items.append(
            ScheduledWorkItem(
                schedule_key=schedule.key,
                work_key=_build_work_key(
                    schedule_key=schedule.key,
                    task_kind=schedule.task_kind,
                    due_at=due_at,
                ),
                task_kind=schedule.task_kind,
                due_at=due_at,
                interval_minutes=schedule.interval_minutes,
            )
        )

    return work_items


def _due_interval_start(
    *,
    schedule: IntervalSchedule,
    now: datetime,
) -> datetime | None:
    """Return the current due interval boundary for a schedule."""
    if not schedule.enabled:
        return None

    start_at = _as_utc(schedule.start_at)
    if now < start_at:
        return None

    interval = timedelta(minutes=schedule.interval_minutes)
    elapsed_intervals = (now - start_at) // interval
    return start_at + (elapsed_intervals * interval)


def _build_work_key(
    *,
    schedule_key: str,
    task_kind: ScheduledTaskKind,
    due_at: datetime,
) -> str:
    """Build a stable idempotency key for one schedule interval."""
    return f"{schedule_key}:{task_kind.value}:{due_at.isoformat()}"


def _as_utc(value: datetime) -> datetime:
    """Normalize aware and naive datetimes to UTC."""
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)

"""Tests for pure scheduler planning services."""

from datetime import UTC, datetime

import pytest
from assertpy import assert_that

from podex.services.scheduler import (
    IntervalSchedule,
    ScheduledTaskKind,
    ScheduledWorkItem,
    compute_due_work_items,
)


@pytest.mark.parametrize(
    "task_kind",
    [
        ScheduledTaskKind.INGESTION,
        ScheduledTaskKind.REINDEX,
        ScheduledTaskKind.DIGEST,
    ],
)
def test_compute_due_work_items_supports_core_task_kinds(
    task_kind: ScheduledTaskKind,
) -> None:
    """Verify all phase-two task kinds can produce interval work."""
    now = datetime(2026, 5, 10, 12, 34, tzinfo=UTC)
    schedule = IntervalSchedule(
        key=f"{task_kind.value}-main",
        task_kind=task_kind,
        interval_minutes=15,
    )

    work_items = compute_due_work_items(
        schedules=[schedule],
        now=now,
    )

    assert_that(work_items).is_length(1)
    item = work_items[0]
    assert_that(item.schedule_key).is_equal_to(f"{task_kind.value}-main")
    assert_that(item.task_kind).is_equal_to(task_kind)
    assert_that(item.due_at).is_equal_to(datetime(2026, 5, 10, 12, 30, tzinfo=UTC))
    assert_that(item.work_key).is_equal_to(
        f"{task_kind.value}-main:{task_kind.value}:2026-05-10T12:30:00+00:00"
    )


def test_compute_due_work_items_skips_disabled_schedules() -> None:
    """Verify disabled schedules do not produce planned work."""
    schedule = IntervalSchedule(
        key="digest-nightly",
        task_kind=ScheduledTaskKind.DIGEST,
        interval_minutes=60,
        enabled=False,
    )

    work_items = compute_due_work_items(
        schedules=[schedule],
        now=datetime(2026, 5, 10, 12, 0, tzinfo=UTC),
    )

    assert_that(work_items).is_empty()


def test_compute_due_work_items_waits_for_next_interval() -> None:
    """Verify completed intervals are not planned again before the next boundary."""
    start_at = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)
    schedule = IntervalSchedule(
        key="ingestion-hourly",
        task_kind=ScheduledTaskKind.INGESTION,
        interval_minutes=30,
        last_scheduled_at=start_at,
        start_at=start_at,
    )

    early_items = compute_due_work_items(
        schedules=[schedule],
        now=datetime(2026, 5, 10, 12, 29, tzinfo=UTC),
    )
    due_items = compute_due_work_items(
        schedules=[schedule],
        now=datetime(2026, 5, 10, 12, 30, tzinfo=UTC),
    )

    assert_that(early_items).is_empty()
    assert_that(due_items).contains_only(
        ScheduledWorkItem(
            schedule_key="ingestion-hourly",
            work_key="ingestion-hourly:ingestion:2026-05-10T12:30:00+00:00",
            task_kind=ScheduledTaskKind.INGESTION,
            due_at=datetime(2026, 5, 10, 12, 30, tzinfo=UTC),
            interval_minutes=30,
        )
    )


def test_compute_due_work_items_uses_stable_idempotency_keys() -> None:
    """Verify repeated planning for the same interval returns stable work keys."""
    now = datetime(2026, 5, 10, 16, 3, tzinfo=UTC)
    schedule = IntervalSchedule(
        key="search-reindex",
        task_kind=ScheduledTaskKind.REINDEX,
        interval_minutes=20,
    )

    first_items = compute_due_work_items(
        schedules=[schedule],
        now=now,
    )
    second_items = compute_due_work_items(
        schedules=[schedule],
        now=now,
    )

    assert_that(second_items).is_equal_to(first_items)
    assert_that(first_items[0].work_key).is_equal_to(
        "search-reindex:reindex:2026-05-10T16:00:00+00:00"
    )


def test_compute_due_work_items_treats_naive_datetimes_as_utc() -> None:
    """Verify naive inputs are normalized to UTC for deterministic outputs."""
    start_at = datetime(2026, 5, 10, 8, 0)
    schedule = IntervalSchedule(
        key="digest-summary",
        task_kind=ScheduledTaskKind.DIGEST,
        interval_minutes=45,
        start_at=start_at,
    )

    work_items = compute_due_work_items(
        schedules=[schedule],
        now=datetime(2026, 5, 10, 10, 1),
    )

    assert_that(work_items).is_length(1)
    assert_that(work_items[0].due_at).is_equal_to(
        datetime(2026, 5, 10, 9, 30, tzinfo=UTC)
    )


@pytest.mark.parametrize(
    "key,interval_minutes",
    [
        ("", 15),
        ("   ", 15),
        ("valid", 0),
        ("valid", -5),
    ],
)
def test_interval_schedule_validates_required_fields(
    key: str,
    interval_minutes: int,
) -> None:
    """Verify invalid schedule definitions fail before planning."""
    with pytest.raises(ValueError):
        IntervalSchedule(
            key=key,
            task_kind=ScheduledTaskKind.INGESTION,
            interval_minutes=interval_minutes,
        )

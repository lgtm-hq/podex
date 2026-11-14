"""Regression tests for persistent schedule reconciliation and due boundaries."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from assertpy import assert_that
from sqlalchemy.orm import Session

from podex.services.scheduled_work import (
    PipelineScheduleInputData,
    plan_due_scheduled_work,
    upsert_pipeline_schedule,
)
from podex.services.scheduler import ScheduledTaskKind

_NOW = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)


def _as_utc(value: datetime | None) -> datetime | None:
    """Attach UTC to naive datetimes, which SQLite returns for stored UTC values."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


@pytest.fixture(name="schedule_payload")
def schedule_payload_fixture() -> PipelineScheduleInputData:
    """Provide an hourly schedule with an explicit initial boundary."""
    return PipelineScheduleInputData(
        schedule_key="reconciliation-test",
        task_kind=ScheduledTaskKind.INGESTION,
        interval_minutes=60,
        start_at=_NOW,
    )


@pytest.mark.parametrize("start_offset", [-60, 0, 60], ids=["overdue", "due", "future"])
def test_reconcile_preserves_unplanned_start(
    db_session: Session,
    schedule_payload: PipelineScheduleInputData,
    start_offset: int,
) -> None:
    """Repeated reconciliation cannot postpone an unplanned first boundary."""
    start_at = _NOW + timedelta(minutes=start_offset)
    upsert_pipeline_schedule(
        db=db_session,
        payload=replace(schedule_payload, start_at=start_at),
        now=_NOW,
    )
    db_session.commit()

    reconciled = upsert_pipeline_schedule(
        db=db_session,
        payload=replace(schedule_payload, metadata_json={"updated": True}),
        now=_NOW,
    )

    assert_that(_as_utc(reconciled.next_due_at)).is_equal_to(start_at)
    assert_that(reconciled.metadata_json).is_equal_to({"updated": True})
    planned = plan_due_scheduled_work(db=db_session, now=_NOW)
    assert_that(planned).is_length(0 if start_offset > 0 else 1)
    assert_that(plan_due_scheduled_work(db=db_session, now=_NOW)).is_empty()


@pytest.mark.parametrize("interval_minutes", [30, 120], ids=["shorter", "longer"])
def test_cadence_change_keeps_missed_boundary_due(
    db_session: Session,
    schedule_payload: PipelineScheduleInputData,
    interval_minutes: int,
) -> None:
    """A cadence change applied late plans one item for the latest boundary."""
    upsert_pipeline_schedule(db=db_session, payload=schedule_payload, now=_NOW)
    first = plan_due_scheduled_work(db=db_session, now=_NOW)
    db_session.commit()
    changed_at = _NOW + timedelta(minutes=interval_minutes * 2)
    payload = replace(schedule_payload, interval_minutes=interval_minutes)
    changed = upsert_pipeline_schedule(db=db_session, payload=payload, now=changed_at)

    assert_that(_as_utc(changed.next_due_at)).is_equal_to(
        _NOW + timedelta(minutes=interval_minutes),
    )
    missed = plan_due_scheduled_work(db=db_session, now=changed_at)
    assert_that(missed).is_length(1)
    assert_that(_as_utc(missed[0].due_at)).is_equal_to(changed_at)
    assert_that(missed[0].work_key).is_not_equal_to(first[0].work_key)
    assert_that(plan_due_scheduled_work(db=db_session, now=changed_at)).is_empty()
    db_session.commit()

    next_due = changed_at + timedelta(minutes=interval_minutes)
    upsert_pipeline_schedule(db=db_session, payload=payload, now=next_due)
    second = plan_due_scheduled_work(db=db_session, now=next_due)

    assert_that(second).is_length(1)
    assert_that(_as_utc(second[0].due_at)).is_equal_to(next_due)
    assert_that(second[0].interval_minutes).is_equal_to(interval_minutes)


def test_cadence_change_before_boundary_plans_nothing_early(
    db_session: Session,
    schedule_payload: PipelineScheduleInputData,
) -> None:
    """A cadence change before the new boundary creates no work until it is due."""
    upsert_pipeline_schedule(db=db_session, payload=schedule_payload, now=_NOW)
    plan_due_scheduled_work(db=db_session, now=_NOW)
    db_session.commit()
    changed_at = _NOW + timedelta(minutes=15)
    payload = replace(schedule_payload, interval_minutes=30)
    changed = upsert_pipeline_schedule(db=db_session, payload=payload, now=changed_at)

    next_due = _NOW + timedelta(minutes=30)
    assert_that(_as_utc(changed.next_due_at)).is_equal_to(next_due)
    assert_that(plan_due_scheduled_work(db=db_session, now=changed_at)).is_empty()
    assert_that(plan_due_scheduled_work(db=db_session, now=next_due)).is_length(1)


@pytest.mark.parametrize("plan_first", [False, True], ids=["unplanned", "planned"])
def test_disable_and_reenable_skip_disabled_intervals(
    db_session: Session,
    schedule_payload: PipelineScheduleInputData,
    plan_first: bool,
) -> None:
    """Re-enabling resumes at a future boundary without disabled-interval work."""
    upsert_pipeline_schedule(db=db_session, payload=schedule_payload, now=_NOW)
    if plan_first:
        plan_due_scheduled_work(db=db_session, now=_NOW)
    db_session.commit()
    disabled_payload = replace(schedule_payload, enabled=False)
    disabled_at = _NOW + timedelta(minutes=15)
    disabled = upsert_pipeline_schedule(
        db=db_session,
        payload=disabled_payload,
        now=disabled_at,
    )
    assert_that(disabled.next_due_at).is_equal_to(_NOW + timedelta(hours=1))
    db_session.commit()
    reenabled_at = _NOW + timedelta(hours=3)
    upsert_pipeline_schedule(
        db=db_session,
        payload=disabled_payload,
        now=reenabled_at,
    )
    assert_that(plan_due_scheduled_work(db=db_session, now=reenabled_at)).is_empty()

    reenabled = upsert_pipeline_schedule(
        db=db_session,
        payload=schedule_payload,
        now=reenabled_at,
    )
    next_due = _NOW + timedelta(hours=4)
    assert_that(reenabled.next_due_at).is_equal_to(next_due)
    assert_that(plan_due_scheduled_work(db=db_session, now=reenabled_at)).is_empty()
    db_session.commit()

    upsert_pipeline_schedule(db=db_session, payload=schedule_payload, now=next_due)
    resumed = plan_due_scheduled_work(db=db_session, now=next_due)
    assert_that(resumed).is_length(1)
    assert_that(resumed[0].due_at).is_equal_to(next_due)


def test_cadence_change_before_first_plan_keeps_outstanding_boundary(
    db_session: Session,
    schedule_payload: PipelineScheduleInputData,
) -> None:
    """A cadence change on a never-planned schedule cannot postpone due work."""
    upsert_pipeline_schedule(db=db_session, payload=schedule_payload, now=_NOW)
    db_session.commit()
    changed_at = _NOW + timedelta(minutes=10)
    changed = upsert_pipeline_schedule(
        db=db_session,
        payload=replace(schedule_payload, interval_minutes=30, start_at=changed_at),
        now=changed_at,
    )

    assert_that(_as_utc(changed.next_due_at)).is_equal_to(_NOW)
    planned = plan_due_scheduled_work(db=db_session, now=changed_at)
    assert_that(planned).is_length(1)
    assert_that(_as_utc(planned[0].due_at)).is_equal_to(_NOW)

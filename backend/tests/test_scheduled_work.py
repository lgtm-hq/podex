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

    assert_that(reconciled.next_due_at).is_equal_to(start_at.replace(tzinfo=None))
    assert_that(reconciled.metadata_json).is_equal_to({"updated": True})
    planned = plan_due_scheduled_work(db=db_session, now=_NOW)
    assert_that(planned).is_length(0 if start_offset > 0 else 1)
    assert_that(plan_due_scheduled_work(db=db_session, now=_NOW)).is_empty()


@pytest.mark.parametrize("interval_minutes", [30, 120], ids=["shorter", "longer"])
def test_cadence_change_resets_next_due_boundary(
    db_session: Session,
    schedule_payload: PipelineScheduleInputData,
    interval_minutes: int,
) -> None:
    """Cadence changes use the last planned boundary and skip past intervals."""
    upsert_pipeline_schedule(db=db_session, payload=schedule_payload, now=_NOW)
    first = plan_due_scheduled_work(db=db_session, now=_NOW)
    db_session.commit()
    changed_at = _NOW + timedelta(minutes=interval_minutes * 2)
    payload = replace(schedule_payload, interval_minutes=interval_minutes)
    changed = upsert_pipeline_schedule(db=db_session, payload=payload, now=changed_at)
    next_due = changed_at + timedelta(minutes=interval_minutes)

    assert_that(changed.next_due_at).is_equal_to(next_due)
    assert_that(plan_due_scheduled_work(db=db_session, now=changed_at)).is_empty()
    db_session.commit()

    upsert_pipeline_schedule(db=db_session, payload=payload, now=next_due)
    second = plan_due_scheduled_work(db=db_session, now=next_due)

    assert_that(second).is_length(1)
    assert_that(second[0].due_at).is_equal_to(next_due)
    assert_that(second[0].interval_minutes).is_equal_to(interval_minutes)
    assert_that(second[0].work_key).is_not_equal_to(first[0].work_key)


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


def test_cadence_change_before_first_plan_uses_explicit_start(
    db_session: Session,
    schedule_payload: PipelineScheduleInputData,
) -> None:
    """An explicit start anchors a cadence reset before any work was planned."""
    upsert_pipeline_schedule(db=db_session, payload=schedule_payload, now=_NOW)
    db_session.commit()
    start_at = _NOW + timedelta(minutes=45)
    changed = upsert_pipeline_schedule(
        db=db_session,
        payload=replace(schedule_payload, interval_minutes=30, start_at=start_at),
        now=_NOW + timedelta(minutes=15),
    )

    assert_that(changed.next_due_at).is_equal_to(start_at)
    assert_that(plan_due_scheduled_work(db=db_session, now=_NOW)).is_empty()
    assert_that(plan_due_scheduled_work(db=db_session, now=start_at)).is_length(1)

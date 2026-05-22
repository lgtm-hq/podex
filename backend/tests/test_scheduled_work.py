"""Tests for DB-backed scheduled work services."""

from datetime import UTC, datetime, timedelta

from assertpy import assert_that
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from podex.models import PipelineSchedule, ScheduledWorkItemModel, ScheduledWorkStatus
from podex.services.scheduled_work import (
    PipelineScheduleInputData,
    list_pipeline_schedules,
    plan_due_scheduled_work,
    upsert_pipeline_schedule,
)
from podex.services.scheduler import ScheduledTaskKind


def test_plan_due_scheduled_work_persists_idempotent_items(
    db_session: Session,
) -> None:
    """Verify due schedules create one stable work item per interval."""
    now = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)
    upsert_pipeline_schedule(
        db=db_session,
        payload=PipelineScheduleInputData(
            schedule_key="ingestion:main",
            task_kind=ScheduledTaskKind.INGESTION,
            interval_minutes=60,
            start_at=now,
        ),
        now=now,
    )
    db_session.commit()

    first_items = plan_due_scheduled_work(db=db_session, now=now)
    second_items = plan_due_scheduled_work(db=db_session, now=now)
    db_session.commit()

    schedule = db_session.query(PipelineSchedule).one()
    work_item = db_session.query(ScheduledWorkItemModel).one()
    assert_that(first_items).is_length(1)
    assert_that(second_items).is_empty()
    assert_that(work_item.work_key).is_equal_to(
        "ingestion:main:ingestion:2026-05-10T12:00:00+00:00",
    )
    assert_that(schedule.last_scheduled_at).is_equal_to(now.replace(tzinfo=None))
    assert_that(schedule.next_due_at).is_equal_to(
        (now + timedelta(hours=1)).replace(tzinfo=None),
    )


def test_list_pipeline_schedules_exposes_ops_state(db_session: Session) -> None:
    """Verify schedules can be listed for ops views."""
    now = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)
    upsert_pipeline_schedule(
        db=db_session,
        payload=PipelineScheduleInputData(
            schedule_key="digest:daily",
            task_kind=ScheduledTaskKind.DIGEST,
            interval_minutes=1440,
            metadata_json={"scope": "daily"},
            start_at=now,
        ),
        now=now,
    )
    db_session.commit()

    schedules = list_pipeline_schedules(db=db_session)

    assert_that(schedules).is_length(1)
    assert_that(schedules[0].schedule_key).is_equal_to("digest:daily")
    assert_that(schedules[0].task_kind).is_equal_to(ScheduledTaskKind.DIGEST)
    assert_that(schedules[0].metadata_json).is_equal_to({"scope": "daily"})


def test_ops_scheduled_work_endpoint_lists_schedules_and_work(
    client: TestClient,
    db_session: Session,
) -> None:
    """Verify v2 ops scheduled-work endpoint exposes schedules and work items."""
    now = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)
    upsert_pipeline_schedule(
        db=db_session,
        payload=PipelineScheduleInputData(
            schedule_key="reindex:hourly",
            task_kind=ScheduledTaskKind.REINDEX,
            interval_minutes=60,
            start_at=now,
        ),
        now=now,
    )
    plan_due_scheduled_work(db=db_session, now=now)
    db_session.commit()

    response = client.get(
        f"/api/v2/ops/scheduled-work?status={ScheduledWorkStatus.PENDING.value}",
    )

    assert_that(response.status_code).is_equal_to(200)
    data = response.json()
    assert_that(data["schedules"]).is_length(1)
    assert_that(data["schedules"][0]["id"]).starts_with("sched_")
    assert_that(data["work_items"]).is_length(1)
    assert_that(data["work_items"][0]["id"]).starts_with("work_")

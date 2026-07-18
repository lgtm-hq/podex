"""Tests for the ingestion run model."""

from datetime import datetime

from assertpy import assert_that
from sqlalchemy import select
from sqlalchemy.orm import Session

from podex.models import IngestionRun, IngestionRunStatus


def test_create_ingestion_run(db_session: Session) -> None:
    """A completed run round-trips its status, timestamps, and summary."""
    started_at = datetime(2026, 2, 3)
    completed_at = datetime(2026, 2, 3, 1)

    run = IngestionRun(
        status=IngestionRunStatus.COMPLETED,
        error_summary=None,
        started_at=started_at,
        completed_at=completed_at,
    )
    db_session.add(run)
    db_session.commit()

    stored = db_session.execute(select(IngestionRun)).scalar_one()
    assert_that(stored.status).is_equal_to(IngestionRunStatus.COMPLETED)
    assert_that(stored.error_summary).is_none()
    assert_that(stored.started_at).is_equal_to(started_at)
    assert_that(stored.completed_at).is_equal_to(completed_at)
    assert_that(stored.created_at).is_not_none()


def test_failed_run_records_error_summary(db_session: Session) -> None:
    """A failed run keeps its error summary and stays open-ended."""
    run = IngestionRun(
        status=IngestionRunStatus.FAILED,
        error_summary="feed fetch timed out",
    )
    db_session.add(run)
    db_session.commit()

    stored = db_session.execute(select(IngestionRun)).scalar_one()
    assert_that(stored.status).is_equal_to(IngestionRunStatus.FAILED)
    assert_that(stored.error_summary).is_equal_to("feed fetch timed out")
    assert_that(stored.completed_at).is_none()

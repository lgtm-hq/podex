"""Tests for ingestion run model."""

from datetime import datetime

from assertpy import assert_that
from sqlalchemy.orm import Session

from podex.models import IngestionRun


def test_create_ingestion_run(db_session: Session) -> None:
    started_at = datetime(2026, 2, 3)
    completed_at = datetime(2026, 2, 3, 1)

    run = IngestionRun(
        status="completed",
        error_summary=None,
        started_at=started_at,
        completed_at=completed_at,
    )
    db_session.add(run)
    db_session.commit()

    stored = db_session.query(IngestionRun).first()
    assert_that(stored).is_not_none()
    assert stored is not None
    assert_that(stored.status).is_equal_to("completed")
    assert_that(stored.error_summary).is_none()
    assert_that(stored.started_at).is_equal_to(started_at)
    assert_that(stored.completed_at).is_equal_to(completed_at)

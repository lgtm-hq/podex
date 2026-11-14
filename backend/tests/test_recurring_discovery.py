"""Tests for recurring episode discovery scheduling."""

from datetime import UTC, datetime

from assertpy import assert_that
from sqlalchemy.orm import Session

from podex.models import (
    IngestionRun,
    IngestionRunStatus,
    Podcast,
    ScheduledWorkItemModel,
)
from podex.services.discovery import DiscoveryResult
from podex.services.recurring_discovery import (
    plan_recurring_discovery_work,
    reconcile_recurring_discovery_schedules,
    run_due_episode_discovery_work,
)


def test_reconcile_recurring_discovery_schedules_only_tracks_active_podcasts(
    db_session: Session,
) -> None:
    """Verify recurring discovery schedules are created for active podcasts."""
    active = Podcast(name="Active", slug="active", status="active")
    paused = Podcast(name="Paused", slug="paused", status="paused")
    db_session.add_all([active, paused])
    db_session.commit()

    result = reconcile_recurring_discovery_schedules(
        db=db_session,
        interval_minutes=60,
        now=datetime(2026, 5, 10, 12, 0, tzinfo=UTC),
    )
    db_session.commit()

    assert_that(result.schedules).is_length(1)
    assert_that(result.schedules[0].schedule_key).is_equal_to(
        f"discovery:podcast:{active.id}",
    )
    assert_that(result.schedules[0].metadata_json).contains_entry(
        {"podcast_slug": "active"},
    )


def test_plan_recurring_discovery_work_creates_due_work(
    db_session: Session,
) -> None:
    """Verify recurring discovery planning persists due scheduled work."""
    podcast = Podcast(name="Active", slug="active", status="active")
    db_session.add(podcast)
    db_session.commit()

    work_items = plan_recurring_discovery_work(
        db=db_session,
        interval_minutes=60,
        now=datetime(2026, 5, 10, 12, 0, tzinfo=UTC),
    )
    db_session.commit()

    assert_that(work_items).is_length(1)
    assert_that(work_items[0].metadata_json).contains_entry(
        {"kind": "episode_discovery"}
    )
    assert_that(db_session.query(ScheduledWorkItemModel).count()).is_equal_to(1)


def test_run_due_episode_discovery_work_uses_runner_and_ingestion_run(
    db_session: Session,
) -> None:
    """Verify pending discovery work runs through an injected runner."""
    podcast = Podcast(name="Active", slug="active", status="active")
    db_session.add(podcast)
    db_session.commit()
    plan_recurring_discovery_work(
        db=db_session,
        interval_minutes=60,
        now=datetime(2026, 5, 10, 12, 0, tzinfo=UTC),
    )
    db_session.commit()

    def runner(_db: Session, run_podcast: Podcast) -> DiscoveryResult:
        assert_that(run_podcast.id).is_equal_to(podcast.id)
        return DiscoveryResult(
            new_episodes=2,
            updated_episodes=1,
            total_discovered=3,
        )

    results = run_due_episode_discovery_work(db=db_session, runner=runner)
    db_session.commit()

    work_item = db_session.query(ScheduledWorkItemModel).one()
    run = db_session.query(IngestionRun).one()
    assert_that(results).is_length(1)
    assert_that(results[0].new_episodes).is_equal_to(2)
    assert_that(work_item.status).is_equal_to("completed")
    assert_that(work_item.ingestion_run_id).is_equal_to(run.id)
    assert_that(run.status).is_equal_to("completed")


def _plan_discovery_for_active_podcast(db: Session) -> Podcast:
    """Persist an active podcast with one committed, due discovery work item."""
    podcast = Podcast(name="Active", slug="active", status="active")
    db.add(podcast)
    db.commit()
    plan_recurring_discovery_work(
        db=db,
        interval_minutes=60,
        now=datetime(2026, 5, 10, 12, 0, tzinfo=UTC),
    )
    db.commit()
    return podcast


def _load_run_statuses(db: Session) -> list[IngestionRunStatus]:
    """Load every ingestion run from a fresh session and return its status."""
    with Session(bind=db.get_bind()) as reader:
        return [run.status for run in reader.query(IngestionRun).all()]


def test_discovery_run_status_is_loadable_mid_run(db_session: Session) -> None:
    """A run committed mid-discovery holds a status the ORM can load."""
    _plan_discovery_for_active_podcast(db_session)
    observed: list[IngestionRunStatus] = []

    def committing_runner(db: Session, _podcast: Podcast) -> DiscoveryResult:
        # The discovery orchestrator commits while upserting episodes.
        db.commit()
        observed.extend(_load_run_statuses(db))
        return DiscoveryResult(new_episodes=0, updated_episodes=0, total_discovered=0)

    run_due_episode_discovery_work(db=db_session, runner=committing_runner)
    db_session.commit()

    # A load failure inside the runner is recorded as the run's error.
    run = db_session.query(IngestionRun).one()
    assert_that(run.error_summary).is_none()
    assert_that(run.status).is_equal_to(IngestionRunStatus.COMPLETED)
    assert_that(observed).is_equal_to([IngestionRunStatus.RUNNING])


def test_failed_tick_leaves_loadable_run(db_session: Session) -> None:
    """A tick that fails after discovery committed leaves a loadable run."""
    _plan_discovery_for_active_podcast(db_session)

    def committing_runner(db: Session, _podcast: Podcast) -> DiscoveryResult:
        db.commit()
        return DiscoveryResult(new_episodes=0, updated_episodes=0, total_discovered=0)

    run_due_episode_discovery_work(db=db_session, runner=committing_runner)
    # A later tick step fails, so the scheduler rolls back the final commit.
    db_session.rollback()

    # The rolled-back tick never records an outcome, so the run stays running.
    assert_that(_load_run_statuses(db_session)).is_equal_to(
        [IngestionRunStatus.RUNNING],
    )


def test_runner_exception_records_loadable_failed_run(db_session: Session) -> None:
    """A runner that raises leaves a failed run that a fresh session can load."""
    _plan_discovery_for_active_podcast(db_session)

    def raising_runner(_db: Session, _podcast: Podcast) -> DiscoveryResult:
        raise RuntimeError("provider unavailable")

    run_due_episode_discovery_work(db=db_session, runner=raising_runner)
    db_session.commit()

    run = db_session.query(IngestionRun).one()
    assert_that(run.error_summary).is_equal_to("provider unavailable")
    assert_that(_load_run_statuses(db_session)).is_equal_to(
        [IngestionRunStatus.FAILED],
    )


def test_reported_errors_record_loadable_failed_run(db_session: Session) -> None:
    """A result that reports errors leaves a failed run that can be loaded."""
    _plan_discovery_for_active_podcast(db_session)

    def failing_runner(_db: Session, _podcast: Podcast) -> DiscoveryResult:
        return DiscoveryResult(errors=["feed timed out", "bad episode"])

    run_due_episode_discovery_work(db=db_session, runner=failing_runner)
    db_session.commit()

    run = db_session.query(IngestionRun).one()
    assert_that(run.error_summary).is_equal_to("feed timed out; bad episode")
    assert_that(_load_run_statuses(db_session)).is_equal_to(
        [IngestionRunStatus.FAILED],
    )

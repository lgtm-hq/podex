"""Tests for recurring episode discovery scheduling."""

from datetime import UTC, datetime

from assertpy import assert_that
from sqlalchemy.orm import Session

from podex.models import IngestionRun, Podcast, ScheduledWorkItemModel
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

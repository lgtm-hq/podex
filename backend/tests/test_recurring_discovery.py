"""Tests for recurring episode discovery scheduling."""

from datetime import UTC, datetime

import pytest
from assertpy import assert_that
from sqlalchemy.orm import Session

from podex.models import (
    IngestionRun,
    IngestionRunStatus,
    PipelineSchedule,
    Podcast,
    PodcastStatus,
    ScheduledWorkItemModel,
)
from podex.services.discovery import DiscoveryResult
from podex.services.recurring_discovery import (
    plan_recurring_discovery_work,
    reconcile_recurring_discovery_schedules,
    run_due_episode_discovery_work,
)
from podex.services.scheduled_work import plan_due_scheduled_work


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


def test_reconcile_preserves_next_due_boundary(db_session: Session) -> None:
    """Reconciliation keeps the next discovery boundary available to plan."""
    db_session.add(Podcast(name="Recurring", slug="recurring", status="active"))
    db_session.commit()

    first = plan_recurring_discovery_work(
        db=db_session,
        interval_minutes=60,
        now=datetime(2026, 5, 10, 12, 0, tzinfo=UTC),
    )
    db_session.commit()
    second = plan_recurring_discovery_work(
        db=db_session,
        interval_minutes=60,
        now=datetime(2026, 5, 10, 13, 0, tzinfo=UTC),
    )

    assert_that(first).is_length(1)
    assert_that(second).is_length(1)
    assert_that(second[0].work_key).is_not_equal_to(first[0].work_key)
    assert_that(db_session.query(ScheduledWorkItemModel).count()).is_equal_to(2)


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


def test_pause_disables_existing_discovery_schedule(db_session: Session) -> None:
    """Pausing disables the existing schedule and prevents later cadence work."""
    podcast = _plan_discovery_for_active_podcast(db_session)
    podcast.status = PodcastStatus.PAUSED
    db_session.commit()

    reconcile_recurring_discovery_schedules(
        db=db_session,
        interval_minutes=60,
        now=datetime(2026, 5, 10, 12, 15, tzinfo=UTC),
    )
    db_session.commit()

    assert_that(db_session.query(PipelineSchedule).one().enabled).is_false()
    assert_that(
        plan_recurring_discovery_work(
            db=db_session,
            interval_minutes=60,
            now=datetime(2026, 5, 10, 15, 0, tzinfo=UTC),
        ),
    ).is_empty()
    assert_that(db_session.query(ScheduledWorkItemModel).count()).is_equal_to(1)


@pytest.mark.parametrize("status", [PodcastStatus.PAUSED, PodcastStatus.WATCHLIST])
def test_queued_discovery_skips_paused_podcast(
    db_session: Session,
    status: PodcastStatus,
) -> None:
    """Already queued discovery skips a pause even before reconciliation runs."""
    podcast = _plan_discovery_for_active_podcast(db_session)
    podcast.status = status
    db_session.commit()
    calls: list[int] = []

    def runner(_db: Session, run_podcast: Podcast) -> DiscoveryResult:
        """Record every attempted provider execution."""
        calls.append(run_podcast.id)
        return DiscoveryResult()

    results = run_due_episode_discovery_work(db=db_session, runner=runner)
    db_session.commit()

    assert_that(calls).is_empty()
    assert_that(results).is_empty()
    item = db_session.query(ScheduledWorkItemModel).one()
    assert_that(item.status).is_equal_to("skipped")
    assert_that(item.error_message).is_equal_to("Podcast is not active")
    assert_that(item.completed_at).is_not_none()
    assert_that(item.started_at).is_none()
    assert_that(item.ingestion_run_id).is_none()
    assert_that(db_session.query(IngestionRun).count()).is_zero()


def _as_utc(value: datetime | None) -> datetime | None:
    """Normalize SQLite's naive UTC results while preserving aware timestamps."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


@pytest.mark.parametrize("resume_minute", [0, 30], ids=["on-boundary", "between"])
def test_resume_discards_queued_work_and_uses_future_boundary(
    db_session: Session,
    resume_minute: int,
) -> None:
    """Resuming cannot replay old work even when no executor ran during pause."""
    podcast = _plan_discovery_for_active_podcast(db_session)
    original = db_session.query(ScheduledWorkItemModel).one()
    original_id = original.id
    schedule_id = original.schedule_id
    podcast.status = PodcastStatus.PAUSED
    db_session.commit()
    reconcile_recurring_discovery_schedules(
        db=db_session,
        interval_minutes=60,
        now=datetime(2026, 5, 10, 12, 15, tzinfo=UTC),
    )
    db_session.commit()
    assert_that(original.status).is_equal_to("skipped")
    assert_that(original.completed_at).is_not_none()

    podcast.status = PodcastStatus.ACTIVE
    db_session.commit()
    resumed_at = datetime(2026, 5, 10, 15, resume_minute, tzinfo=UTC)
    assert_that(
        plan_recurring_discovery_work(
            db=db_session,
            interval_minutes=60,
            now=resumed_at,
        ),
    ).is_empty()
    db_session.commit()
    schedule = db_session.query(PipelineSchedule).one()
    next_due = datetime(2026, 5, 10, 16, tzinfo=UTC)
    assert_that(schedule.enabled).is_true()
    assert_that(schedule.id).is_equal_to(schedule_id)
    assert_that(_as_utc(schedule.next_due_at)).is_equal_to(next_due)
    calls: list[int] = []

    def runner(_db: Session, run_podcast: Podcast) -> DiscoveryResult:
        """Record executions to distinguish fresh work from stale replay."""
        calls.append(run_podcast.id)
        return DiscoveryResult()

    assert_that(
        run_due_episode_discovery_work(db=db_session, runner=runner),
    ).is_empty()
    fresh = plan_recurring_discovery_work(
        db=db_session,
        interval_minutes=60,
        now=next_due,
    )
    assert_that(fresh).is_length(1)
    assert_that(_as_utc(fresh[0].due_at)).is_equal_to(next_due)
    assert_that(fresh[0].id).is_not_equal_to(original_id)
    assert_that(
        plan_recurring_discovery_work(
            db=db_session,
            interval_minutes=60,
            now=next_due,
        ),
    ).is_empty()
    run_due_episode_discovery_work(db=db_session, runner=runner)
    db_session.commit()
    assert_that(calls).is_equal_to([podcast.id])
    assert_that(db_session.query(ScheduledWorkItemModel).count()).is_equal_to(2)


@pytest.mark.parametrize("reconcile", [False, True], ids=["queued", "reconciled"])
def test_deleted_podcast_never_runs_discovery(
    db_session: Session,
    reconcile: bool,
) -> None:
    """Deletion disables orphaned schedules and queued work never calls providers."""
    podcast = _plan_discovery_for_active_podcast(db_session)
    db_session.delete(podcast)
    db_session.commit()
    if reconcile:
        assert_that(
            plan_recurring_discovery_work(
                db=db_session,
                interval_minutes=60,
                now=datetime(2026, 5, 10, 15, 0, tzinfo=UTC),
            ),
        ).is_empty()
        assert_that(db_session.query(PipelineSchedule).one().enabled).is_false()

    calls: list[int] = []

    def runner(_db: Session, run_podcast: Podcast) -> DiscoveryResult:
        """Record any unexpected call for a deleted podcast."""
        calls.append(run_podcast.id)
        return DiscoveryResult()

    assert_that(
        run_due_episode_discovery_work(db=db_session, runner=runner),
    ).is_empty()
    db_session.commit()
    item = db_session.query(ScheduledWorkItemModel).one()
    assert_that(item.status).is_equal_to("skipped" if reconcile else "failed")
    assert_that(item.completed_at).is_not_none()
    assert_that(item.ingestion_run_id).is_none()
    assert_that(calls).is_empty()
    assert_that(db_session.query(IngestionRun).count()).is_zero()


def test_execution_rechecks_cached_podcast_status(db_session: Session) -> None:
    """A pause committed by another session overrides a cached active podcast."""
    podcast = _plan_discovery_for_active_podcast(db_session)
    podcast_id = podcast.id
    assert_that(podcast.status).is_equal_to(PodcastStatus.ACTIVE)
    with Session(bind=db_session.get_bind()) as writer:
        writer.query(Podcast).filter(Podcast.id == podcast_id).update(
            {Podcast.status: PodcastStatus.PAUSED},
        )
        writer.commit()

    calls: list[int] = []

    def runner(_db: Session, run_podcast: Podcast) -> DiscoveryResult:
        """Record provider calls made with stale eligibility."""
        calls.append(run_podcast.id)
        return DiscoveryResult()

    run_due_episode_discovery_work(db=db_session, runner=runner)
    db_session.commit()
    assert_that(calls).is_empty()
    assert_that(db_session.query(ScheduledWorkItemModel).one().status).is_equal_to(
        "skipped",
    )


@pytest.mark.parametrize("status", ["running", "completed"])
def test_pause_preserves_started_work_and_other_job_kinds(
    db_session: Session,
    status: str,
) -> None:
    """Reconciliation only cancels pending discovery, leaving other work alone."""
    podcast = _plan_discovery_for_active_podcast(db_session)
    discovery = db_session.query(ScheduledWorkItemModel).one()
    discovery.status = status
    podcast.status = PodcastStatus.PAUSED
    other_schedule = PipelineSchedule(
        schedule_key="other-ingestion",
        task_kind="ingestion",
        interval_minutes=60,
        enabled=True,
        metadata_json={"kind": "other"},
        next_due_at=datetime(2026, 5, 10, 12, 0, tzinfo=UTC),
    )
    db_session.add(other_schedule)
    db_session.commit()
    other_items = plan_due_scheduled_work(
        db=db_session,
        now=datetime(2026, 5, 10, 12, 0, tzinfo=UTC),
    )
    assert_that(other_items).is_length(1)
    db_session.commit()

    reconcile_recurring_discovery_schedules(
        db=db_session,
        now=datetime(2026, 5, 10, 12, 15, tzinfo=UTC),
    )
    assert_that(run_due_episode_discovery_work(db=db_session)).is_empty()
    db_session.commit()

    assert_that(discovery.status).is_equal_to(status)
    assert_that(other_schedule.enabled).is_true()
    other_work = (
        db_session.query(ScheduledWorkItemModel)
        .filter(ScheduledWorkItemModel.id == other_items[0].id)
        .one()
    )
    assert_that(other_work.status).is_equal_to("pending")


@pytest.mark.parametrize("runner_commits", [False, True], ids=["rollback", "commit"])
def test_pause_writes_follow_tick_transaction_boundaries(
    db_session: Session,
    runner_commits: bool,
) -> None:
    """Pause writes survive only a commit, and rollback retries still skip them."""
    paused = _plan_discovery_for_active_podcast(db_session)
    active = Podcast(name="Continuing", slug="continuing", status=PodcastStatus.ACTIVE)
    db_session.add(active)
    db_session.commit()
    plan_recurring_discovery_work(
        db=db_session,
        interval_minutes=60,
        now=datetime(2026, 5, 10, 12, 0, tzinfo=UTC),
    )
    paused.status = PodcastStatus.PAUSED
    db_session.commit()
    reconcile_recurring_discovery_schedules(
        db=db_session,
        interval_minutes=60,
        now=datetime(2026, 5, 10, 12, 15, tzinfo=UTC),
    )
    calls: list[int] = []

    def runner(db: Session, run_podcast: Podcast) -> DiscoveryResult:
        """Optionally reproduce the orchestrator's mid-tick commit."""
        calls.append(run_podcast.id)
        if runner_commits:
            db.commit()
        return DiscoveryResult()

    run_due_episode_discovery_work(db=db_session, runner=runner)
    db_session.rollback()
    item = (
        db_session.query(ScheduledWorkItemModel)
        .filter(ScheduledWorkItemModel.schedule_key == f"discovery:podcast:{paused.id}")
        .one()
    )
    assert_that(item.status).is_equal_to("skipped" if runner_commits else "pending")
    assert_that(item.schedule.enabled).is_equal_to(not runner_commits)
    run_due_episode_discovery_work(db=db_session, runner=runner)
    db_session.commit()
    assert_that(calls).does_not_contain(paused.id)
    assert_that(item.status).is_equal_to("skipped")


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

"""Tests for the scheduler runner, recurring work, and operational alerts."""

from datetime import UTC, datetime, timedelta

import pytest
from assertpy import assert_that
from sqlalchemy.orm import Session

from podex.config import Settings
from podex.models import (
    AccountPreference,
    AccountUser,
    Episode,
    PipelineSchedule,
    Podcast,
    ScheduledWorkItemModel,
    ScheduledWorkStatus,
    SweepCheckpoint,
    Transcript,
)
from podex.scheduler_runner import TickSummaryData, run_tick
from podex.services.account_follows import follow_podcast
from podex.services.discovery import DiscoveryResult
from podex.services.discovery_orchestrator import DiscoveryOrchestrator
from podex.services.operational_alerts import (
    OperationalAlertThresholdsData,
    evaluate_operational_alerts,
)
from podex.services.ops_metrics import get_operational_metrics
from podex.services.recurring_notifications import (
    DIGEST_SCHEDULE_KEY,
    RETENTION_SCHEDULE_KEY,
    reconcile_notification_schedules,
    run_due_digest_work,
    run_due_retention_work,
)
from podex.services.scheduled_work import plan_due_scheduled_work
from tests.conftest import create_podcast_alert_rule, seed_catalog_graph

_NOW = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)


# This adapter implements one protocol operation; extra methods have no purpose.
# pylint: disable-next=too-few-public-methods
class RecordingDigestSender:
    """In-memory digest delivery for scheduler tests."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    def send_digest(self, *, email: str, subject: str, body_text: str) -> None:
        """Record one delivered digest."""
        del subject, body_text
        self.sent.append(email)


def _plan_notification_work(db_session: Session) -> None:
    reconcile_notification_schedules(
        db=db_session,
        digest_interval_minutes=60,
        retention_interval_minutes=60,
        now=_NOW,
    )
    plan_due_scheduled_work(db=db_session, now=_NOW)


def test_reconcile_notification_schedules_is_idempotent(
    db_session: Session,
) -> None:
    """Reconciling twice keeps exactly one schedule per kind."""
    _plan_notification_work(db_session)
    _plan_notification_work(db_session)
    db_session.commit()

    keys = [
        schedule.schedule_key for schedule in db_session.query(PipelineSchedule).all()
    ]
    assert_that(keys).contains(DIGEST_SCHEDULE_KEY, RETENTION_SCHEDULE_KEY)
    assert_that(len(keys)).is_equal_to(len(set(keys)))


def test_run_due_digest_work_delivers_and_completes(db_session: Session) -> None:
    """Digest work evaluates rules, delivers digests, and completes items."""
    graph = seed_catalog_graph(db_session)
    user = AccountUser(email="reader@example.com")
    db_session.add(user)
    db_session.flush()
    db_session.add(AccountPreference(user_id=user.id))
    follow_podcast(db=db_session, user_id=user.id, podcast_id=graph.podcast_id)
    create_podcast_alert_rule(
        db=db_session, user_id=user.id, podcast_id=graph.podcast_id
    )
    db_session.add(
        Episode(podcast_id=graph.podcast_id, title="Pilot II", episode_number=2),
    )
    _plan_notification_work(db_session)
    db_session.commit()
    sender = RecordingDigestSender()

    skipped = run_due_digest_work(db=db_session, sender=None, now=_NOW)
    assert_that(skipped).is_empty()

    results = run_due_digest_work(db=db_session, sender=sender, now=_NOW)
    db_session.commit()

    assert_that(results).is_length(1)
    assert_that(results[0].users_evaluated).is_equal_to(1)
    assert_that(results[0].digests_delivered).is_equal_to(1)
    assert_that(sender.sent).is_equal_to(["reader@example.com"])
    item = (
        db_session.query(ScheduledWorkItemModel)
        .filter(ScheduledWorkItemModel.schedule_key == DIGEST_SCHEDULE_KEY)
        .one()
    )
    assert_that(item.status).is_equal_to(ScheduledWorkStatus.COMPLETED.value)


def test_run_due_retention_work_evaluates_transcripts(
    db_session: Session,
) -> None:
    """Retention work evaluates unpurged transcripts and completes items."""
    graph = seed_catalog_graph(db_session)
    db_session.add(
        Transcript(
            episode_id=graph.episode_id,
            provider="podscripts",
            raw_text="raw",
        ),
    )
    _plan_notification_work(db_session)
    db_session.commit()

    results = run_due_retention_work(db=db_session, now=_NOW)
    db_session.commit()

    assert_that(results).is_length(1)
    assert_that(results[0].transcripts_evaluated).is_equal_to(1)
    transcript = db_session.query(Transcript).one()
    assert_that(transcript.retention_evaluated_at).is_not_none()


def test_run_tick_plans_and_executes(db_session: Session) -> None:
    """A single tick reconciles, plans, and executes due work."""
    settings = Settings(
        scheduler_digest_interval_minutes=60,
        scheduler_retention_interval_minutes=60,
    )

    summary = run_tick(
        db=db_session,
        settings=settings,
        digest_sender=RecordingDigestSender(),
    )

    assert_that(summary.planned).is_greater_than_or_equal_to(2)
    assert_that(summary.digest_runs).is_equal_to(1)
    assert_that(summary.retention_runs).is_equal_to(1)


def test_operational_alerts_fire_on_thresholds(db_session: Session) -> None:
    """Alerts fire only when metric pressure crosses configured thresholds."""
    metrics = get_operational_metrics(db=db_session, now=_NOW)

    quiet = evaluate_operational_alerts(
        metrics=metrics,
        thresholds=OperationalAlertThresholdsData(
            review_pending=1,
            alert_delivery_pending=1,
        ),
    )
    assert_that(quiet).is_empty()

    firing = evaluate_operational_alerts(
        metrics=metrics,
        thresholds=OperationalAlertThresholdsData(
            review_pending=0,
            alert_delivery_pending=0,
        ),
    )
    assert_that([alert.key for alert in firing]).contains(
        "review_backlog",
        "delivery_backlog",
    )


@pytest.mark.parametrize(
    ("tick_minutes", "due_minutes"),
    [((0, 360, 720), (0, 360, 720)), ((0, 737, 1080), (0, 720, 1080))],
    ids=["successive-boundaries", "late-tick-coalesces-missed-intervals"],
)
def test_tick_runs_successive_cadences(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    tick_minutes: tuple[int, ...],
    due_minutes: tuple[int, ...],
) -> None:
    """Three clocked ticks execute each kind once per boundary, even when late."""
    graph = seed_catalog_graph(db_session)
    user = AccountUser(email="recurring@example.com")
    db_session.add(user)
    db_session.flush()
    db_session.add(AccountPreference(user_id=user.id))
    follow_podcast(db=db_session, user_id=user.id, podcast_id=graph.podcast_id)
    create_podcast_alert_rule(
        db=db_session,
        user_id=user.id,
        podcast_id=graph.podcast_id,
    )
    db_session.add(
        Transcript(episode_id=graph.episode_id, provider="podscripts", raw_text="raw"),
    )
    db_session.query(Podcast).filter(
        Podcast.id == graph.podcast_id
    ).one().status = "active"
    db_session.commit()
    discoveries: list[int] = []

    def discover(
        _self: DiscoveryOrchestrator,
        *,
        podcast: Podcast,
    ) -> DiscoveryResult:
        """Add a new episode at the discovery boundary without external I/O."""
        discoveries.append(podcast.id)
        db_session.add(
            Episode(
                podcast_id=podcast.id,
                title=f"Recurring episode {len(discoveries)}",
                episode_number=len(discoveries) + 1,
            ),
        )
        db_session.flush()
        return DiscoveryResult(new_episodes=1, total_discovered=1)

    monkeypatch.setattr(DiscoveryOrchestrator, "discover_for_podcast", discover)
    settings = Settings(
        scheduler_digest_interval_minutes=360,
        scheduler_retention_interval_minutes=360,
    )
    sender = RecordingDigestSender()

    for tick_minute, due_minute in zip(tick_minutes, due_minutes, strict=True):
        tick_at = _NOW + timedelta(minutes=tick_minute)
        summary = run_tick(
            db=db_session,
            settings=settings,
            digest_sender=sender,
            now=tick_at,
        )
        assert_that(summary.planned).is_equal_to(3)
        assert_that(summary.discovery_runs).is_equal_to(1)
        assert_that(summary.digest_runs).is_equal_to(1)
        assert_that(summary.retention_runs).is_equal_to(1)
        items = (
            db_session.query(ScheduledWorkItemModel)
            .filter(
                ScheduledWorkItemModel.due_at == _NOW + timedelta(minutes=due_minute),
            )
            .all()
        )
        assert_that(items).is_length(3)
        assert_that([item.task_kind for item in items]).contains_only(
            "ingestion",
            "digest",
            "retention",
        )
        assert_that({item.status for item in items}).is_equal_to({"completed"})

        assert_that(
            run_tick(
                db=db_session,
                settings=settings,
                digest_sender=sender,
                now=tick_at,
            ),
        ).is_equal_to(
            TickSummaryData(
                planned=0, discovery_runs=0, digest_runs=0, retention_runs=0
            ),
        )

    items = db_session.query(ScheduledWorkItemModel).all()
    assert_that(items).is_length(9)
    assert_that({item.work_key for item in items}).is_length(9)
    assert_that(discoveries).is_equal_to([graph.podcast_id] * 3)
    assert_that(sender.sent).is_equal_to([user.email] * 3)


def test_tick_respects_changed_intervals(db_session: Session) -> None:
    """Runner reconciliation applies shorter and longer notification cadences."""
    settings = Settings(
        scheduler_digest_interval_minutes=60,
        scheduler_retention_interval_minutes=60,
    )
    sender = RecordingDigestSender()
    first = run_tick(db=db_session, settings=settings, digest_sender=sender, now=_NOW)
    assert_that(first.planned).is_equal_to(2)
    changed_settings = Settings(
        scheduler_digest_interval_minutes=30,
        scheduler_retention_interval_minutes=120,
    )
    changed = run_tick(
        db=db_session,
        settings=changed_settings,
        digest_sender=sender,
        now=_NOW + timedelta(minutes=15),
    )
    assert_that(changed.planned).is_equal_to(0)

    shorter = run_tick(
        db=db_session,
        settings=changed_settings,
        digest_sender=sender,
        now=_NOW + timedelta(minutes=30),
    )
    assert_that(
        (shorter.planned, shorter.digest_runs, shorter.retention_runs)
    ).is_equal_to(
        (1, 1, 0),
    )
    longer = run_tick(
        db=db_session,
        settings=changed_settings,
        digest_sender=sender,
        now=_NOW + timedelta(minutes=120),
    )
    assert_that(
        (longer.planned, longer.digest_runs, longer.retention_runs)
    ).is_equal_to(
        (2, 1, 1),
    )


def _seed_transcripts(db_session: Session, *, count: int) -> list[int]:
    """Persist ``count`` unpurged transcripts and return their IDs in order."""
    graph = seed_catalog_graph(db_session)
    return _add_transcripts(db_session, episode_id=graph.episode_id, count=count)


def _add_transcripts(db_session: Session, *, episode_id: int, count: int) -> list[int]:
    """Persist and commit ``count`` unpurged transcripts for one episode."""
    transcripts = [
        Transcript(episode_id=episode_id, provider="podscripts", raw_text="raw")
        for _ in range(count)
    ]
    db_session.add_all(transcripts)
    db_session.commit()
    return [transcript.id for transcript in transcripts]


def _as_utc(value: datetime | None) -> datetime | None:
    """Normalize a stored timestamp; some dialects return naive UTC values."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _run_retention_job(
    db_session: Session,
    *,
    job: int,
    batch_size: int,
    commit: bool = True,
) -> list[int]:
    """Plan and run one retention work item at its own boundary.

    Returns:
        IDs of the transcripts that job evaluated, in ascending order.
    """
    at = _NOW + timedelta(minutes=60 * job)
    reconcile_notification_schedules(
        db=db_session,
        digest_interval_minutes=60,
        retention_interval_minutes=60,
        now=at,
    )
    plan_due_scheduled_work(db=db_session, now=at)
    results = run_due_retention_work(db=db_session, batch_size=batch_size, now=at)
    evaluated = [
        transcript.id
        for transcript in db_session.query(Transcript)
        .order_by(Transcript.id.asc())
        .all()
        if _as_utc(transcript.retention_evaluated_at) == at
    ]
    if commit:
        db_session.commit()
    else:
        db_session.rollback()
    assert_that(results).is_length(1)
    assert_that(results[0].transcripts_evaluated).is_equal_to(len(evaluated))
    assert_that(len(evaluated)).is_less_than_or_equal_to(batch_size)
    return evaluated


def test_retention_sweep_reaches_later_batches(db_session: Session) -> None:
    """Two jobs over 201 transcripts evaluate every transcript, 200 at most each."""
    ids = _seed_transcripts(db_session, count=201)

    first = _run_retention_job(db_session, job=0, batch_size=200)
    second = _run_retention_job(db_session, job=1, batch_size=200)

    assert_that(first).is_equal_to(ids[:200])
    assert_that(second).is_equal_to(ids[200:])
    last = db_session.query(Transcript).filter(Transcript.id == ids[-1]).one()
    assert_that(last.retention_evaluated_at).is_not_none()


def test_retention_sweep_cycles_restart_from_lowest_id(db_session: Session) -> None:
    """A finished cycle starts over, revisiting earlier transcripts in batches."""
    ids = _seed_transcripts(db_session, count=5)

    batches = [
        _run_retention_job(db_session, job=job, batch_size=2) for job in range(6)
    ]

    assert_that(batches).is_equal_to(
        [ids[0:2], ids[2:4], ids[4:5], ids[0:2], ids[2:4], ids[4:5]],
    )


def test_retention_sweep_resumes_in_a_new_session(db_session: Session) -> None:
    """Committed progress survives a process restart."""
    ids = _seed_transcripts(db_session, count=5)
    first = _run_retention_job(db_session, job=0, batch_size=2)
    db_session.close()

    restarted = Session(bind=db_session.get_bind())
    try:
        second = _run_retention_job(restarted, job=1, batch_size=2)
    finally:
        restarted.close()

    assert_that(first).is_equal_to(ids[0:2])
    assert_that(second).is_equal_to(ids[2:4])


def test_retention_sweep_refreshes_a_checkpoint_the_session_holds(
    db_session: Session,
) -> None:
    """A checkpoint object still held by the session is re-read when locked."""
    ids = _seed_transcripts(db_session, count=6)
    first = _run_retention_job(db_session, job=0, batch_size=2)
    held = db_session.query(SweepCheckpoint).one()
    other = Session(bind=db_session.get_bind())
    try:
        second = _run_retention_job(other, job=1, batch_size=2)
    finally:
        other.close()
    third = _run_retention_job(db_session, job=2, batch_size=2)

    assert_that([first, second, third]).is_equal_to(
        [ids[0:2], ids[2:4], ids[4:6]],
    )
    assert_that(held.last_id).is_none()


def test_retention_sweep_retries_rolled_back_batch(db_session: Session) -> None:
    """A batch rolled back with its tick is evaluated again, nothing skipped."""
    ids = _seed_transcripts(db_session, count=5)
    first = _run_retention_job(db_session, job=0, batch_size=2)

    failed = _run_retention_job(db_session, job=1, batch_size=2, commit=False)
    unevaluated = [
        transcript.id
        for transcript in db_session.query(Transcript).order_by(Transcript.id).all()
        if transcript.retention_evaluated_at is None
    ]
    retried = _run_retention_job(db_session, job=1, batch_size=2)
    after = _run_retention_job(db_session, job=2, batch_size=2)

    assert_that(first).is_equal_to(ids[0:2])
    assert_that(failed).is_equal_to(ids[2:4])
    assert_that(unevaluated).is_equal_to(ids[2:5])
    assert_that(retried).is_equal_to(ids[2:4])
    assert_that(after).is_equal_to(ids[4:5])


def test_retention_sweep_defers_mid_cycle_inserts(db_session: Session) -> None:
    """Rows added mid-cycle wait for the next cycle instead of extending it."""
    ids = _seed_transcripts(db_session, count=5)
    episode_id = db_session.query(Transcript).first().episode_id
    batches = [_run_retention_job(db_session, job=0, batch_size=2)]
    ids += _add_transcripts(db_session, episode_id=episode_id, count=1)

    batches += [
        _run_retention_job(db_session, job=job, batch_size=2) for job in range(1, 6)
    ]

    assert_that(batches).is_equal_to(
        [ids[0:2], ids[2:4], ids[4:5], ids[0:2], ids[2:4], ids[4:6]],
    )


def test_retention_sweep_survives_deleted_and_purged_rows(
    db_session: Session,
) -> None:
    """Deleting the checkpoint and bound rows or purging rows strands nothing."""
    ids = _seed_transcripts(db_session, count=6)
    first = _run_retention_job(db_session, job=0, batch_size=2)
    rows = {
        transcript.id: transcript for transcript in db_session.query(Transcript).all()
    }
    db_session.delete(rows[ids[1]])
    db_session.delete(rows[ids[5]])
    rows[ids[2]].purged_at = _NOW
    db_session.commit()

    batches = [
        _run_retention_job(db_session, job=job, batch_size=2) for job in range(1, 5)
    ]

    assert_that(first).is_equal_to(ids[0:2])
    assert_that(batches).is_equal_to(
        [[ids[3], ids[4]], [], [ids[0], ids[3]], [ids[4]]],
    )


def test_retention_sweep_reaches_rows_under_continuous_arrivals(
    db_session: Session,
) -> None:
    """With a new row after every job, cycles still finish and include arrivals."""
    ids = _seed_transcripts(db_session, count=3)
    episode_id = db_session.query(Transcript).first().episode_id
    batches: list[list[int]] = []
    for job in range(6):
        batches.append(_run_retention_job(db_session, job=job, batch_size=2))
        ids += _add_transcripts(db_session, episode_id=episode_id, count=1)

    assert_that(batches).is_equal_to(
        [ids[0:2], ids[2:3], ids[0:2], ids[2:4], ids[4:5], ids[0:2]],
    )


def test_retention_sweep_rejects_empty_batches(db_session: Session) -> None:
    """A non-positive batch size cannot make progress and is refused."""
    _seed_transcripts(db_session, count=1)
    _plan_notification_work(db_session)

    with pytest.raises(ValueError, match="batch_size"):
        run_due_retention_work(db=db_session, batch_size=0, now=_NOW)

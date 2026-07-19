"""Tests for the scheduler runner, recurring work, and operational alerts."""

from datetime import UTC, datetime

from assertpy import assert_that
from sqlalchemy.orm import Session

from podex.config import Settings
from podex.models import (
    AccountPreference,
    AccountUser,
    Episode,
    PipelineSchedule,
    ScheduledWorkItemModel,
    ScheduledWorkStatus,
    Transcript,
)
from podex.scheduler_runner import run_tick
from podex.services.account_alerts import create_alert_rule
from podex.services.account_follows import follow_podcast
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
from tests.conftest import seed_catalog_graph

_NOW = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)


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
    create_alert_rule(
        db=db_session,
        user_id=user.id,
        target_type="podcast",
        target_id=graph.podcast_id,
        event_type="new_episode",
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

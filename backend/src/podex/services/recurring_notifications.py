"""Recurring digest delivery and retention sweep scheduled work."""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from podex.models import (
    AccountPreference,
    AccountUser,
    ScheduledWorkItemModel,
    ScheduledWorkStatus,
    Transcript,
)
from podex.services.account_alerts import evaluate_alert_rules
from podex.services.account_digests import send_pending_digest
from podex.services.notification_delivery import DigestSender
from podex.services.scheduled_work import (
    PipelineScheduleInputData,
    upsert_pipeline_schedule,
)
from podex.services.scheduler import ScheduledTaskKind
from podex.services.transcript_retention import TranscriptRetentionPolicy
from podex.services.transcript_retention_commands import (
    evaluate_transcript_retention,
)

DIGEST_SCHEDULE_KEY = "notifications:digest-delivery"
RETENTION_SCHEDULE_KEY = "retention:policy-sweep"


@dataclass(frozen=True, slots=True)
class DigestWorkRunData:
    """Result of one recurring digest delivery sweep."""

    work_key: str
    users_evaluated: int
    digests_delivered: int


@dataclass(frozen=True, slots=True)
class RetentionWorkRunData:
    """Result of one recurring retention evaluation sweep."""

    work_key: str
    transcripts_evaluated: int


def reconcile_notification_schedules(
    *,
    db: Session,
    digest_interval_minutes: int,
    retention_interval_minutes: int,
    now: datetime | None = None,
) -> None:
    """Ensure the digest delivery and retention sweep schedules exist.

    Args:
        db: Database session.
        digest_interval_minutes: Cadence for digest delivery sweeps.
        retention_interval_minutes: Cadence for retention evaluation sweeps.
        now: Deterministic timestamp for next-due calculation.
    """
    upsert_pipeline_schedule(
        db=db,
        payload=PipelineScheduleInputData(
            schedule_key=DIGEST_SCHEDULE_KEY,
            task_kind=ScheduledTaskKind.DIGEST,
            interval_minutes=digest_interval_minutes,
        ),
        now=now,
    )
    upsert_pipeline_schedule(
        db=db,
        payload=PipelineScheduleInputData(
            schedule_key=RETENTION_SCHEDULE_KEY,
            task_kind=ScheduledTaskKind.RETENTION,
            interval_minutes=retention_interval_minutes,
        ),
        now=now,
    )


def run_due_digest_work(
    *,
    db: Session,
    sender: DigestSender | None,
    limit: int = 5,
    now: datetime | None = None,
) -> list[DigestWorkRunData]:
    """Run pending digest work: evaluate rules and deliver pending digests.

    Args:
        db: Database session.
        sender: Configured digest sender; without one, work stays pending so
            deliveries resume when delivery is configured.
        limit: Maximum pending work items to run.
        now: Deterministic completion timestamp.

    Returns:
        Execution summaries for the work items that ran.
    """
    if sender is None:
        return []
    effective_now = now or datetime.now(UTC)
    pending = _pending_items(db=db, task_kind=ScheduledTaskKind.DIGEST, limit=limit)
    results: list[DigestWorkRunData] = []
    for item in pending:
        item.status = ScheduledWorkStatus.RUNNING.value
        item.started_at = effective_now
        db.flush()
        users = (
            db.query(AccountUser)
            .join(
                AccountPreference,
                AccountPreference.user_id == AccountUser.id,
            )
            .filter(AccountPreference.digest_enabled.is_(True))
            .order_by(AccountUser.id.asc())
            .all()
        )
        delivered = 0
        for user in users:
            evaluate_alert_rules(db=db, user_id=user.id, now=effective_now)
            digest = send_pending_digest(
                db=db,
                user=user,
                sender=sender,
                now=effective_now,
            )
            if digest is not None:
                delivered += 1
        item.status = ScheduledWorkStatus.COMPLETED.value
        item.completed_at = effective_now
        db.flush()
        results.append(
            DigestWorkRunData(
                work_key=item.work_key,
                users_evaluated=len(users),
                digests_delivered=delivered,
            ),
        )
    return results


def run_due_retention_work(
    *,
    db: Session,
    policy: TranscriptRetentionPolicy | None = None,
    limit: int = 5,
    batch_size: int = 200,
    now: datetime | None = None,
) -> list[RetentionWorkRunData]:
    """Run pending retention work: evaluate lifecycle policy over transcripts.

    Args:
        db: Database session.
        policy: Retention policy; defaults to the standard policy.
        limit: Maximum pending work items to run.
        batch_size: Maximum transcripts evaluated per work item.
        now: Deterministic evaluation timestamp.

    Returns:
        Execution summaries for the work items that ran.
    """
    effective_policy = policy or TranscriptRetentionPolicy()
    effective_now = now or datetime.now(UTC)
    pending = _pending_items(
        db=db,
        task_kind=ScheduledTaskKind.RETENTION,
        limit=limit,
    )
    results: list[RetentionWorkRunData] = []
    for item in pending:
        item.status = ScheduledWorkStatus.RUNNING.value
        item.started_at = effective_now
        db.flush()
        transcripts = (
            db.query(Transcript)
            .filter(Transcript.purged_at.is_(None))
            .order_by(Transcript.id.asc())
            .limit(batch_size)
            .all()
        )
        for transcript in transcripts:
            evaluate_transcript_retention(
                db=db,
                transcript=transcript,
                extraction_confidence=None,
                source_retention_opt_out=transcript.source_retention_opt_out,
                policy=effective_policy,
                now=effective_now,
            )
        item.status = ScheduledWorkStatus.COMPLETED.value
        item.completed_at = effective_now
        db.flush()
        results.append(
            RetentionWorkRunData(
                work_key=item.work_key,
                transcripts_evaluated=len(transcripts),
            ),
        )
    return results


def _pending_items(
    *,
    db: Session,
    task_kind: ScheduledTaskKind,
    limit: int,
) -> list[ScheduledWorkItemModel]:
    """Load pending scheduled work items of one kind, oldest first."""
    return list(
        db.query(ScheduledWorkItemModel)
        .filter(ScheduledWorkItemModel.status == ScheduledWorkStatus.PENDING.value)
        .filter(ScheduledWorkItemModel.task_kind == task_kind.value)
        .order_by(ScheduledWorkItemModel.due_at.asc(), ScheduledWorkItemModel.id.asc())
        .limit(limit)
        .all(),
    )

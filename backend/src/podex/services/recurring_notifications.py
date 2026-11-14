"""Recurring digest delivery and retention sweep scheduled work."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypedDict, Unpack

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from podex.config import RetentionSettings, get_settings
from podex.models import (
    AccountPreference,
    AccountUser,
    ScheduledWorkItemModel,
    ScheduledWorkStatus,
    SweepCheckpoint,
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
    PURGE_DISABLED_REASON,
    evaluate_transcript_retention,
)

DIGEST_SCHEDULE_KEY = "notifications:digest-delivery"
RETENTION_SCHEDULE_KEY = "retention:policy-sweep"


class RetentionWorkOptions(TypedDict, total=False):
    """Optional environment authorization for the existing sweep inputs."""

    retention: RetentionSettings | None


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
    purge_blocker: str | None


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
    **options: Unpack[RetentionWorkOptions],
) -> list[RetentionWorkRunData]:
    """Run pending retention work: evaluate lifecycle policy over transcripts.

    Args:
        db: Database session.
        policy: Retention policy; defaults to the standard policy.
        limit: Maximum pending work items to run.
        batch_size: Maximum transcripts evaluated per work item; successive
            items resume after the last committed transcript.
        now: Deterministic evaluation timestamp.
        **options: Optional retention settings; omitted or None uses runtime settings.

    Returns:
        Execution summaries for the work items that ran.
    """
    effective_policy = policy or TranscriptRetentionPolicy()
    if options.keys() - {"retention"}:
        raise TypeError("Only retention is a supported retention work option")
    retention = options.get("retention")
    purge_enabled = (
        retention if retention is not None else get_settings().retention
    ).purge_enabled
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
        evaluated = _evaluate_next_retention_batch(
            db=db,
            policy=effective_policy,
            batch_size=batch_size,
            now=effective_now,
        )
        if not purge_enabled:
            item.metadata_json = {
                **(item.metadata_json or {}),
                "purge_blocker": PURGE_DISABLED_REASON,
            }
        item.status = ScheduledWorkStatus.COMPLETED.value
        item.completed_at = effective_now
        db.flush()
        results.append(
            RetentionWorkRunData(
                work_key=item.work_key,
                transcripts_evaluated=evaluated,
                purge_blocker=None if purge_enabled else PURGE_DISABLED_REASON,
            ),
        )
    return results


def _evaluate_next_retention_batch(
    *,
    db: Session,
    policy: TranscriptRetentionPolicy,
    batch_size: int,
    now: datetime,
) -> int:
    """Evaluate the next keyset batch of the current retention cycle.

    A cycle covers every unpurged transcript whose ID is at most the largest
    transcript ID present when the cycle started; later arrivals wait for the
    next cycle, so continuous inserts cannot keep a cycle from finishing. The
    checkpoint moves in the caller's transaction, so a rolled-back batch is
    evaluated again on retry. The keyset never dereferences the checkpoint
    row, so deleted or purged rows cannot strand it.

    Args:
        db: Database session.
        policy: Retention policy applied to each transcript.
        batch_size: Maximum transcripts evaluated by this call.
        now: Evaluation timestamp.

    Returns:
        Number of transcripts evaluated.

    Raises:
        ValueError: If ``batch_size`` is less than one.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    checkpoint = _retention_checkpoint(db=db, now=now)
    if checkpoint.cycle_upper_id is None:
        checkpoint.last_id = None
        checkpoint.cycle_upper_id = db.query(func.max(Transcript.id)).scalar()
        if checkpoint.cycle_upper_id is None:
            return 0
    query = db.query(Transcript).filter(
        Transcript.purged_at.is_(None),
        Transcript.id <= checkpoint.cycle_upper_id,
    )
    if checkpoint.last_id is not None:
        query = query.filter(Transcript.id > checkpoint.last_id)
    transcripts = query.order_by(Transcript.id.asc()).limit(batch_size).all()
    for transcript in transcripts:
        evaluate_transcript_retention(
            db=db,
            transcript=transcript,
            extraction_confidence=None,
            source_retention_opt_out=transcript.source_retention_opt_out,
            policy=policy,
            now=now,
        )
    if len(transcripts) < batch_size or (
        transcripts[-1].id >= checkpoint.cycle_upper_id
    ):
        checkpoint.last_id = None
        checkpoint.cycle_upper_id = None
    else:
        checkpoint.last_id = transcripts[-1].id
    checkpoint.updated_at = now
    db.flush()
    return len(transcripts)


def _retention_checkpoint(*, db: Session, now: datetime) -> SweepCheckpoint:
    """Load the retention sweep checkpoint for update, creating it if absent."""
    checkpoint = _locked_retention_checkpoint(db=db)
    if checkpoint is not None:
        return checkpoint
    # Flush first, so the handler below only ever sees the checkpoint insert
    # and cannot hide an integrity error from another pending row.
    db.flush()
    try:
        # A savepoint keeps a lost creation race from discarding the caller's
        # transaction; the winner's row is then loaded and locked instead.
        with db.begin_nested():
            db.add(SweepCheckpoint(sweep_key=RETENTION_SCHEDULE_KEY, updated_at=now))
    except IntegrityError:
        pass
    checkpoint = _locked_retention_checkpoint(db=db)
    if checkpoint is None:
        raise RuntimeError("Retention sweep checkpoint could not be created")
    return checkpoint


def _locked_retention_checkpoint(*, db: Session) -> SweepCheckpoint | None:
    """Lock the retention checkpoint row and refresh any cached copy of it."""
    return (
        db.query(SweepCheckpoint)
        .filter(SweepCheckpoint.sweep_key == RETENTION_SCHEDULE_KEY)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )


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

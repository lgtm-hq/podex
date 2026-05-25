"""Recurring search, digest, and transcript retention maintenance services."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from podex.models import (
    AuditAction,
    Episode,
    IngestionRun,
    Media,
    MentionCandidate,
    ScheduledWorkItemModel,
    ScheduledWorkStatus,
    Transcript,
    TranscriptSourceRetentionPolicy,
)
from podex.models.search_projection_repair import (
    SearchProjectionRepairReason,
    SearchProjectionRepairResourceType,
    SearchProjectionRepairStatus,
)
from podex.services.audit_log import record_audit_log
from podex.services.scheduled_work import (
    PipelineScheduleInputData,
    PipelineScheduleSummaryData,
    plan_due_scheduled_work,
    upsert_pipeline_schedule,
)
from podex.services.scheduler import ScheduledTaskKind
from podex.services.search_projection_repairs import ensure_search_projection_repair
from podex.services.transcript_retention_commands import evaluate_transcript_retention
from podex.services.transcript_retention_policies import to_retention_policy

REINDEX_WORK_KIND = "search_reindex"
TRANSCRIPT_DIGEST_WORK_KIND = "transcript_digest"
TRANSCRIPT_RETENTION_WORK_KIND = "transcript_retention"


@dataclass(frozen=True, slots=True)
class MaintenanceScheduleResultData:
    """Result of reconciling recurring maintenance schedules."""

    schedules: list[PipelineScheduleSummaryData]


@dataclass(frozen=True, slots=True)
class MaintenanceWorkRunData:
    """Result of running one recurring maintenance work item."""

    work_item_id: int
    ingestion_run_id: int
    kind: str
    processed_count: int
    error_message: str | None


ReindexRunner = Callable[[Session], int]
DigestRunner = Callable[[Session, datetime], int]
RetentionRunner = Callable[[Session, datetime], int]


def reconcile_recurring_maintenance_schedules(
    *,
    db: Session,
    reindex_interval_minutes: int = 1440,
    digest_interval_minutes: int = 360,
    retention_interval_minutes: int = 360,
    now: datetime | None = None,
) -> MaintenanceScheduleResultData:
    """Ensure recurring reindex, digest, and retention schedules exist.

    Args:
        db: Database session.
        reindex_interval_minutes: Cadence for search reindex repair scheduling.
        digest_interval_minutes: Cadence for missing transcript digest generation.
        retention_interval_minutes: Cadence for saved-policy retention evaluation.
        now: Deterministic timestamp for next-due calculation.

    Returns:
        Reconciled maintenance schedule summaries.
    """
    effective_now = now or datetime.now(UTC)
    schedules = [
        upsert_pipeline_schedule(
            db=db,
            payload=PipelineScheduleInputData(
                schedule_key="maintenance:search-reindex",
                task_kind=ScheduledTaskKind.REINDEX,
                interval_minutes=reindex_interval_minutes,
                metadata_json={"kind": REINDEX_WORK_KIND},
                start_at=effective_now,
            ),
            now=effective_now,
        ),
        upsert_pipeline_schedule(
            db=db,
            payload=PipelineScheduleInputData(
                schedule_key="maintenance:transcript-retention",
                task_kind=ScheduledTaskKind.RETENTION,
                interval_minutes=retention_interval_minutes,
                metadata_json={"kind": TRANSCRIPT_RETENTION_WORK_KIND},
                start_at=effective_now,
            ),
            now=effective_now,
        ),
        upsert_pipeline_schedule(
            db=db,
            payload=PipelineScheduleInputData(
                schedule_key="maintenance:transcript-digest",
                task_kind=ScheduledTaskKind.DIGEST,
                interval_minutes=digest_interval_minutes,
                metadata_json={"kind": TRANSCRIPT_DIGEST_WORK_KIND},
                start_at=effective_now,
            ),
            now=effective_now,
        ),
    ]
    return MaintenanceScheduleResultData(schedules=schedules)


def plan_recurring_maintenance_work(
    *,
    db: Session,
    reindex_interval_minutes: int = 1440,
    digest_interval_minutes: int = 360,
    retention_interval_minutes: int = 360,
    now: datetime | None = None,
) -> list[ScheduledWorkItemModel]:
    """Reconcile maintenance schedules and persist currently due work.

    Args:
        db: Database session.
        reindex_interval_minutes: Cadence for search reindex repair scheduling.
        digest_interval_minutes: Cadence for missing transcript digest generation.
        retention_interval_minutes: Cadence for saved-policy retention evaluation.
        now: Deterministic timestamp for planning.

    Returns:
        Newly persisted maintenance work item models.
    """
    effective_now = now or datetime.now(UTC)
    reconcile_recurring_maintenance_schedules(
        db=db,
        reindex_interval_minutes=reindex_interval_minutes,
        digest_interval_minutes=digest_interval_minutes,
        retention_interval_minutes=retention_interval_minutes,
        now=effective_now,
    )
    planned = plan_due_scheduled_work(db=db, now=effective_now)
    return [
        db.query(ScheduledWorkItemModel)
        .filter(ScheduledWorkItemModel.id == item.id)
        .one()
        for item in planned
        if _is_maintenance_metadata(metadata=item.metadata_json)
    ]


def run_due_maintenance_work(
    *,
    db: Session,
    reindex_runner: ReindexRunner | None = None,
    digest_runner: DigestRunner | None = None,
    retention_runner: RetentionRunner | None = None,
    limit: int = 10,
    now: datetime | None = None,
) -> list[MaintenanceWorkRunData]:
    """Run pending recurring search, digest, and retention work items.

    Args:
        db: Database session.
        reindex_runner: Optional reindex runner for tests or alternate execution.
        digest_runner: Optional digest runner for tests or alternate execution.
        retention_runner: Optional retention runner for tests or alternate execution.
        limit: Maximum pending maintenance items to run.
        now: Deterministic timestamp for run bookkeeping.

    Returns:
        Execution summaries.
    """
    effective_now = now or datetime.now(UTC)
    effective_reindex_runner = reindex_runner or enqueue_full_reindex_repairs
    effective_digest_runner = digest_runner or generate_missing_transcript_digests
    effective_retention_runner = (
        retention_runner or evaluate_saved_transcript_retention_policies
    )
    pending_items = (
        db.query(ScheduledWorkItemModel)
        .filter(ScheduledWorkItemModel.status == ScheduledWorkStatus.PENDING.value)
        .filter(
            ScheduledWorkItemModel.task_kind.in_(
                [
                    ScheduledTaskKind.REINDEX.value,
                    ScheduledTaskKind.DIGEST.value,
                    ScheduledTaskKind.RETENTION.value,
                ],
            ),
        )
        .order_by(ScheduledWorkItemModel.due_at.asc(), ScheduledWorkItemModel.id.asc())
        .limit(limit)
        .all()
    )
    results: list[MaintenanceWorkRunData] = []

    for work_item in pending_items:
        metadata = work_item.metadata_json or {}
        kind = metadata.get("kind")
        if kind not in {
            REINDEX_WORK_KIND,
            TRANSCRIPT_DIGEST_WORK_KIND,
            TRANSCRIPT_RETENTION_WORK_KIND,
        }:
            continue

        run = IngestionRun(status="in_progress", started_at=effective_now)
        db.add(run)
        db.flush()
        work_item.status = ScheduledWorkStatus.RUNNING.value
        work_item.started_at = effective_now
        work_item.ingestion_run_id = run.id
        db.flush()

        try:
            if kind == REINDEX_WORK_KIND:
                processed_count = effective_reindex_runner(db)
            elif kind == TRANSCRIPT_DIGEST_WORK_KIND:
                processed_count = effective_digest_runner(db, effective_now)
            else:
                processed_count = effective_retention_runner(db, effective_now)
        except Exception as exc:
            run.status = "failed"
            run.error_summary = str(exc)
            run.completed_at = effective_now
            work_item.status = ScheduledWorkStatus.FAILED.value
            work_item.error_message = str(exc)
            work_item.completed_at = effective_now
            results.append(
                MaintenanceWorkRunData(
                    work_item_id=work_item.id,
                    ingestion_run_id=run.id,
                    kind=str(kind),
                    processed_count=0,
                    error_message=str(exc),
                )
            )
            continue

        run.status = "completed"
        run.completed_at = effective_now
        work_item.status = ScheduledWorkStatus.COMPLETED.value
        work_item.error_message = None
        work_item.completed_at = effective_now
        results.append(
            MaintenanceWorkRunData(
                work_item_id=work_item.id,
                ingestion_run_id=run.id,
                kind=str(kind),
                processed_count=processed_count,
                error_message=None,
            )
        )

    db.flush()
    return results


def enqueue_full_reindex_repairs(db: Session) -> int:
    """Queue replay-safe search projection repairs for catalog resources.

    Args:
        db: Database session.

    Returns:
        Number of resource repair requests touched.
    """
    processed_count = 0
    for media_id in db.query(Media.id).order_by(Media.id.asc()).all():
        ensure_search_projection_repair(
            db=db,
            resource_type=SearchProjectionRepairResourceType.MEDIA,
            resource_id=media_id[0],
            reason=SearchProjectionRepairReason.SCHEDULED_REINDEX,
            status=SearchProjectionRepairStatus.PENDING,
            metadata_json={"scheduled": True},
        )
        processed_count += 1

    for episode_id in db.query(Episode.id).order_by(Episode.id.asc()).all():
        ensure_search_projection_repair(
            db=db,
            resource_type=SearchProjectionRepairResourceType.EPISODE,
            resource_id=episode_id[0],
            reason=SearchProjectionRepairReason.SCHEDULED_REINDEX,
            status=SearchProjectionRepairStatus.PENDING,
            metadata_json={"scheduled": True},
        )
        processed_count += 1

    return processed_count


def generate_missing_transcript_digests(
    db: Session,
    now: datetime,
    limit: int = 100,
) -> int:
    """Generate compact text digests for transcripts that lack one.

    Args:
        db: Database session.
        now: Digest creation timestamp.
        limit: Maximum transcripts to update.

    Returns:
        Number of transcripts updated.
    """
    transcripts = (
        db.query(Transcript)
        .filter(Transcript.digest_text.is_(None))
        .filter(Transcript.purged_at.is_(None))
        .filter(
            or_(
                Transcript.cleaned_text.is_not(None),
                Transcript.raw_text.is_not(None),
            ),
        )
        .order_by(Transcript.fetched_at.asc(), Transcript.id.asc())
        .limit(limit)
        .all()
    )

    for transcript in transcripts:
        source_text = transcript.cleaned_text or transcript.raw_text or ""
        transcript.digest_text = _compact_digest_text(source_text)
        transcript.digest_created_at = now

    db.flush()
    return len(transcripts)


def evaluate_saved_transcript_retention_policies(
    db: Session,
    now: datetime,
    limit: int = 100,
) -> int:
    """Evaluate transcripts whose source has a persisted retention policy.

    Args:
        db: Database session.
        now: Retention evaluation timestamp.
        limit: Maximum transcripts to evaluate.

    Returns:
        Number of transcript lifecycle decisions persisted.
    """
    configured_transcripts = (
        db.query(Transcript, TranscriptSourceRetentionPolicy)
        .join(Episode, Episode.id == Transcript.episode_id)
        .join(
            TranscriptSourceRetentionPolicy,
            and_(
                TranscriptSourceRetentionPolicy.podcast_id == Episode.podcast_id,
                TranscriptSourceRetentionPolicy.source_key == Transcript.provider,
            ),
        )
        .filter(Transcript.purged_at.is_(None))
        .order_by(Transcript.fetched_at.asc(), Transcript.id.asc())
        .limit(limit)
        .all()
    )

    for transcript, record in configured_transcripts:
        result = evaluate_transcript_retention(
            db=db,
            transcript=transcript,
            extraction_confidence=_latest_extraction_confidence(
                db=db,
                transcript=transcript,
            ),
            source_retention_opt_out=record.source_retention_opt_out,
            policy=to_retention_policy(record=record),
            now=now,
        )
        record_audit_log(
            db=db,
            action=AuditAction.EVALUATE_TRANSCRIPT_RETENTION,
            resource_type="transcript",
            resource_id=transcript.id,
            actor_name="system:retention-maintenance",
            summary=(
                "Automatically evaluated transcript retention as "
                f"{result.decision.tier.value}"
            ),
            metadata_json={
                "policy_version": record.policy_version,
                "source_key": record.source_key,
                "source_retention_opt_out": record.source_retention_opt_out,
                "purge_eligible": result.decision.purge_eligible,
            },
        )

    db.flush()
    return len(configured_transcripts)


def _latest_extraction_confidence(
    *,
    db: Session,
    transcript: Transcript,
) -> float | None:
    """Return the highest mention-candidate confidence for one episode."""
    row = (
        db.query(MentionCandidate.confidence)
        .filter(MentionCandidate.episode_id == transcript.episode_id)
        .order_by(MentionCandidate.confidence.desc())
        .first()
    )
    return row[0] if row is not None else None


def _compact_digest_text(text: str, max_length: int = 1000) -> str:
    """Build a bounded digest from transcript text."""
    compacted = " ".join(text.split())
    if len(compacted) <= max_length:
        return compacted
    return f"{compacted[: max_length - 3].rstrip()}..."


def _is_maintenance_metadata(
    *,
    metadata: dict[str, object] | None,
) -> bool:
    """Check whether scheduled work metadata describes maintenance work."""
    return bool(
        metadata
        and metadata.get("kind")
        in {
            REINDEX_WORK_KIND,
            TRANSCRIPT_DIGEST_WORK_KIND,
            TRANSCRIPT_RETENTION_WORK_KIND,
        },
    )

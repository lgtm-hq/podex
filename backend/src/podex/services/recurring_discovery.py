"""Recurring episode discovery scheduling and execution services."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from podex.models import (
    IngestionRun,
    IngestionRunStatus,
    PipelineSchedule,
    Podcast,
    PodcastStatus,
    ScheduledWorkItemModel,
    ScheduledWorkStatus,
)
from podex.services.discovery import DiscoveryResult
from podex.services.discovery_orchestrator import DiscoveryOrchestrator
from podex.services.failure_descriptors import (
    describe_failure,
    log_failure,
    persisted_failure,
)
from podex.services.scheduled_work import (
    PipelineScheduleInputData,
    PipelineScheduleSummaryData,
    plan_due_scheduled_work,
    upsert_pipeline_schedule,
)
from podex.services.scheduler import ScheduledTaskKind

logger = logging.getLogger(__name__)

DISCOVERY_WORK_KIND = "episode_discovery"


@dataclass(frozen=True, slots=True)
class DiscoveryScheduleResultData:
    """Result of reconciling recurring discovery schedules."""

    schedules: list[PipelineScheduleSummaryData]


@dataclass(frozen=True, slots=True)
# This flat data record mirrors its published fields; nesting changes the contract.
# pylint: disable-next=too-many-instance-attributes
class DiscoveryWorkRunData:
    """Result of running one scheduled discovery work item."""

    work_item_id: int
    ingestion_run_id: int
    podcast_id: int
    podcast_slug: str
    new_episodes: int
    updated_episodes: int
    total_discovered: int
    errors: list[str]


DiscoveryRunner = Callable[[Session, Podcast], DiscoveryResult]


def reconcile_recurring_discovery_schedules(
    *,
    db: Session,
    interval_minutes: int = 360,
    now: datetime | None = None,
) -> DiscoveryScheduleResultData:
    """Reconcile discovery schedules with current podcast eligibility.

    Existing schedules for inactive or deleted podcasts are disabled and their
    pending discovery is terminally skipped. Re-enabling uses the schedule
    contract's first future boundary, without replaying skipped work.

    Args:
        db: Database session.
        interval_minutes: Discovery cadence for active podcasts.
        now: Deterministic timestamp for next-due calculation.

    Returns:
        Reconciled schedule summaries.
    """
    effective_now = now or datetime.now(UTC)
    podcasts = (
        db.query(Podcast)
        .filter(Podcast.status == PodcastStatus.ACTIVE.value)
        .order_by(Podcast.slug.asc())
        .all()
    )
    _disable_inactive_discovery_schedules(
        db=db,
        active_podcast_ids={podcast.id for podcast in podcasts},
        now=effective_now,
    )
    schedules = [
        upsert_pipeline_schedule(
            db=db,
            payload=PipelineScheduleInputData(
                schedule_key=_discovery_schedule_key(podcast_id=podcast.id),
                task_kind=ScheduledTaskKind.INGESTION,
                interval_minutes=interval_minutes,
                enabled=True,
                metadata_json={
                    "kind": DISCOVERY_WORK_KIND,
                    "podcast_id": podcast.id,
                    "podcast_slug": podcast.slug,
                },
                start_at=effective_now,
            ),
            now=effective_now,
        )
        for podcast in podcasts
    ]
    return DiscoveryScheduleResultData(schedules=schedules)


def plan_recurring_discovery_work(
    *,
    db: Session,
    interval_minutes: int = 360,
    now: datetime | None = None,
) -> list[ScheduledWorkItemModel]:
    """Reconcile discovery schedules and persist due work items.

    Args:
        db: Database session.
        interval_minutes: Discovery cadence for active podcasts.
        now: Deterministic timestamp for planning.

    Returns:
        Newly persisted scheduled work item models.
    """
    effective_now = now or datetime.now(UTC)
    reconcile_recurring_discovery_schedules(
        db=db,
        interval_minutes=interval_minutes,
        now=effective_now,
    )
    planned = plan_due_scheduled_work(db=db, now=effective_now)
    return [
        db.query(ScheduledWorkItemModel)
        .filter(
            ScheduledWorkItemModel.id == item.id,
        )
        .one()
        for item in planned
        if _is_discovery_metadata(metadata=item.metadata_json)
    ]


def run_due_episode_discovery_work(
    *,
    db: Session,
    runner: DiscoveryRunner | None = None,
    limit: int = 10,
) -> list[DiscoveryWorkRunData]:
    """Run pending scheduled episode discovery work.

    Inactive podcasts produce a terminal ``skipped`` item with a reason and
    completion timestamp, without an ingestion run or provider invocation.
    Eligibility is re-read immediately before the provider is called, without
    a row lock. Discovery already in flight when a podcast is paused can finish.

    Args:
        db: Database session.
        runner: Optional discovery runner for tests or alternate execution.
        limit: Maximum pending work items to run.

    Returns:
        Execution summaries.
    """
    effective_runner = runner or _run_discovery_orchestrator
    pending_items = (
        db.query(ScheduledWorkItemModel.id, ScheduledWorkItemModel.metadata_json)
        .filter(ScheduledWorkItemModel.status == ScheduledWorkStatus.PENDING.value)
        .filter(ScheduledWorkItemModel.task_kind == ScheduledTaskKind.INGESTION.value)
        .order_by(ScheduledWorkItemModel.due_at.asc(), ScheduledWorkItemModel.id.asc())
        .limit(limit)
        .all()
    )
    results: list[DiscoveryWorkRunData] = []

    for work_item_id, metadata in pending_items:
        if not _is_discovery_metadata(metadata=metadata):
            continue
        work_item = (
            db.query(ScheduledWorkItemModel)
            .filter(
                ScheduledWorkItemModel.id == work_item_id,
                ScheduledWorkItemModel.status == ScheduledWorkStatus.PENDING.value,
            )
            .with_for_update(skip_locked=True)
            .populate_existing()
            .first()
        )
        if work_item is None:
            continue
        if not _is_discovery_metadata(metadata=work_item.metadata_json):
            continue

        metadata = work_item.metadata_json or {}
        podcast_id = metadata.get("podcast_id")
        if not isinstance(podcast_id, int):
            _mark_work_failed(work_item=work_item, error_message="Missing podcast_id")
            continue

        # Re-read the podcast so a pause since planning is seen. No row lock is
        # taken: it would be held across the provider's network calls and block
        # ops edits and follows. A pause that lands during a provider call lets
        # that one run finish; the next reconcile disables the schedule.
        podcast = (
            db.query(Podcast)
            .filter(Podcast.id == podcast_id)
            .populate_existing()
            .first()
        )
        if podcast is None:
            _mark_work_failed(work_item=work_item, error_message="Podcast not found")
            continue

        if podcast.status != PodcastStatus.ACTIVE:
            _mark_work_skipped(
                work_item=work_item,
                reason="Podcast is not active",
                now=datetime.now(UTC),
            )
            continue

        run = IngestionRun(
            status=IngestionRunStatus.RUNNING,
            started_at=datetime.now(UTC),
        )
        db.add(run)
        db.flush()

        work_item.status = ScheduledWorkStatus.RUNNING.value
        work_item.started_at = run.started_at
        work_item.ingestion_run_id = run.id
        db.flush()

        try:
            discovery_result = effective_runner(db, podcast)
        # Keep other providers and tasks running after this failure.
        # pylint: disable-next=broad-exception-caught
        except Exception as exc:
            run.status = IngestionRunStatus.FAILED
            run.error_summary = describe_failure("discovery", exc)
            log_failure(logger, run.error_summary, exc)
            run.completed_at = datetime.now(UTC)
            _mark_work_failed(work_item=work_item, error_message=run.error_summary)
            continue

        completed_at = datetime.now(UTC)
        run.status = (
            IngestionRunStatus.COMPLETED
            if not discovery_result.errors
            else IngestionRunStatus.FAILED
        )
        discovery_result.errors = [
            persisted_failure(error) for error in discovery_result.errors
        ]
        run.error_summary = "; ".join(discovery_result.errors) or None
        run.completed_at = completed_at
        work_item.status = (
            ScheduledWorkStatus.COMPLETED.value
            if not discovery_result.errors
            else ScheduledWorkStatus.FAILED.value
        )
        work_item.error_message = run.error_summary
        work_item.completed_at = completed_at
        results.append(
            DiscoveryWorkRunData(
                work_item_id=work_item.id,
                ingestion_run_id=run.id,
                podcast_id=podcast.id,
                podcast_slug=podcast.slug,
                new_episodes=discovery_result.new_episodes,
                updated_episodes=discovery_result.updated_episodes,
                total_discovered=discovery_result.total_discovered,
                errors=discovery_result.errors,
            )
        )

    db.flush()
    return results


def _disable_inactive_discovery_schedules(
    *,
    db: Session,
    active_podcast_ids: set[int],
    now: datetime,
) -> None:
    """Disable orphaned/inactive discovery schedules and discard pending work.

    Pending discovery is discarded for every disabled schedule, not only the
    ones disabled by this call. That covers work committed by a planner after
    an earlier disable, and it runs before a schedule is re-enabled, so resume
    never replays stale work.
    """
    for schedule in db.query(PipelineSchedule).filter(
        PipelineSchedule.enabled.is_(True),
    ):
        metadata = schedule.metadata_json or {}
        if not _is_discovery_metadata(metadata=metadata):
            continue
        podcast_id = metadata.get("podcast_id")
        if isinstance(podcast_id, int) and podcast_id in active_podcast_ids:
            continue
        schedule.enabled = False
    db.flush()

    disabled_schedule_ids = select(PipelineSchedule.id).where(
        PipelineSchedule.enabled.is_(False),
    )
    pending = (
        db.query(ScheduledWorkItemModel)
        .filter(
            ScheduledWorkItemModel.schedule_id.in_(disabled_schedule_ids),
            ScheduledWorkItemModel.task_kind == ScheduledTaskKind.INGESTION.value,
            ScheduledWorkItemModel.status == ScheduledWorkStatus.PENDING.value,
        )
        .with_for_update()
        .populate_existing()
        .all()
    )
    for item in pending:
        if _is_discovery_metadata(metadata=item.metadata_json):
            _mark_work_skipped(
                work_item=item,
                reason="Discovery schedule disabled",
                now=now,
            )
    db.flush()


def _mark_work_skipped(
    *,
    work_item: ScheduledWorkItemModel,
    reason: str,
    now: datetime,
) -> None:
    """Record a terminal non-executed outcome using the existing status contract."""
    work_item.status = ScheduledWorkStatus.SKIPPED.value
    work_item.error_message = reason
    work_item.completed_at = now


def _discovery_schedule_key(
    *,
    podcast_id: int,
) -> str:
    """Build the recurring discovery schedule key for a podcast."""
    return f"discovery:podcast:{podcast_id}"


def _is_discovery_metadata(
    *,
    metadata: dict[str, object] | None,
) -> bool:
    """Check whether scheduled work metadata describes episode discovery."""
    return bool(metadata and metadata.get("kind") == DISCOVERY_WORK_KIND)


def _mark_work_failed(
    *,
    work_item: ScheduledWorkItemModel,
    error_message: str,
) -> None:
    """Mark a scheduled work item as failed."""
    work_item.status = ScheduledWorkStatus.FAILED.value
    work_item.error_message = error_message
    work_item.completed_at = datetime.now(UTC)


def _run_discovery_orchestrator(
    db: Session,
    podcast: Podcast,
) -> DiscoveryResult:
    """Run the existing discovery orchestrator for a podcast."""
    return DiscoveryOrchestrator(db).discover_for_podcast(podcast=podcast)

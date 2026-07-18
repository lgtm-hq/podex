"""Recurring episode discovery scheduling and execution services."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from podex.models import (
    IngestionRun,
    Podcast,
    PodcastStatus,
    ScheduledWorkItemModel,
    ScheduledWorkStatus,
)
from podex.services.discovery import DiscoveryResult
from podex.services.discovery_orchestrator import DiscoveryOrchestrator
from podex.services.scheduled_work import (
    PipelineScheduleInputData,
    PipelineScheduleSummaryData,
    plan_due_scheduled_work,
    upsert_pipeline_schedule,
)
from podex.services.scheduler import ScheduledTaskKind

DISCOVERY_WORK_KIND = "episode_discovery"


@dataclass(frozen=True, slots=True)
class DiscoveryScheduleResultData:
    """Result of reconciling recurring discovery schedules."""

    schedules: list[PipelineScheduleSummaryData]


@dataclass(frozen=True, slots=True)
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
    """Ensure active podcasts have recurring episode discovery schedules.

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

    Args:
        db: Database session.
        runner: Optional discovery runner for tests or alternate execution.
        limit: Maximum pending work items to run.

    Returns:
        Execution summaries.
    """
    effective_runner = runner or _run_discovery_orchestrator
    pending_items = (
        db.query(ScheduledWorkItemModel)
        .filter(ScheduledWorkItemModel.status == ScheduledWorkStatus.PENDING.value)
        .filter(ScheduledWorkItemModel.task_kind == ScheduledTaskKind.INGESTION.value)
        .order_by(ScheduledWorkItemModel.due_at.asc(), ScheduledWorkItemModel.id.asc())
        .limit(limit)
        .all()
    )
    results: list[DiscoveryWorkRunData] = []

    for work_item in pending_items:
        if not _is_discovery_metadata(metadata=work_item.metadata_json):
            continue

        metadata = work_item.metadata_json or {}
        podcast_id = metadata.get("podcast_id")
        if not isinstance(podcast_id, int):
            _mark_work_failed(work_item=work_item, error_message="Missing podcast_id")
            continue

        podcast = db.query(Podcast).filter(Podcast.id == podcast_id).first()
        if podcast is None:
            _mark_work_failed(work_item=work_item, error_message="Podcast not found")
            continue

        run = IngestionRun(
            status="in_progress",
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
        except Exception as exc:
            run.status = "failed"
            run.error_summary = str(exc)
            run.completed_at = datetime.now(UTC)
            _mark_work_failed(work_item=work_item, error_message=str(exc))
            continue

        completed_at = datetime.now(UTC)
        run.status = "completed" if not discovery_result.errors else "failed"
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

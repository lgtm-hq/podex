"""Shared search projection repair services for ops surfaces."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from podex.models import Mention, SearchProjectionRepair
from podex.models.search_projection_repair import (
    SearchProjectionRepairReason,
    SearchProjectionRepairResourceType,
    SearchProjectionRepairStatus,
)


@dataclass(frozen=True, slots=True)
class SearchProjectionRepairSummaryData:
    """Shared projection repair summary payload."""

    id: int
    resource_type: SearchProjectionRepairResourceType
    resource_id: int
    status: SearchProjectionRepairStatus
    reason: SearchProjectionRepairReason
    source_job_id: int | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class SearchProjectionRepairCountsData:
    """Aggregate projection repair counts for ops surfaces."""

    pending: int
    failed: int
    completed: int


def _merge_metadata(
    *,
    existing: dict[str, Any] | None,
    incoming: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Merge repair metadata dictionaries.

    Args:
        existing: Existing persisted metadata.
        incoming: Newly supplied metadata.

    Returns:
        Merged metadata when any values exist.
    """
    if existing is None and incoming is None:
        return None

    return {
        **(existing or {}),
        **(incoming or {}),
    }


def _to_search_projection_repair_summary(
    *,
    repair: SearchProjectionRepair,
) -> SearchProjectionRepairSummaryData:
    """Build a shared repair summary payload.

    Args:
        repair: Projection repair model instance.

    Returns:
        Shared repair summary payload.
    """
    return SearchProjectionRepairSummaryData(
        id=repair.id,
        resource_type=SearchProjectionRepairResourceType(repair.resource_type),
        resource_id=repair.resource_id,
        status=SearchProjectionRepairStatus(repair.status),
        reason=SearchProjectionRepairReason(repair.reason),
        source_job_id=repair.source_job_id,
        error_message=repair.error_message,
        created_at=repair.created_at,
        updated_at=repair.updated_at,
        completed_at=repair.completed_at,
    )


def _get_open_projection_repair(
    *,
    db: Session,
    resource_type: SearchProjectionRepairResourceType,
    resource_id: int,
) -> SearchProjectionRepair | None:
    """Load the latest open repair for a resource.

    Args:
        db: Database session.
        resource_type: Resource type that needs repair.
        resource_id: Internal resource identifier.

    Returns:
        Open repair record when found.
    """
    return (
        db.query(SearchProjectionRepair)
        .filter(SearchProjectionRepair.resource_type == resource_type.value)
        .filter(SearchProjectionRepair.resource_id == resource_id)
        .filter(
            SearchProjectionRepair.status.in_(
                [
                    SearchProjectionRepairStatus.PENDING.value,
                    SearchProjectionRepairStatus.FAILED.value,
                ]
            )
        )
        .order_by(
            SearchProjectionRepair.updated_at.desc(),
            SearchProjectionRepair.id.desc(),
        )
        .first()
    )


def ensure_search_projection_repair(
    *,
    db: Session,
    resource_type: SearchProjectionRepairResourceType,
    resource_id: int,
    reason: SearchProjectionRepairReason,
    status: SearchProjectionRepairStatus,
    source_job_id: int | None = None,
    error_message: str | None = None,
    metadata_json: dict[str, Any] | None = None,
) -> SearchProjectionRepair:
    """Create or update a replay-safe projection repair record.

    Args:
        db: Database session.
        resource_type: Resource type that needs repair.
        resource_id: Internal resource identifier.
        reason: Why the repair was opened.
        status: Current repair state.
        source_job_id: Optional triggering transcription job.
        error_message: Optional latest failure message.
        metadata_json: Optional repair metadata.

    Returns:
        Persisted projection repair model.
    """
    now = datetime.now(UTC)
    repair = _get_open_projection_repair(
        db=db,
        resource_type=resource_type,
        resource_id=resource_id,
    )
    if repair is None:
        repair = SearchProjectionRepair(
            resource_type=resource_type.value,
            resource_id=resource_id,
            reason=reason.value,
            status=status.value,
            source_job_id=source_job_id,
            error_message=error_message,
            metadata_json=metadata_json,
            last_attempted_at=(
                now
                if status is SearchProjectionRepairStatus.FAILED
                or status is SearchProjectionRepairStatus.COMPLETED
                else None
            ),
            completed_at=(
                now if status is SearchProjectionRepairStatus.COMPLETED else None
            ),
            created_at=now,
            updated_at=now,
        )
        db.add(repair)
        db.flush()
        return repair

    repair.status = status.value
    repair.source_job_id = source_job_id or repair.source_job_id
    repair.error_message = error_message
    repair.metadata_json = _merge_metadata(
        existing=repair.metadata_json,
        incoming=metadata_json,
    )
    repair.updated_at = now
    if status is SearchProjectionRepairStatus.COMPLETED:
        repair.completed_at = now
        repair.last_attempted_at = now
        repair.error_message = None
    elif status is SearchProjectionRepairStatus.FAILED:
        repair.last_attempted_at = now
        repair.completed_at = None
    else:
        repair.completed_at = None
    db.flush()
    return repair


def mark_search_projection_repair_completed(
    *,
    db: Session,
    resource_type: SearchProjectionRepairResourceType,
    resource_id: int,
) -> None:
    """Mark any open repairs for a resource as completed.

    Args:
        db: Database session.
        resource_type: Resource type that was resynced.
        resource_id: Internal resource identifier.
    """
    repair = _get_open_projection_repair(
        db=db,
        resource_type=resource_type,
        resource_id=resource_id,
    )
    if repair is None:
        return

    ensure_search_projection_repair(
        db=db,
        resource_type=resource_type,
        resource_id=resource_id,
        reason=SearchProjectionRepairReason(repair.reason),
        status=SearchProjectionRepairStatus.COMPLETED,
        source_job_id=repair.source_job_id,
        metadata_json=repair.metadata_json,
    )


def enqueue_extract_rerun_projection_repairs(
    *,
    db: Session,
    episode_id: int,
    source_job_id: int,
) -> list[SearchProjectionRepairSummaryData]:
    """Open replay-safe projection repairs for an extract rerun.

    Args:
        db: Database session.
        episode_id: Episode being rerun.
        source_job_id: Triggered extraction job identifier.

    Returns:
        Repair summaries opened or refreshed for this rerun.
    """
    media_ids = [
        media_id
        for media_id, in (
            db.query(Mention.media_id)
            .filter(Mention.episode_id == episode_id)
            .group_by(Mention.media_id)
            .all()
        )
        if media_id is not None
    ]
    if not media_ids:
        return []

    repairs = [
        ensure_search_projection_repair(
            db=db,
            resource_type=SearchProjectionRepairResourceType.EPISODE,
            resource_id=episode_id,
            reason=SearchProjectionRepairReason.EXTRACT_RERUN,
            status=SearchProjectionRepairStatus.PENDING,
            source_job_id=source_job_id,
            metadata_json={"episode_id": episode_id},
        )
    ]
    repairs.extend(
        ensure_search_projection_repair(
            db=db,
            resource_type=SearchProjectionRepairResourceType.MEDIA,
            resource_id=media_id,
            reason=SearchProjectionRepairReason.EXTRACT_RERUN,
            status=SearchProjectionRepairStatus.PENDING,
            source_job_id=source_job_id,
            metadata_json={"episode_id": episode_id},
        )
        for media_id in media_ids
    )
    return [_to_search_projection_repair_summary(repair=repair) for repair in repairs]


def get_search_projection_repair_counts(
    *,
    db: Session,
) -> SearchProjectionRepairCountsData:
    """Count projection repair records by status.

    Args:
        db: Database session.

    Returns:
        Aggregate projection repair counts.
    """
    rows = dict(
        db.query(
            SearchProjectionRepair.status,
            func.count(SearchProjectionRepair.id),
        )
        .group_by(SearchProjectionRepair.status)
        .all()
    )
    return SearchProjectionRepairCountsData(
        pending=int(rows.get(SearchProjectionRepairStatus.PENDING.value, 0) or 0),
        failed=int(rows.get(SearchProjectionRepairStatus.FAILED.value, 0) or 0),
        completed=int(rows.get(SearchProjectionRepairStatus.COMPLETED.value, 0) or 0),
    )


def list_recent_search_projection_repairs(
    *,
    db: Session,
    limit: int,
) -> list[SearchProjectionRepairSummaryData]:
    """List recent projection repair records for ops views.

    Args:
        db: Database session.
        limit: Maximum number of repairs to return.

    Returns:
        Recent repair summaries ordered by recency.
    """
    repairs = (
        db.query(SearchProjectionRepair)
        .order_by(
            SearchProjectionRepair.updated_at.desc(),
            SearchProjectionRepair.id.desc(),
        )
        .limit(limit)
        .all()
    )
    return [_to_search_projection_repair_summary(repair=repair) for repair in repairs]

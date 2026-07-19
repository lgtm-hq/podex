"""Shared pipeline and job query services for ops surfaces."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from podex.models import IngestionRun


@dataclass(frozen=True, slots=True)
class IngestionRunSummaryData:
    """Shared ingestion run summary payload."""

    id: int
    status: str
    error_summary: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    duration_seconds: int | None


def calculate_duration_seconds(
    *,
    started_at: datetime | None,
    completed_at: datetime | None,
) -> int | None:
    """Calculate whole-second duration for a completed run or job.

    Args:
        started_at: Start timestamp.
        completed_at: Completion timestamp.

    Returns:
        Duration in seconds when both timestamps are present.
    """
    if started_at is None or completed_at is None:
        return None

    return int((completed_at - started_at).total_seconds())


def _to_ingestion_run_summary_data(
    *,
    run: IngestionRun,
) -> IngestionRunSummaryData:
    """Build a shared ingestion run summary payload.

    Args:
        run: Ingestion run model instance.

    Returns:
        Shared ingestion run summary payload.
    """
    return IngestionRunSummaryData(
        id=run.id,
        status=run.status,
        error_summary=run.error_summary,
        started_at=run.started_at,
        completed_at=run.completed_at,
        created_at=run.created_at,
        duration_seconds=calculate_duration_seconds(
            started_at=run.started_at,
            completed_at=run.completed_at,
        ),
    )


def get_ingestion_run_by_id(
    *,
    db: Session,
    ingestion_run_id: int,
) -> IngestionRunSummaryData | None:
    """Get a single ingestion run summary for ops surfaces.

    Args:
        db: Database session.
        ingestion_run_id: Internal ingestion run identifier.

    Returns:
        Ingestion run summary when found, otherwise ``None``.
    """
    run = db.query(IngestionRun).filter(IngestionRun.id == ingestion_run_id).first()
    if run is None:
        return None
    return _to_ingestion_run_summary_data(run=run)


def list_recent_ingestion_runs(
    *,
    db: Session,
    limit: int,
) -> list[IngestionRunSummaryData]:
    """List recent ingestion runs for ops views.

    Args:
        db: Database session.
        limit: Maximum number of runs to return.

    Returns:
        Recent ingestion run summaries ordered by recency.
    """
    runs = (
        db.query(IngestionRun)
        .order_by(IngestionRun.created_at.desc(), IngestionRun.id.desc())
        .limit(limit)
        .all()
    )
    return [_to_ingestion_run_summary_data(run=run) for run in runs]

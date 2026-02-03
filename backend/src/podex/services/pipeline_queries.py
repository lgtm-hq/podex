"""Shared pipeline and job query services for ops surfaces."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from podex.models import Episode, IngestionRun, Podcast, TranscriptionJob


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


@dataclass(frozen=True, slots=True)
class TranscriptionJobSummaryData:
    """Shared transcription job summary payload."""

    id: int
    episode_id: int
    episode_title: str
    podcast_id: int
    podcast_name: str
    podcast_slug: str
    job_type: str
    status: str
    backend: str | None
    model: str | None
    error_message: str | None
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


def _to_transcription_job_summary_data(
    *,
    job: TranscriptionJob,
    episode_title: str,
    podcast_id: int,
    podcast_name: str,
    podcast_slug: str,
) -> TranscriptionJobSummaryData:
    """Build a shared transcription job summary payload.

    Args:
        job: Transcription job model instance.
        episode_title: Episode title.
        podcast_id: Podcast identifier.
        podcast_name: Podcast name.
        podcast_slug: Podcast slug.

    Returns:
        Shared transcription job summary payload.
    """
    return TranscriptionJobSummaryData(
        id=job.id,
        episode_id=job.episode_id,
        episode_title=episode_title,
        podcast_id=podcast_id,
        podcast_name=podcast_name,
        podcast_slug=podcast_slug,
        job_type=job.job_type,
        status=job.status,
        backend=job.backend,
        model=job.model,
        error_message=job.error_message,
        started_at=job.started_at,
        completed_at=job.completed_at,
        created_at=job.created_at,
        duration_seconds=calculate_duration_seconds(
            started_at=job.started_at,
            completed_at=job.completed_at,
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


def list_transcription_jobs_by_ids(
    *,
    db: Session,
    job_ids: list[int],
) -> list[TranscriptionJobSummaryData]:
    """List specific transcription jobs with episode and podcast context.

    Args:
        db: Database session.
        job_ids: Internal transcription job identifiers.

    Returns:
        Matching transcription job summaries ordered by creation.
    """
    if not job_ids:
        return []

    rows = (
        db.query(
            TranscriptionJob,
            Episode.title,
            Podcast.id,
            Podcast.name,
            Podcast.slug,
        )
        .join(Episode, Episode.id == TranscriptionJob.episode_id)
        .join(Podcast, Podcast.id == Episode.podcast_id)
        .filter(TranscriptionJob.id.in_(job_ids))
        .order_by(TranscriptionJob.created_at.asc(), TranscriptionJob.id.asc())
        .all()
    )

    return [
        _to_transcription_job_summary_data(
            job=job,
            episode_title=episode_title,
            podcast_id=podcast_id,
            podcast_name=podcast_name,
            podcast_slug=podcast_slug,
        )
        for job, episode_title, podcast_id, podcast_name, podcast_slug in rows
    ]


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


def list_recent_transcription_jobs(
    *,
    db: Session,
    limit: int,
) -> list[TranscriptionJobSummaryData]:
    """List recent transcription jobs with episode and podcast context.

    Args:
        db: Database session.
        limit: Maximum number of jobs to return.

    Returns:
        Recent transcription job summaries ordered by recency.
    """
    rows = (
        db.query(
            TranscriptionJob,
            Episode.title,
            Podcast.id,
            Podcast.name,
            Podcast.slug,
        )
        .join(Episode, Episode.id == TranscriptionJob.episode_id)
        .join(Podcast, Podcast.id == Episode.podcast_id)
        .order_by(TranscriptionJob.created_at.desc(), TranscriptionJob.id.desc())
        .limit(limit)
        .all()
    )

    return [
        _to_transcription_job_summary_data(
            job=job,
            episode_title=episode_title,
            podcast_id=podcast_id,
            podcast_name=podcast_name,
            podcast_slug=podcast_slug,
        )
        for job, episode_title, podcast_id, podcast_name, podcast_slug in rows
    ]

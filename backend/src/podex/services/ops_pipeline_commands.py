"""Shared pipeline mutation services for ops surfaces."""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from podex.models import Episode, IngestionRun, JobStatus, JobType, TranscriptionJob
from podex.services.pipeline_queries import (
    IngestionRunSummaryData,
    TranscriptionJobSummaryData,
    get_ingestion_run_by_id,
    list_transcription_jobs_by_ids,
)


def create_ops_ingestion_run(
    *,
    db: Session,
) -> IngestionRunSummaryData:
    """Create a new ingestion run for ops-triggered pipeline execution.

    Args:
        db: Database session.

    Returns:
        Created ingestion run summary.

    Raises:
        RuntimeError: If the created run cannot be reloaded.
    """
    run = IngestionRun(
        status="in_progress",
        started_at=datetime.now(UTC),
    )
    db.add(run)
    db.flush()

    created_run = get_ingestion_run_by_id(db=db, ingestion_run_id=run.id)
    if created_run is None:
        raise RuntimeError("Created ingestion run could not be reloaded")
    return created_run


def rerun_episode_processing_jobs(
    *,
    db: Session,
    episode_id: int,
    job_types: tuple[JobType, ...],
) -> list[TranscriptionJobSummaryData] | None:
    """Queue processing jobs for an episode rerun.

    Args:
        db: Database session.
        episode_id: Internal episode identifier.
        job_types: Ordered job types to enqueue.

    Returns:
        Created job summaries when the episode exists, otherwise ``None``.

    Raises:
        RuntimeError: If the created jobs cannot be reloaded.
    """
    episode = db.query(Episode).filter(Episode.id == episode_id).first()
    if episode is None:
        return None

    job_ids: list[int] = []
    for job_type in job_types:
        if job_type is JobType.TRANSCRIBE:
            episode.transcript_status = JobStatus.PENDING.value
        elif job_type is JobType.EXTRACT:
            episode.extraction_status = JobStatus.PENDING.value
        else:
            episode.cleanup_status = JobStatus.PENDING.value

        job = TranscriptionJob(
            episode_id=episode.id,
            job_type=job_type.value,
            status=JobStatus.PENDING,
        )
        db.add(job)
        db.flush()
        job_ids.append(job.id)

    created_jobs = list_transcription_jobs_by_ids(db=db, job_ids=job_ids)
    if len(created_jobs) != len(job_ids):
        raise RuntimeError("Created transcription jobs could not be reloaded")

    return created_jobs

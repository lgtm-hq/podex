"""Shared status and dashboard queries."""

from sqlalchemy import func
from sqlalchemy.orm import Session

from podex.models import (
    Episode,
    IngestionRun,
    JobStatus,
    Podcast,
    PodcastStatus,
    TranscriptionJob,
)


def get_podcast_status_counts(
    *,
    db: Session,
) -> dict[str, int]:
    """Get podcast counts by status.

    Args:
        db: Database session.

    Returns:
        Podcast counts keyed by status and total.
    """
    return {
        "total": db.query(func.count(Podcast.id)).scalar() or 0,
        "active": db.query(func.count(Podcast.id))
        .filter(Podcast.status == PodcastStatus.ACTIVE)
        .scalar()
        or 0,
        "watchlist": db.query(func.count(Podcast.id))
        .filter(Podcast.status == PodcastStatus.WATCHLIST)
        .scalar()
        or 0,
        "paused": db.query(func.count(Podcast.id))
        .filter(Podcast.status == PodcastStatus.PAUSED)
        .scalar()
        or 0,
    }


def get_source_coverage_counts(
    *,
    db: Session,
) -> dict[str, int]:
    """Get configured source counts across podcasts.

    Args:
        db: Database session.

    Returns:
        Counts keyed by source type.
    """
    return {
        "with_rss": db.query(func.count(Podcast.id))
        .filter(Podcast.rss_url.isnot(None))
        .scalar()
        or 0,
        "with_spotify": db.query(func.count(Podcast.id))
        .filter(Podcast.spotify_id.isnot(None))
        .scalar()
        or 0,
        "with_podscripts": db.query(func.count(Podcast.id))
        .filter(Podcast.podscripts_slug.isnot(None))
        .scalar()
        or 0,
        "with_youtube": db.query(func.count(Podcast.id))
        .filter(Podcast.youtube_channel_id.isnot(None))
        .scalar()
        or 0,
    }


def get_episode_processing_counts(
    *,
    db: Session,
) -> dict[str, int]:
    """Get episode processing counts.

    Args:
        db: Database session.

    Returns:
        Counts for total, transcribed, and extracted episodes.
    """
    return {
        "total_known": db.query(func.count(Episode.id)).scalar() or 0,
        "transcribed": db.query(func.count(Episode.id))
        .filter(Episode.transcript_status == "completed")
        .scalar()
        or 0,
        "extracted": db.query(func.count(Episode.id))
        .filter(Episode.extraction_status == "completed")
        .scalar()
        or 0,
    }


def get_ingestion_run_counts(
    *,
    db: Session,
) -> dict[str, int]:
    """Get ingestion run counts by status.

    Args:
        db: Database session.

    Returns:
        Counts for total, in progress, failed, and completed runs.
    """
    return {
        "total": db.query(func.count(IngestionRun.id)).scalar() or 0,
        "in_progress": db.query(func.count(IngestionRun.id))
        .filter(IngestionRun.status == "in_progress")
        .scalar()
        or 0,
        "failed": db.query(func.count(IngestionRun.id))
        .filter(IngestionRun.status == "failed")
        .scalar()
        or 0,
        "completed": db.query(func.count(IngestionRun.id))
        .filter(IngestionRun.status == "completed")
        .scalar()
        or 0,
    }


def get_transcription_job_counts(
    *,
    db: Session,
) -> dict[str, int]:
    """Get transcription job counts by status.

    Args:
        db: Database session.

    Returns:
        Counts for pending, failed, and in-progress jobs.
    """
    return {
        "pending": db.query(func.count(TranscriptionJob.id))
        .filter(TranscriptionJob.status == JobStatus.PENDING)
        .scalar()
        or 0,
        "failed": db.query(func.count(TranscriptionJob.id))
        .filter(TranscriptionJob.status == JobStatus.FAILED)
        .scalar()
        or 0,
        "in_progress": db.query(func.count(TranscriptionJob.id))
        .filter(TranscriptionJob.status == JobStatus.IN_PROGRESS)
        .scalar()
        or 0,
    }

"""Status API endpoints for discovery and processing status."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from podex.database import get_db
from podex.models import Episode, Podcast, Transcript
from podex.services.status_queries import (
    get_episode_processing_counts,
    get_podcast_status_counts,
    get_source_coverage_counts,
)

router = APIRouter(prefix="/status", tags=["status"])


class SourceStatus(BaseModel):
    """Status of a discovery source."""

    configured: bool
    last_sync: str | None = None


class DiscoverySourcesStatus(BaseModel):
    """Status of all discovery sources for a podcast."""

    podscripts: SourceStatus
    rss: SourceStatus
    spotify: SourceStatus
    youtube: SourceStatus


class EpisodesBySource(BaseModel):
    """Episode counts by discovery source."""

    podscripts: int = 0
    rss: int = 0
    youtube: int = 0
    spotify: int = 0
    manual: int = 0


class TranscriptsByProvider(BaseModel):
    """Transcript counts by provider."""

    total: int = 0
    by_provider: dict[str, int] = {}
    pending: int = 0


class ExtractionStatus(BaseModel):
    """Extraction status counts."""

    completed: int = 0
    pending: int = 0
    failed: int = 0


class PodcastStatusResponse(BaseModel):
    """Full status for a podcast."""

    podcast: str
    slug: str
    status: str
    discovery_sources: DiscoverySourcesStatus
    episodes: dict[str, Any]
    transcripts: TranscriptsByProvider
    extraction: ExtractionStatus


class GlobalStatusResponse(BaseModel):
    """Global system status."""

    podcasts: dict[str, int]
    episodes: dict[str, int]


@router.get("/discovery", response_model=GlobalStatusResponse)
def get_global_status(db: Session = Depends(get_db)) -> GlobalStatusResponse:
    """Get global discovery and processing status."""
    podcast_counts = get_podcast_status_counts(db=db)
    source_counts = get_source_coverage_counts(db=db)
    episode_counts = get_episode_processing_counts(db=db)

    return GlobalStatusResponse(
        podcasts={
            "total": podcast_counts["total"],
            "with_rss": source_counts["with_rss"],
            "with_spotify": source_counts["with_spotify"],
            "with_podscripts": source_counts["with_podscripts"],
        },
        episodes={
            "total_known": episode_counts["total_known"],
            "transcribed": episode_counts["transcribed"],
            "extracted": episode_counts["extracted"],
        },
    )


@router.get("/podcasts/{slug}", response_model=PodcastStatusResponse)
def get_podcast_status(
    slug: str,
    db: Session = Depends(get_db),
) -> PodcastStatusResponse:
    """Get detailed status for a specific podcast."""
    podcast = db.query(Podcast).filter(Podcast.slug == slug).first()
    if not podcast:
        raise HTTPException(status_code=404, detail="Podcast not found")

    # Discovery sources status
    discovery_sources = DiscoverySourcesStatus(
        podscripts=SourceStatus(configured=bool(podcast.podscripts_slug)),
        rss=SourceStatus(configured=bool(podcast.rss_url)),
        spotify=SourceStatus(configured=bool(podcast.spotify_id)),
        youtube=SourceStatus(configured=bool(podcast.youtube_channel_id)),
    )

    # Episode counts
    base_query = db.query(Episode).filter(Episode.podcast_id == podcast.id)
    total_episodes = base_query.count()

    # Episodes by discovery source
    episodes_by_source = {}
    for source in ["podscripts", "rss", "youtube", "spotify", "manual"]:
        count = base_query.filter(Episode.discovery_source == source).count()
        if count > 0:
            episodes_by_source[source] = count

    # Transcript stats
    transcripts_completed = base_query.filter(
        Episode.transcript_status == "completed"
    ).count()
    transcripts_pending = base_query.filter(
        Episode.transcript_status == "pending"
    ).count()

    # Get transcript providers
    transcript_providers = (
        db.query(Transcript.provider, func.count(Transcript.id))
        .join(Episode)
        .filter(Episode.podcast_id == podcast.id)
        .group_by(Transcript.provider)
        .all()
    )

    transcripts = TranscriptsByProvider(
        total=transcripts_completed,
        by_provider={provider: count for provider, count in transcript_providers},
        pending=transcripts_pending,
    )

    # Extraction stats
    extraction = ExtractionStatus(
        completed=base_query.filter(Episode.extraction_status == "completed").count(),
        pending=base_query.filter(Episode.extraction_status == "pending").count(),
        failed=base_query.filter(Episode.extraction_status == "failed").count(),
    )

    return PodcastStatusResponse(
        podcast=podcast.name,
        slug=podcast.slug,
        status=podcast.status,
        discovery_sources=discovery_sources,
        episodes={
            "total_known": total_episodes,
            "by_discovery_source": episodes_by_source,
        },
        transcripts=transcripts,
        extraction=extraction,
    )

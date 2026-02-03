"""Shared podcast management mutation services for ops surfaces."""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from podex.models import Podcast, PodcastStatus
from podex.services.ops_podcast_queries import (
    OpsPodcastSummaryData,
    get_ops_podcast_by_id,
)


@dataclass(frozen=True, slots=True)
class OpsPodcastSourceInputData:
    """Source fields provided for ops podcast mutations."""

    rss_url: str | None = None
    spotify_id: str | None = None
    apple_id: str | None = None
    youtube_channel_id: str | None = None
    podscripts_slug: str | None = None


@dataclass(frozen=True, slots=True)
class CreateOpsPodcastInputData:
    """Required and optional fields for podcast creation."""

    name: str
    slug: str
    status: PodcastStatus
    description: str | None = None
    cover_url: str | None = None
    discovery_source: str | None = None
    sources: OpsPodcastSourceInputData = OpsPodcastSourceInputData()


@dataclass(frozen=True, slots=True)
class UpdateOpsPodcastInputData:
    """Partial fields accepted for podcast updates."""

    provided_fields: frozenset[str] = frozenset()
    source_fields: frozenset[str] = frozenset()
    name: str | None = None
    slug: str | None = None
    status: PodcastStatus | None = None
    description: str | None = None
    cover_url: str | None = None
    discovery_source: str | None = None
    sources: OpsPodcastSourceInputData | None = None


def create_ops_podcast(
    *,
    db: Session,
    payload: CreateOpsPodcastInputData,
) -> OpsPodcastSummaryData:
    """Create a podcast for ops catalog management.

    Args:
        db: Database session.
        payload: Podcast creation payload.

    Returns:
        Created podcast summary.

    Raises:
        RuntimeError: If the created podcast cannot be reloaded.
    """
    podcast = Podcast(
        name=payload.name,
        slug=payload.slug,
        status=payload.status.value,
        description=payload.description,
        cover_url=payload.cover_url,
        discovery_source=payload.discovery_source,
        rss_url=payload.sources.rss_url,
        spotify_id=payload.sources.spotify_id,
        apple_id=payload.sources.apple_id,
        youtube_channel_id=payload.sources.youtube_channel_id,
        podscripts_slug=payload.sources.podscripts_slug,
    )
    db.add(podcast)
    db.flush()

    created_podcast = get_ops_podcast_by_id(db=db, podcast_id=podcast.id)
    if created_podcast is None:
        raise RuntimeError("Created podcast could not be reloaded")
    return created_podcast


def update_ops_podcast(
    *,
    db: Session,
    podcast_id: int,
    payload: UpdateOpsPodcastInputData,
) -> OpsPodcastSummaryData | None:
    """Update a podcast for ops catalog management.

    Args:
        db: Database session.
        podcast_id: Internal podcast identifier.
        payload: Partial update payload.

    Returns:
        Updated podcast summary when found, otherwise ``None``.

    Raises:
        RuntimeError: If the updated podcast cannot be reloaded.
    """
    podcast = db.query(Podcast).filter(Podcast.id == podcast_id).first()
    if podcast is None:
        return None

    if "name" in payload.provided_fields:
        podcast.name = payload.name
    if "slug" in payload.provided_fields:
        podcast.slug = payload.slug
    if "status" in payload.provided_fields:
        podcast.status = payload.status.value
    if "description" in payload.provided_fields:
        podcast.description = payload.description
    if "cover_url" in payload.provided_fields:
        podcast.cover_url = payload.cover_url
    if "discovery_source" in payload.provided_fields:
        podcast.discovery_source = payload.discovery_source

    if payload.sources is not None:
        if "rss_url" in payload.source_fields:
            podcast.rss_url = payload.sources.rss_url
        if "spotify_id" in payload.source_fields:
            podcast.spotify_id = payload.sources.spotify_id
        if "apple_id" in payload.source_fields:
            podcast.apple_id = payload.sources.apple_id
        if "youtube_channel_id" in payload.source_fields:
            podcast.youtube_channel_id = payload.sources.youtube_channel_id
        if "podscripts_slug" in payload.source_fields:
            podcast.podscripts_slug = payload.sources.podscripts_slug

    db.flush()

    updated_podcast = get_ops_podcast_by_id(db=db, podcast_id=podcast.id)
    if updated_podcast is None:
        raise RuntimeError("Updated podcast could not be reloaded")
    return updated_podcast


def archive_ops_podcast(
    *,
    db: Session,
    podcast_id: int,
) -> OpsPodcastSummaryData | None:
    """Archive a podcast for ops catalog management.

    Args:
        db: Database session.
        podcast_id: Internal podcast identifier.

    Returns:
        Archived podcast summary when found, otherwise ``None``.

    Raises:
        RuntimeError: If the archived podcast cannot be reloaded.
    """
    podcast = db.query(Podcast).filter(Podcast.id == podcast_id).first()
    if podcast is None:
        return None

    podcast.status = PodcastStatus.PAUSED.value
    db.flush()

    archived_podcast = get_ops_podcast_by_id(db=db, podcast_id=podcast.id)
    if archived_podcast is None:
        raise RuntimeError("Archived podcast could not be reloaded")
    return archived_podcast

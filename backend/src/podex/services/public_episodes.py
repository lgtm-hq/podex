"""Shared public episode query services."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from podex.api.query_helpers import mention_count_by_episode_subquery
from podex.models import Episode, Mention
from podex.schemas import EpisodeListResponse, EpisodeWithStats, MentionWithMedia


@dataclass(frozen=True, slots=True)
class EpisodeDetailData:
    """Detailed public episode data shared across API surfaces."""

    id: int
    podcast_id: int
    podcast_name: str
    podcast_slug: str
    title: str
    episode_number: int | None
    youtube_id: str | None
    published_at: datetime | None
    duration_seconds: int | None
    thumbnail_url: str | None
    transcript_status: str
    extraction_status: str
    cleanup_status: str
    created_at: datetime
    mention_count: int


def build_episode_response(
    *,
    episode: Episode,
    mention_count: int,
) -> EpisodeWithStats:
    """Build a public episode response payload.

    Args:
        episode: Episode model.
        mention_count: Number of mentions for the episode.

    Returns:
        Episode payload with stats.
    """
    return EpisodeWithStats(
        id=episode.id,
        podcast_id=episode.podcast_id,
        title=episode.title,
        episode_number=episode.episode_number,
        youtube_id=episode.youtube_id,
        published_at=episode.published_at,
        duration_seconds=episode.duration_seconds,
        thumbnail_url=episode.thumbnail_url,
        transcript_status=episode.transcript_status,
        created_at=episode.created_at,
        mention_count=mention_count,
    )


def list_episodes_with_stats(
    *,
    db: Session,
    page: int,
    per_page: int,
    podcast_id: int | None = None,
) -> EpisodeListResponse:
    """List episodes with pagination and mention statistics.

    Args:
        db: Database session.
        page: Requested page number.
        per_page: Number of items per page.
        podcast_id: Optional podcast filter.

    Returns:
        Paginated episode list response.
    """
    query = db.query(Episode)
    if podcast_id is not None:
        query = query.filter(Episode.podcast_id == podcast_id)

    total = query.count()
    mention_counts = mention_count_by_episode_subquery(db)

    rows = (
        query.outerjoin(mention_counts, Episode.id == mention_counts.c.episode_id)
        .add_columns(
            func.coalesce(mention_counts.c.mention_count, 0).label("mention_count")
        )
        .order_by(Episode.published_at.desc(), Episode.id.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    return EpisodeListResponse(
        items=[
            build_episode_response(episode=episode, mention_count=mention_count)
            for episode, mention_count in rows
        ],
        total=total,
        page=page,
        per_page=per_page,
    )


def get_episode_with_stats(
    *,
    db: Session,
    episode_id: int,
) -> EpisodeWithStats | None:
    """Get a single episode with mention statistics.

    Args:
        db: Database session.
        episode_id: Internal episode identifier.

    Returns:
        Episode payload with stats, if found.
    """
    mention_counts = mention_count_by_episode_subquery(db)
    row = (
        db.query(
            Episode,
            func.coalesce(mention_counts.c.mention_count, 0).label("mention_count"),
        )
        .outerjoin(mention_counts, Episode.id == mention_counts.c.episode_id)
        .filter(Episode.id == episode_id)
        .first()
    )
    if row is None:
        return None

    episode, mention_count = row
    return build_episode_response(episode=episode, mention_count=mention_count)


def get_episode_detail_by_id(
    *,
    db: Session,
    episode_id: int,
) -> EpisodeDetailData | None:
    """Get a detailed episode record with podcast context.

    Args:
        db: Database session.
        episode_id: Internal episode identifier.

    Returns:
        Detailed episode data, if found.
    """
    mention_counts = mention_count_by_episode_subquery(db)
    row = (
        db.query(
            Episode,
            func.coalesce(mention_counts.c.mention_count, 0).label("mention_count"),
        )
        .options(selectinload(Episode.podcast))
        .outerjoin(mention_counts, Episode.id == mention_counts.c.episode_id)
        .filter(Episode.id == episode_id)
        .first()
    )
    if row is None:
        return None

    episode, mention_count = row
    return EpisodeDetailData(
        id=episode.id,
        podcast_id=episode.podcast_id,
        podcast_name=episode.podcast.name,
        podcast_slug=episode.podcast.slug,
        title=episode.title,
        episode_number=episode.episode_number,
        youtube_id=episode.youtube_id,
        published_at=episode.published_at,
        duration_seconds=episode.duration_seconds,
        thumbnail_url=episode.thumbnail_url,
        transcript_status=episode.transcript_status,
        extraction_status=episode.extraction_status,
        cleanup_status=episode.cleanup_status,
        created_at=episode.created_at,
        mention_count=mention_count,
    )


def get_episode_mentions(
    *,
    db: Session,
    episode_id: int,
) -> list[MentionWithMedia] | None:
    """Get all mention occurrences for an episode.

    Args:
        db: Database session.
        episode_id: Internal episode identifier.

    Returns:
        Mention payloads for the episode, or ``None`` if missing.
    """
    episode = db.query(Episode).filter(Episode.id == episode_id).first()
    if episode is None:
        return None

    mentions = (
        db.query(Mention)
        .options(selectinload(Mention.media))
        .filter(Mention.episode_id == episode_id)
        .order_by(Mention.timestamp_seconds.asc(), Mention.id.asc())
        .all()
    )

    result: list[MentionWithMedia] = []
    for mention in mentions:
        youtube_url = None
        if episode.youtube_id and mention.timestamp_seconds is not None:
            youtube_url = (
                f"https://youtube.com/watch?v={episode.youtube_id}"
                f"&t={mention.timestamp_seconds}"
            )

        result.append(
            MentionWithMedia(
                id=mention.id,
                media_id=mention.media_id,
                media_title=mention.media.title,
                media_type=mention.media.type,
                media_author=mention.media.author,
                timestamp_seconds=mention.timestamp_seconds,
                context=mention.context,
                confidence=mention.confidence,
                youtube_timestamp_url=youtube_url,
            )
        )

    return result

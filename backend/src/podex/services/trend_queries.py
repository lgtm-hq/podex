"""Shared trend and stats query services for discovery surfaces."""

from dataclasses import dataclass

from sqlalchemy import func
from sqlalchemy.orm import Session

from podex.models import Episode, Media, Mention, Podcast
from podex.models.media import MediaType


@dataclass(frozen=True, slots=True)
class OverviewStatsData:
    """Aggregate catalog statistics for discovery views."""

    total_podcasts: int
    total_episodes: int
    total_media: int
    total_mentions: int
    total_books: int
    total_movies: int


@dataclass(frozen=True, slots=True)
class MediaTypeStatsData:
    """Discovery counts grouped by media type."""

    type: MediaType
    count: int
    mention_count: int


@dataclass(frozen=True, slots=True)
class TopMentionedMediaData:
    """Top mentioned media item summary for discovery surfaces."""

    id: int
    title: str
    type: MediaType
    author: str | None
    mention_count: int


@dataclass(frozen=True, slots=True)
class PublicTrendsData:
    """Combined discovery trend payload for public API surfaces."""

    overview: OverviewStatsData
    by_type: list[MediaTypeStatsData]
    top_mentioned: list[TopMentionedMediaData]


def _normalize_media_type(*, media_type: MediaType | str | None) -> str | None:
    """Normalize enum or raw media types for query filtering.

    Args:
        media_type: Optional media type filter.

    Returns:
        Normalized raw media type value when provided.
    """
    if isinstance(media_type, MediaType):
        return str(media_type.value)
    return media_type


def get_overview_stats(
    *,
    db: Session,
) -> OverviewStatsData:
    """Get aggregate catalog statistics for discovery surfaces.

    Args:
        db: Database session.

    Returns:
        Aggregate overview metrics.
    """
    return OverviewStatsData(
        total_podcasts=db.query(Podcast).count(),
        total_episodes=db.query(Episode).count(),
        total_media=db.query(Media).count(),
        total_mentions=db.query(Mention).count(),
        total_books=db.query(Media).filter(Media.type == MediaType.BOOK.value).count(),
        total_movies=db.query(Media)
        .filter(Media.type.in_([MediaType.MOVIE.value, MediaType.DOCUMENTARY.value]))
        .count(),
    )


def get_stats_by_type(
    *,
    db: Session,
) -> list[MediaTypeStatsData]:
    """Get discovery statistics grouped by media type.

    Args:
        db: Database session.

    Returns:
        Media-type summaries sorted by mention count.
    """
    type_counts = (
        db.query(
            Media.type,
            func.count(func.distinct(Media.id)).label("count"),
            func.count(Mention.id).label("mention_count"),
        )
        .outerjoin(Mention, Mention.media_id == Media.id)
        .group_by(Media.type)
        .order_by(func.count(Mention.id).desc(), Media.type.asc())
        .all()
    )

    return [
        MediaTypeStatsData(
            type=MediaType(type_name),
            count=count,
            mention_count=mention_count,
        )
        for type_name, count, mention_count in type_counts
    ]


def get_top_mentioned_media(
    *,
    db: Session,
    limit: int = 10,
    media_type: MediaType | str | None = None,
) -> list[TopMentionedMediaData]:
    """Get top mentioned media items for discovery surfaces.

    Args:
        db: Database session.
        limit: Maximum number of items to return.
        media_type: Optional media type filter.

    Returns:
        Ranked media summaries by mention count.
    """
    normalized_media_type = _normalize_media_type(media_type=media_type)

    query = (
        db.query(
            Media.id,
            Media.title,
            Media.type,
            Media.author,
            func.count(Mention.id).label("mention_count"),
        )
        .join(Mention)
        .group_by(
            Media.id,
            Media.title,
            Media.type,
            Media.author,
        )
    )

    if normalized_media_type is not None:
        query = query.filter(Media.type == normalized_media_type)

    items = (
        query.order_by(func.count(Mention.id).desc(), Media.title.asc())
        .limit(limit)
        .all()
    )

    return [
        TopMentionedMediaData(
            id=item.id,
            title=item.title,
            type=MediaType(item.type),
            author=item.author,
            mention_count=item.mention_count,
        )
        for item in items
    ]


def get_public_trends(
    *,
    db: Session,
    limit: int = 10,
    media_type: MediaType | str | None = None,
) -> PublicTrendsData:
    """Get the combined public trends payload for discovery surfaces.

    Args:
        db: Database session.
        limit: Maximum number of top-mentioned items to return.
        media_type: Optional top-mentioned media type filter.

    Returns:
        Combined trends payload.
    """
    return PublicTrendsData(
        overview=get_overview_stats(db=db),
        by_type=get_stats_by_type(db=db),
        top_mentioned=get_top_mentioned_media(
            db=db,
            limit=limit,
            media_type=media_type,
        ),
    )

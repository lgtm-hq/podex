"""Shared trend and stats query services for discovery surfaces."""

from collections.abc import Callable, Hashable
from dataclasses import dataclass
from typing import TypeVar, cast

from sqlalchemy import func
from sqlalchemy.orm import Session

from podex.config import get_settings
from podex.models import Episode, Media, Mention, Podcast
from podex.models.media import MediaType
from podex.services.read_model_cache import TtlCache

_trend_cache: TtlCache[object] = TtlCache()
CachedValueT = TypeVar("CachedValueT")


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


@dataclass(frozen=True, slots=True)
class CatalogStatsSignature:
    """Freshness signature for public catalog read-model caches."""

    podcast_count: int
    episode_count: int
    media_count: int
    mention_count: int
    latest_podcast_id: int | None
    latest_episode_id: int | None
    latest_media_id: int | None
    latest_mention_id: int | None


def clear_trend_cache() -> None:
    """Clear cached public trend and stats read models."""
    _trend_cache.clear()


def get_catalog_stats_signature(
    *,
    db: Session,
) -> CatalogStatsSignature:
    """Get a compact signature for cache invalidation.

    Args:
        db: Database session.

    Returns:
        Counts and high-water marks for catalog tables used by stats queries.
    """
    podcast_count, latest_podcast_id = db.query(
        func.count(Podcast.id),
        func.max(Podcast.id),
    ).one()
    episode_count, latest_episode_id = db.query(
        func.count(Episode.id),
        func.max(Episode.id),
    ).one()
    media_count, latest_media_id = db.query(
        func.count(Media.id),
        func.max(Media.id),
    ).one()
    mention_count, latest_mention_id = db.query(
        func.count(Mention.id),
        func.max(Mention.id),
    ).one()

    return CatalogStatsSignature(
        podcast_count=podcast_count,
        episode_count=episode_count,
        media_count=media_count,
        mention_count=mention_count,
        latest_podcast_id=latest_podcast_id,
        latest_episode_id=latest_episode_id,
        latest_media_id=latest_media_id,
        latest_mention_id=latest_mention_id,
    )


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
    signature = get_catalog_stats_signature(db=db)
    return _cached(
        key=("overview", signature),
        loader=lambda: _get_overview_stats_uncached(
            db=db,
            signature=signature,
        ),
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
    signature = get_catalog_stats_signature(db=db)
    return _cached(
        key=("by-type", signature),
        loader=lambda: _get_stats_by_type_uncached(db=db),
    )


def _get_overview_stats_uncached(
    *,
    db: Session,
    signature: CatalogStatsSignature,
) -> OverviewStatsData:
    """Get aggregate catalog statistics without reading the cache.

    Args:
        db: Database session.
        signature: Freshness signature with precomputed counts.

    Returns:
        Aggregate overview metrics.
    """
    return OverviewStatsData(
        total_podcasts=signature.podcast_count,
        total_episodes=signature.episode_count,
        total_media=signature.media_count,
        total_mentions=signature.mention_count,
        total_books=db.query(Media).filter(Media.type == MediaType.BOOK.value).count(),
        total_movies=db.query(Media)
        .filter(Media.type.in_([MediaType.MOVIE.value, MediaType.DOCUMENTARY.value]))
        .count(),
    )


def _get_stats_by_type_uncached(
    *,
    db: Session,
) -> list[MediaTypeStatsData]:
    """Get discovery statistics grouped by media type without cache lookup.

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
    signature = get_catalog_stats_signature(db=db)
    normalized_media_type = _normalize_media_type(media_type=media_type)
    return _cached(
        key=("top-mentioned", signature, limit, normalized_media_type),
        loader=lambda: _get_top_mentioned_media_uncached(
            db=db,
            limit=limit,
            media_type=normalized_media_type,
        ),
    )


def _get_top_mentioned_media_uncached(
    *,
    db: Session,
    limit: int,
    media_type: str | None,
) -> list[TopMentionedMediaData]:
    """Get top mentioned media without reading the cache.

    Args:
        db: Database session.
        limit: Maximum number of items to return.
        media_type: Optional normalized media type filter.

    Returns:
        Ranked media summaries by mention count.
    """
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

    if media_type is not None:
        query = query.filter(Media.type == media_type)

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
    signature = get_catalog_stats_signature(db=db)
    normalized_media_type = _normalize_media_type(media_type=media_type)
    return _cached(
        key=("public-trends", signature, limit, normalized_media_type),
        loader=lambda: PublicTrendsData(
            overview=_get_overview_stats_uncached(
                db=db,
                signature=signature,
            ),
            by_type=_get_stats_by_type_uncached(db=db),
            top_mentioned=_get_top_mentioned_media_uncached(
                db=db,
                limit=limit,
                media_type=normalized_media_type,
            ),
        ),
    )


def _cached(
    *,
    key: Hashable,
    loader: Callable[[], CachedValueT],
) -> CachedValueT:
    """Read a public trend value through the shared TTL cache.

    Args:
        key: Cache key containing query shape and catalog freshness.
        loader: Callable used to compute cache misses.

    Returns:
        Cached or newly computed read model value.
    """
    value = _trend_cache.get_or_set(
        key=key,
        ttl_seconds=get_settings().stats_cache_ttl_seconds,
        loader=loader,
    )
    return cast("CachedValueT", value)

"""Aggregate/statistics queries with a read-through cache.

The uncached helper :func:`compute_catalog_stats` runs the raw ``COUNT``
aggregations against SQLAlchemy; the cached helper
:func:`get_catalog_stats` wraps it in a :class:`~podex.services.cache.Cache`
lookup keyed by :data:`CATALOG_STATS_CACHE_KEY`.

Cache invalidation is TTL-based only for now — write-time invalidation hooks
will be added once the ingestion pipeline lands.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from podex.models import Episode, Media, Mention, Podcast
from podex.schemas.stats import CatalogStats, MediaTypeCount
from podex.services.cache import Cache

CATALOG_STATS_CACHE_KEY = "catalog:stats"
"""Cache key under which the aggregate catalog stats are stored."""

_TOP_MEDIA_TYPES_LIMIT = 5


def compute_catalog_stats(db: Session) -> CatalogStats:
    """Compute catalog-wide counters directly from the database.

    Args:
        db: Active SQLAlchemy session.

    Returns:
        A fresh :class:`CatalogStats` payload with counts and a small
        top-media-types breakdown.
    """
    podcasts = db.execute(select(func.count()).select_from(Podcast)).scalar_one()
    episodes = db.execute(select(func.count()).select_from(Episode)).scalar_one()
    media = db.execute(select(func.count()).select_from(Media)).scalar_one()
    mentions = db.execute(select(func.count()).select_from(Mention)).scalar_one()

    # Ties are broken alphabetically by type name so responses are stable
    # regardless of insertion order.
    top_rows = db.execute(
        select(Media.type, func.count().label("count"))
        .group_by(Media.type)
        .order_by(func.count().desc(), Media.type.asc())
        .limit(_TOP_MEDIA_TYPES_LIMIT),
    ).all()
    top_media_types = [
        MediaTypeCount(media_type=str(row_type), count=int(row_count))
        for row_type, row_count in top_rows
    ]

    return CatalogStats(
        podcasts=int(podcasts),
        episodes=int(episodes),
        media=int(media),
        mentions=int(mentions),
        top_media_types=top_media_types,
    )


def get_catalog_stats(
    db: Session,
    *,
    cache: Cache,
    ttl_seconds: float,
) -> CatalogStats:
    """Return the catalog stats, hitting the cache first.

    On a miss the value is computed from ``db`` and stored under
    :data:`CATALOG_STATS_CACHE_KEY` for ``ttl_seconds`` seconds. A
    non-positive TTL disables caching (the value is always recomputed).

    Args:
        db: Active SQLAlchemy session bound to the request.
        cache: Cache backend used for the read-through lookup.
        ttl_seconds: Maximum age of a cached response before recomputation.

    Returns:
        The (possibly cached) :class:`CatalogStats` payload.
    """
    if ttl_seconds > 0:
        cached = cache.get(CATALOG_STATS_CACHE_KEY)
        if isinstance(cached, CatalogStats):
            return cached

    fresh = compute_catalog_stats(db)
    if ttl_seconds > 0:
        cache.set(CATALOG_STATS_CACHE_KEY, fresh, ttl_seconds=ttl_seconds)
    return fresh

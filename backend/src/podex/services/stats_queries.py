"""Aggregate/statistics queries with a read-through cache.

The uncached helper :func:`compute_catalog_stats` runs the raw ``COUNT``
aggregations against SQLAlchemy; the cached helper
:func:`get_catalog_stats` wraps it in a :class:`~podex.services.cache.Cache`
lookup keyed by :data:`CATALOG_STATS_CACHE_KEY`.

Cache invalidation is TTL-based, except for suppression changes: each cached
value carries the suppression change counter it was computed under, and a
value from an older counter is recomputed. Takedowns, media merges and splits
and the backfill bump the counter, so the first read after any of them is
committed is current in every process.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from podex.models import Episode, Media, Podcast
from podex.schemas.stats import CatalogStats, MediaTypeCount
from podex.services.cache import Cache
from podex.services.content_suppression import (
    count_published_mentions,
    suppression_version,
)

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
    mentions = count_published_mentions(db)

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
    :data:`CATALOG_STATS_CACHE_KEY` for ``ttl_seconds`` seconds. The read is
    single-flight — concurrent cold-miss callers wait for the in-flight
    computation instead of stampeding the aggregation queries. A non-positive
    TTL disables caching (the value is always recomputed).

    Args:
        db: Active SQLAlchemy session bound to the request.
        cache: Cache backend used for the read-through lookup.
        ttl_seconds: Maximum age of a cached response before recomputation.

    Returns:
        The (possibly cached) :class:`CatalogStats` payload.
    """
    version = suppression_version(db)

    def load() -> _VersionedStats:
        return _VersionedStats(version=version, stats=compute_catalog_stats(db))

    cached = cache.get_or_compute(
        CATALOG_STATS_CACHE_KEY,
        ttl_seconds=ttl_seconds,
        loader=load,
    )
    if isinstance(cached, _VersionedStats) and cached.version == version:
        return cached.stats
    cache.delete(CATALOG_STATS_CACHE_KEY)
    refreshed = cache.get_or_compute(
        CATALOG_STATS_CACHE_KEY,
        ttl_seconds=ttl_seconds,
        loader=load,
    )
    return refreshed.stats if isinstance(refreshed, _VersionedStats) else load().stats


@dataclass(frozen=True, slots=True)
class _VersionedStats:
    """Cached stats and the suppression version they were computed under."""

    version: int
    stats: CatalogStats

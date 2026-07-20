"""Public read endpoint for catalog-wide statistics.

The route is intentionally thin: it pulls the shared cache and settings from
FastAPI dependencies and delegates to
:mod:`podex.services.stats_queries` for the (possibly cached) aggregate
query.
"""

from fastapi import APIRouter

from podex.api.deps import AppCache, AppSettings, DbSession
from podex.schemas.stats import CatalogStats
from podex.services import stats_queries

router = APIRouter(prefix="/stats", tags=["stats"])


def get_catalog_stats(
    db: DbSession,
    cache: AppCache,
    settings: AppSettings,
) -> CatalogStats:
    """Return catalog-wide counts and a small top-media-types breakdown.

    Responses are cached in-process for
    ``PODEX_STATS_CACHE__TTL_SECONDS`` seconds (configurable via
    :class:`~podex.config.Settings`); set the TTL to ``0`` to bypass the
    cache entirely.
    """
    return stats_queries.get_catalog_stats(
        db,
        cache=cache,
        ttl_seconds=settings.stats_cache.ttl_seconds,
    )


router.add_api_route(
    "",
    get_catalog_stats,
    methods=["GET"],
    response_model=CatalogStats,
)

"""Shared search projection query services for ops surfaces."""

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from podex.services.search import MeilisearchClient


@runtime_checkable
class SearchProjectionClientProtocol(Protocol):
    """Protocol for search projection clients used by ops services."""

    @property
    def enabled(self) -> bool:
        """Whether the underlying search client is enabled."""

    def health_check(self) -> bool:
        """Check whether the search backend is healthy."""

    def get_index_stats(self, index_name: str) -> dict[str, Any]:
        """Fetch stats for a specific search index.

        Args:
            index_name: Search index name.

        Returns:
            Raw index stats payload.
        """


@dataclass(frozen=True, slots=True)
class SearchProjectionIndexData:
    """Summary of a single search projection index."""

    name: str
    document_count: int
    is_indexing: bool


@dataclass(frozen=True, slots=True)
class SearchProjectionStatusData:
    """Search projection health summary for ops surfaces."""

    configured: bool
    healthy: bool
    indexes: list[SearchProjectionIndexData]


def _empty_index_stats(*, index_name: str) -> SearchProjectionIndexData:
    """Build an empty index summary.

    Args:
        index_name: Search index name.

    Returns:
        Empty index summary.
    """
    return SearchProjectionIndexData(
        name=index_name,
        document_count=0,
        is_indexing=False,
    )


def get_search_projection_status(
    *,
    client: SearchProjectionClientProtocol,
    configured_enabled: bool,
) -> SearchProjectionStatusData:
    """Get search projection status for ops APIs.

    Args:
        client: Search client adapter.
        configured_enabled: Whether search is enabled in configuration.

    Returns:
        Search projection status summary.
    """
    index_names = [
        MeilisearchClient.INDEX_MEDIA,
        MeilisearchClient.INDEX_EPISODES,
        MeilisearchClient.INDEX_PODCASTS,
    ]

    if not configured_enabled or not client.enabled:
        return SearchProjectionStatusData(
            configured=configured_enabled,
            healthy=False,
            indexes=[_empty_index_stats(index_name=name) for name in index_names],
        )

    healthy = client.health_check()
    if not healthy:
        return SearchProjectionStatusData(
            configured=True,
            healthy=False,
            indexes=[_empty_index_stats(index_name=name) for name in index_names],
        )

    indexes = []
    for index_name in index_names:
        stats = client.get_index_stats(index_name)
        indexes.append(
            SearchProjectionIndexData(
                name=index_name,
                document_count=int(stats.get("numberOfDocuments", 0) or 0),
                is_indexing=bool(stats.get("isIndexing", False)),
            )
        )

    return SearchProjectionStatusData(
        configured=True,
        healthy=True,
        indexes=indexes,
    )

"""Shared public search services for API surfaces."""

from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from podex.services.search import MeilisearchClient

SearchResultType = Literal["media", "episode", "podcast"]


@runtime_checkable
class SearchClientProtocol(Protocol):
    """Protocol for search clients used by public search services."""

    @property
    def enabled(self) -> bool:
        """Whether search is available."""

    def search(
        self,
        index_name: str,
        query: str,
        limit: int = 10,
        offset: int = 0,
        filters: str | None = None,
        sort: list[str] | None = None,
    ) -> dict[str, Any]:
        """Search a single index.

        Args:
            index_name: Name of the index.
            query: Search query.
            limit: Maximum number of results.
            offset: Search offset.
            filters: Optional filter expression.
            sort: Optional sort rules.

        Returns:
            Raw search response.
        """

    def multi_search(self, queries: list[dict[str, Any]]) -> dict[str, Any]:
        """Run multiple index searches in a single request.

        Args:
            queries: Index-specific search payloads.

        Returns:
            Raw multi-search response.
        """


@dataclass(frozen=True, slots=True)
class SearchResultItem:
    """Single search hit returned by shared public search services."""

    id: int
    type: SearchResultType
    title: str
    subtitle: str | None = None
    cover_url: str | None = None
    url: str = ""
    slug: str | None = None


@dataclass(frozen=True, slots=True)
class SearchResultGroup:
    """Grouped search results for a specific resource type."""

    type: SearchResultType
    hits: list[SearchResultItem]
    total: int


@dataclass(frozen=True, slots=True)
class GlobalSearchResult:
    """Aggregated search response across all public search indexes."""

    query: str
    results: list[SearchResultGroup]
    processing_time_ms: int


def transform_media_hit(hit: dict[str, Any]) -> SearchResultItem:
    """Transform a media search hit to the shared search result shape.

    Args:
        hit: Raw media hit from the search index.

    Returns:
        Normalized shared search result item.
    """
    subtitle_parts = []
    if hit.get("type"):
        subtitle_parts.append(str(hit["type"]).replace("_", " ").title())
    if hit.get("author"):
        subtitle_parts.append(str(hit["author"]))
    if hit.get("year"):
        subtitle_parts.append(str(hit["year"]))

    return SearchResultItem(
        id=int(hit["id"]),
        type="media",
        title=str(hit.get("title", "")),
        subtitle=" • ".join(subtitle_parts) if subtitle_parts else None,
        cover_url=hit.get("cover_url"),
        url=str(hit.get("url", f"/media/{hit['id']}")),
    )


def transform_episode_hit(hit: dict[str, Any]) -> SearchResultItem:
    """Transform an episode search hit to the shared search result shape.

    Args:
        hit: Raw episode hit from the search index.

    Returns:
        Normalized shared search result item.
    """
    subtitle_parts = []
    if hit.get("podcast_name"):
        subtitle_parts.append(str(hit["podcast_name"]))
    if hit.get("episode_number"):
        subtitle_parts.append(f"Episode {hit['episode_number']}")

    return SearchResultItem(
        id=int(hit["id"]),
        type="episode",
        title=str(hit.get("title", "")),
        subtitle=" • ".join(subtitle_parts) if subtitle_parts else None,
        cover_url=hit.get("thumbnail_url"),
        url=str(hit.get("url", f"/episodes/{hit['id']}")),
    )


def transform_podcast_hit(hit: dict[str, Any]) -> SearchResultItem:
    """Transform a podcast search hit to the shared search result shape.

    Args:
        hit: Raw podcast hit from the search index.

    Returns:
        Normalized shared search result item.
    """
    subtitle = None
    episode_count = hit.get("episode_count")
    if episode_count:
        subtitle = f"{episode_count} episodes"

    slug = hit.get("slug")
    fallback_slug = slug if slug is not None else hit["id"]
    return SearchResultItem(
        id=int(hit["id"]),
        type="podcast",
        title=str(hit.get("name", "")),
        subtitle=subtitle,
        cover_url=hit.get("cover_url"),
        url=str(hit.get("url", f"/podcasts/{fallback_slug}")),
        slug=str(slug) if slug is not None else None,
    )


def global_search(
    *,
    client: SearchClientProtocol,
    query_text: str,
    limit: int,
) -> GlobalSearchResult:
    """Search across media, episodes, and podcasts.

    Args:
        client: Search client adapter.
        query_text: Search query text.
        limit: Maximum results per group.

    Returns:
        Aggregated search results.
    """
    if not client.enabled:
        return GlobalSearchResult(
            query=query_text,
            results=[],
            processing_time_ms=0,
        )

    queries = [
        {
            "indexUid": MeilisearchClient.INDEX_MEDIA,
            "q": query_text,
            "limit": limit,
        },
        {
            "indexUid": MeilisearchClient.INDEX_EPISODES,
            "q": query_text,
            "limit": limit,
        },
        {
            "indexUid": MeilisearchClient.INDEX_PODCASTS,
            "q": query_text,
            "limit": limit,
        },
    ]

    multi_result = client.multi_search(queries)
    results: list[SearchResultGroup] = []
    total_time = 0

    for result in multi_result.get("results", []):
        index_uid = result.get("indexUid", "")
        hits = result.get("hits", [])
        total_time += int(result.get("processingTimeMs", 0))

        if index_uid == MeilisearchClient.INDEX_MEDIA:
            transformed_hits = [transform_media_hit(hit) for hit in hits]
            results.append(
                SearchResultGroup(
                    type="media",
                    hits=transformed_hits,
                    total=int(result.get("estimatedTotalHits", len(hits))),
                )
            )
        elif index_uid == MeilisearchClient.INDEX_EPISODES:
            transformed_hits = [transform_episode_hit(hit) for hit in hits]
            results.append(
                SearchResultGroup(
                    type="episode",
                    hits=transformed_hits,
                    total=int(result.get("estimatedTotalHits", len(hits))),
                )
            )
        elif index_uid == MeilisearchClient.INDEX_PODCASTS:
            transformed_hits = [transform_podcast_hit(hit) for hit in hits]
            results.append(
                SearchResultGroup(
                    type="podcast",
                    hits=transformed_hits,
                    total=int(result.get("estimatedTotalHits", len(hits))),
                )
            )

    return GlobalSearchResult(
        query=query_text,
        results=results,
        processing_time_ms=total_time,
    )


def search_media(
    *,
    client: SearchClientProtocol,
    query_text: str,
    limit: int,
    type_filter: str | None = None,
) -> list[SearchResultItem]:
    """Search only media documents.

    Args:
        client: Search client adapter.
        query_text: Search query text.
        limit: Maximum results.
        type_filter: Optional media type filter.

    Returns:
        Media search hits.
    """
    if not client.enabled:
        return []

    filters = f'type = "{type_filter}"' if type_filter else None
    result = client.search(
        MeilisearchClient.INDEX_MEDIA,
        query_text,
        limit=limit,
        filters=filters,
        sort=["mention_count:desc"],
    )
    return [transform_media_hit(hit) for hit in result.get("hits", [])]


def search_episodes(
    *,
    client: SearchClientProtocol,
    query_text: str,
    limit: int,
    podcast_id: int | None = None,
) -> list[SearchResultItem]:
    """Search only episode documents.

    Args:
        client: Search client adapter.
        query_text: Search query text.
        limit: Maximum results.
        podcast_id: Optional numeric podcast filter.

    Returns:
        Episode search hits.
    """
    if not client.enabled:
        return []

    filters = f"podcast_id = {podcast_id}" if podcast_id else None
    result = client.search(
        MeilisearchClient.INDEX_EPISODES,
        query_text,
        limit=limit,
        filters=filters,
        sort=["published_at:desc"],
    )
    return [transform_episode_hit(hit) for hit in result.get("hits", [])]


def search_podcasts(
    *,
    client: SearchClientProtocol,
    query_text: str,
    limit: int,
) -> list[SearchResultItem]:
    """Search only podcast documents.

    Args:
        client: Search client adapter.
        query_text: Search query text.
        limit: Maximum results.

    Returns:
        Podcast search hits.
    """
    if not client.enabled:
        return []

    result = client.search(
        MeilisearchClient.INDEX_PODCASTS,
        query_text,
        limit=limit,
        sort=["episode_count:desc"],
    )
    return [transform_podcast_hit(hit) for hit in result.get("hits", [])]

"""Global search API endpoints."""

from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel

from podex.services.public_search import (
    GlobalSearchResult,
)
from podex.services.public_search import (
    SearchResultGroup as ServiceSearchResultGroup,
)
from podex.services.public_search import (
    SearchResultItem as ServiceSearchResultItem,
)
from podex.services.public_search import (
    global_search as run_global_search,
)
from podex.services.public_search import (
    search_episodes as run_episode_search,
)
from podex.services.public_search import (
    search_media as run_media_search,
)
from podex.services.public_search import (
    search_podcasts as run_podcast_search,
)
from podex.services.search import get_search_client

router = APIRouter(prefix="/search", tags=["search"])


class SearchResultItem(BaseModel):
    """Individual search result item."""

    id: int
    type: Literal["media", "episode", "podcast"]
    title: str
    subtitle: str | None = None
    cover_url: str | None = None
    url: str


class SearchResultGroup(BaseModel):
    """Search results for a specific type."""

    type: str
    hits: list[SearchResultItem]
    total: int


class GlobalSearchResponse(BaseModel):
    """Response for global search across all indexes."""

    query: str
    results: list[SearchResultGroup]
    processing_time_ms: int


def _to_api_item(*, item: ServiceSearchResultItem) -> SearchResultItem:
    """Convert a shared search item into the v1 response shape.

    Args:
        item: Shared search result item.

    Returns:
        API response item.
    """
    return SearchResultItem(
        id=item.id,
        type=item.type,
        title=item.title,
        subtitle=item.subtitle,
        cover_url=item.cover_url,
        url=item.url,
    )


def _to_api_group(*, group: ServiceSearchResultGroup) -> SearchResultGroup:
    """Convert a shared search group into the v1 response shape.

    Args:
        group: Shared search result group.

    Returns:
        API response group.
    """
    return SearchResultGroup(
        type=group.type,
        hits=[_to_api_item(item=item) for item in group.hits],
        total=group.total,
    )


def _to_api_response(*, result: GlobalSearchResult) -> GlobalSearchResponse:
    """Convert a shared search response into the v1 response shape.

    Args:
        result: Shared global search result.

    Returns:
        API response payload.
    """
    return GlobalSearchResponse(
        query=result.query,
        results=[_to_api_group(group=group) for group in result.results],
        processing_time_ms=result.processing_time_ms,
    )


@router.get("/global", response_model=GlobalSearchResponse)
def global_search(
    q: str = Query(..., min_length=1, max_length=200, description="Search query"),
    limit: int = Query(10, ge=1, le=50, description="Results per type"),
) -> GlobalSearchResponse:
    """Global search across media, episodes, and podcasts.

    Returns up to `limit` results for each type (media, episodes, podcasts).
    """
    return _to_api_response(
        result=run_global_search(
            client=get_search_client(),
            query_text=q,
            limit=limit,
        )
    )


@router.get("/media", response_model=list[SearchResultItem])
def search_media(
    q: str = Query(..., min_length=1, max_length=200, description="Search query"),
    limit: int = Query(20, ge=1, le=100, description="Maximum results"),
    type_filter: str | None = Query(
        None,
        alias="type",
        description="Filter by media type",
    ),
) -> list[SearchResultItem]:
    """Search media only."""
    return [
        _to_api_item(item=item)
        for item in run_media_search(
            client=get_search_client(),
            query_text=q,
            limit=limit,
            type_filter=type_filter,
        )
    ]


@router.get("/episodes", response_model=list[SearchResultItem])
def search_episodes(
    q: str = Query(..., min_length=1, max_length=200, description="Search query"),
    limit: int = Query(20, ge=1, le=100, description="Maximum results"),
    podcast_id: int | None = Query(None, description="Filter by podcast ID"),
) -> list[SearchResultItem]:
    """Search episodes only."""
    return [
        _to_api_item(item=item)
        for item in run_episode_search(
            client=get_search_client(),
            query_text=q,
            limit=limit,
            podcast_id=podcast_id,
        )
    ]


@router.get("/podcasts", response_model=list[SearchResultItem])
def search_podcasts(
    q: str = Query(..., min_length=1, max_length=200, description="Search query"),
    limit: int = Query(20, ge=1, le=100, description="Maximum results"),
) -> list[SearchResultItem]:
    """Search podcasts only."""
    return [
        _to_api_item(item=item)
        for item in run_podcast_search(
            client=get_search_client(),
            query_text=q,
            limit=limit,
        )
    ]

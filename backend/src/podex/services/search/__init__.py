"""Search service module for Meilisearch integration."""

from podex.services.search.meilisearch_client import (
    MeilisearchClient,
    get_search_client,
)
from podex.services.search.search_sync import SearchSyncService

__all__ = [
    "MeilisearchClient",
    "SearchSyncService",
    "get_search_client",
]

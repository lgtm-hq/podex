"""Meilisearch client wrapper for search operations."""

from functools import lru_cache
from typing import Any

import meilisearch
from meilisearch.errors import MeilisearchError

from podex.config import get_settings
from podex.logging_config import get_logger

logger = get_logger(__name__)


class MeilisearchClient:
    """Wrapper around Meilisearch client with configuration and error handling.

    Args:
        url: Meilisearch server URL.
        master_key: Optional master key for authentication.
    """

    # Index names
    INDEX_MEDIA = "media"
    INDEX_EPISODES = "episodes"
    INDEX_PODCASTS = "podcasts"

    # Index configurations
    INDEX_CONFIGS = {
        INDEX_MEDIA: {
            "searchableAttributes": ["title", "author", "description", "type"],
            "filterableAttributes": ["type", "year", "mention_count"],
            "sortableAttributes": ["mention_count", "title", "created_at", "year"],
            "rankingRules": [
                "words",
                "typo",
                "proximity",
                "attribute",
                "sort",
                "exactness",
                "mention_count:desc",
            ],
        },
        INDEX_EPISODES: {
            "searchableAttributes": ["title", "podcast_name"],
            "filterableAttributes": ["podcast_id", "published_at", "transcript_status"],
            "sortableAttributes": ["published_at", "episode_number", "mention_count"],
            "rankingRules": [
                "words",
                "typo",
                "proximity",
                "attribute",
                "sort",
                "exactness",
            ],
        },
        INDEX_PODCASTS: {
            "searchableAttributes": ["name", "description"],
            "filterableAttributes": ["status"],
            "sortableAttributes": ["name", "created_at", "episode_count"],
            "rankingRules": [
                "words",
                "typo",
                "proximity",
                "attribute",
                "sort",
                "exactness",
            ],
        },
    }

    def __init__(self, url: str, master_key: str = "") -> None:
        self.url = url
        self._client = meilisearch.Client(url, master_key or None)
        self._enabled = True

    @property
    def enabled(self) -> bool:
        """Check if the client is enabled."""
        return self._enabled

    def disable(self) -> None:
        """Disable the client."""
        self._enabled = False

    def health_check(self) -> bool:
        """Check if Meilisearch is healthy.

        Returns:
            True if healthy, False otherwise.
        """
        try:
            health = self._client.health()
            status: Any = health.get("status")
            return status == "available"
        except MeilisearchError as e:
            logger.warning("meilisearch_health_check_failed", error=str(e))
            return False

    def ensure_indexes(self) -> None:
        """Create indexes if they don't exist and configure them."""
        for index_name, config in self.INDEX_CONFIGS.items():
            try:
                # Create index if it doesn't exist
                self._client.create_index(index_name, {"primaryKey": "id"})
                logger.info("meilisearch_index_created", index=index_name)
            except MeilisearchError:
                # Index already exists
                pass

            # Update settings
            try:
                index = self._client.index(index_name)
                index.update_settings(config)
                logger.info("meilisearch_index_configured", index=index_name)
            except MeilisearchError as e:
                logger.error(
                    "meilisearch_index_config_failed",
                    index=index_name,
                    error=str(e),
                )

    def add_documents(
        self,
        index_name: str,
        documents: list[dict[str, Any]],
        primary_key: str = "id",
    ) -> dict[str, Any]:
        """Add or update documents in an index.

        Args:
            index_name: Name of the index.
            documents: List of documents to add.
            primary_key: Primary key field name.

        Returns:
            Task info from Meilisearch.
        """
        if not self._enabled:
            return {"status": "disabled"}

        try:
            index = self._client.index(index_name)
            task = index.add_documents(documents, primary_key)
            logger.info(
                "meilisearch_documents_added",
                index=index_name,
                count=len(documents),
                task_uid=task.task_uid,
            )
            return {"task_uid": task.task_uid, "status": "enqueued"}
        except MeilisearchError as e:
            logger.error(
                "meilisearch_add_documents_failed",
                index=index_name,
                error=str(e),
            )
            raise

    def delete_document(
        self, index_name: str, document_id: int | str
    ) -> dict[str, Any]:
        """Delete a document from an index.

        Args:
            index_name: Name of the index.
            document_id: ID of the document to delete.

        Returns:
            Task info from Meilisearch.
        """
        if not self._enabled:
            return {"status": "disabled"}

        try:
            index = self._client.index(index_name)
            task = index.delete_document(document_id)
            logger.info(
                "meilisearch_document_deleted",
                index=index_name,
                document_id=document_id,
            )
            return {"task_uid": task.task_uid, "status": "enqueued"}
        except MeilisearchError as e:
            logger.error(
                "meilisearch_delete_document_failed",
                index=index_name,
                document_id=document_id,
                error=str(e),
            )
            raise

    def search(
        self,
        index_name: str,
        query: str,
        limit: int = 10,
        offset: int = 0,
        filters: str | None = None,
        sort: list[str] | None = None,
    ) -> dict[str, Any]:
        """Search an index.

        Args:
            index_name: Name of the index.
            query: Search query string.
            limit: Maximum results to return.
            offset: Number of results to skip.
            filters: Optional filter string.
            sort: Optional list of sort rules.

        Returns:
            Search results from Meilisearch.
        """
        if not self._enabled:
            return {"hits": [], "query": query, "processingTimeMs": 0}

        try:
            index = self._client.index(index_name)
            search_params: dict[str, Any] = {
                "limit": limit,
                "offset": offset,
            }
            if filters:
                search_params["filter"] = filters
            if sort:
                search_params["sort"] = sort

            result: dict[str, Any] = index.search(query, search_params)
            logger.debug(
                "meilisearch_search",
                index=index_name,
                query=query,
                hits=len(result.get("hits", [])),
                processing_time_ms=result.get("processingTimeMs"),
            )
            return result
        except MeilisearchError as e:
            logger.error(
                "meilisearch_search_failed",
                index=index_name,
                query=query,
                error=str(e),
            )
            return {"hits": [], "query": query, "processingTimeMs": 0, "error": str(e)}

    def multi_search(
        self,
        queries: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Perform multiple searches in a single request.

        Args:
            queries: List of search queries with index and query params.

        Returns:
            Combined search results.
        """
        if not self._enabled:
            return {"results": []}

        try:
            result: dict[str, Any] = self._client.multi_search(queries)
            return result
        except MeilisearchError as e:
            logger.error("meilisearch_multi_search_failed", error=str(e))
            return {"results": [], "error": str(e)}

    def delete_all_documents(self, index_name: str) -> dict[str, Any]:
        """Delete all documents from an index.

        Args:
            index_name: Name of the index.

        Returns:
            Task info from Meilisearch.
        """
        if not self._enabled:
            return {"status": "disabled"}

        try:
            index = self._client.index(index_name)
            task = index.delete_all_documents()
            logger.info("meilisearch_all_documents_deleted", index=index_name)
            return {"task_uid": task.task_uid, "status": "enqueued"}
        except MeilisearchError as e:
            logger.error(
                "meilisearch_delete_all_failed",
                index=index_name,
                error=str(e),
            )
            raise

    def get_index_stats(self, index_name: str) -> dict[str, Any]:
        """Get statistics for an index.

        Args:
            index_name: Name of the index.

        Returns:
            Index statistics.
        """
        try:
            index = self._client.index(index_name)
            stats: dict[str, Any] = index.get_stats()
            return stats
        except MeilisearchError as e:
            logger.error(
                "meilisearch_get_stats_failed",
                index=index_name,
                error=str(e),
            )
            return {}


@lru_cache
def get_search_client() -> MeilisearchClient:
    """Get cached Meilisearch client instance.

    Returns:
        Configured MeilisearchClient instance.
    """
    settings = get_settings()
    client = MeilisearchClient(
        url=settings.meilisearch_url,
        master_key=settings.meilisearch_master_key,
    )

    if not settings.meilisearch_enabled:
        client.disable()
        logger.info("meilisearch_disabled")
    else:
        if client.health_check():
            logger.info("meilisearch_connected", url=settings.meilisearch_url)
        else:
            logger.warning("meilisearch_unavailable", url=settings.meilisearch_url)
            client.disable()

    return client

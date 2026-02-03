#!/usr/bin/env python
"""CLI script for syncing database to Meilisearch."""

import argparse
import sys
from typing import Any, Literal

from podex.database import SessionLocal
from podex.logging_config import configure_logging, get_logger
from podex.services.search import (
    MeilisearchClient,
    SearchSyncService,
    get_search_client,
)

configure_logging()
logger = get_logger(__name__)

IndexName = Literal["all", "media", "episodes", "podcasts"]


def sync_indexes(
    index: IndexName = "all",
    full_reindex: bool = False,
) -> dict[str, Any]:
    """Sync database records to Meilisearch.

    Args:
        index: Which index to sync (all, media, episodes, podcasts).
        full_reindex: Whether to delete all documents before syncing.

    Returns:
        Sync results summary.
    """
    client = get_search_client()

    if not client.enabled:
        logger.error("meilisearch_not_available")
        return {"error": "Meilisearch is not available"}

    # Ensure indexes exist with proper configuration
    client.ensure_indexes()

    db = SessionLocal()
    try:
        sync_service = SearchSyncService(client, db)

        if index == "all":
            return sync_service.sync_all(full_reindex=full_reindex)

        if full_reindex:
            if index == "media":
                client.delete_all_documents(MeilisearchClient.INDEX_MEDIA)
            elif index == "episodes":
                client.delete_all_documents(MeilisearchClient.INDEX_EPISODES)
            elif index == "podcasts":
                client.delete_all_documents(MeilisearchClient.INDEX_PODCASTS)

        if index == "media":
            return {"media": sync_service.sync_media()}
        elif index == "episodes":
            return {"episodes": sync_service.sync_episodes()}
        elif index == "podcasts":
            return {"podcasts": sync_service.sync_podcasts()}

        return {"error": f"Unknown index: {index}"}
    finally:
        db.close()


def show_stats() -> None:
    """Show statistics for all Meilisearch indexes."""
    client = get_search_client()

    if not client.enabled:
        print("Meilisearch is not available")
        return

    print("\n=== Meilisearch Index Stats ===\n")

    for index_name in [
        MeilisearchClient.INDEX_MEDIA,
        MeilisearchClient.INDEX_EPISODES,
        MeilisearchClient.INDEX_PODCASTS,
    ]:
        stats = client.get_index_stats(index_name)
        print(f"Index: {index_name}")
        print(f"  Documents: {stats.get('numberOfDocuments', 0)}")
        print(f"  Is Indexing: {stats.get('isIndexing', False)}")
        print()


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Sync database records to Meilisearch indexes"
    )
    parser.add_argument(
        "--index",
        type=str,
        choices=["all", "media", "episodes", "podcasts"],
        default="all",
        help="Which index to sync (default: all)",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Perform full reindex (delete all documents first)",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Show index statistics instead of syncing",
    )

    args = parser.parse_args()

    if args.stats:
        show_stats()
        return 0

    print(f"Syncing index: {args.index}")
    if args.full:
        print("Mode: Full reindex (all existing documents will be deleted)")
    else:
        print("Mode: Incremental (existing documents will be updated)")
    print()

    results = sync_indexes(index=args.index, full_reindex=args.full)

    if "error" in results:
        print(f"Error: {results['error']}")
        return 1

    print("\n=== Sync Results ===\n")
    for index_name, stats in results.items():
        print(f"{index_name}:")
        print(f"  Total records: {stats.get('total', 0)}")
        print(f"  Synced: {stats.get('synced', 0)}")
        print()

    print("Sync completed successfully!")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Service for syncing database records to Meilisearch indexes."""

from datetime import datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from podex.logging_config import get_logger
from podex.models import Episode, Media, Mention, Podcast
from podex.services.search.meilisearch_client import MeilisearchClient

logger = get_logger(__name__)


class SearchSyncService:
    """Synchronize database records to Meilisearch indexes.

    Args:
        client: Meilisearch client instance.
        db: SQLAlchemy database session.
    """

    BATCH_SIZE = 1000

    def __init__(self, client: MeilisearchClient, db: Session) -> None:
        self.client = client
        self.db = db

    def sync_all(self, full_reindex: bool = False) -> dict[str, Any]:
        """Sync all indexes.

        Args:
            full_reindex: If True, delete all documents before syncing.

        Returns:
            Summary of sync operation.
        """
        results = {}

        if full_reindex:
            logger.info("search_sync_full_reindex_started")
            self.client.delete_all_documents(MeilisearchClient.INDEX_MEDIA)
            self.client.delete_all_documents(MeilisearchClient.INDEX_EPISODES)
            self.client.delete_all_documents(MeilisearchClient.INDEX_PODCASTS)

        results["media"] = self.sync_media()
        results["episodes"] = self.sync_episodes()
        results["podcasts"] = self.sync_podcasts()

        logger.info("search_sync_completed", **results)
        return results

    def sync_media(self) -> dict[str, Any]:
        """Sync all media to Meilisearch.

        Returns:
            Sync statistics.
        """
        logger.info("search_sync_media_started")

        # Get mention counts per media
        mention_counts = dict(
            self.db.query(Mention.media_id, func.count(Mention.id))
            .group_by(Mention.media_id)
            .all()
        )

        # Get episode counts per media
        episode_counts = dict(
            self.db.query(
                Mention.media_id,
                func.count(func.distinct(Mention.episode_id)),
            )
            .group_by(Mention.media_id)
            .all()
        )

        total = self.db.query(Media).count()
        synced = 0

        for offset in range(0, total, self.BATCH_SIZE):
            media_batch = (
                self.db.query(Media)
                .order_by(Media.id)
                .offset(offset)
                .limit(self.BATCH_SIZE)
                .all()
            )

            documents = []
            for media in media_batch:
                doc = {
                    "id": media.id,
                    "type": media.type,
                    "title": media.title,
                    "author": media.author,
                    "description": media.description,
                    "year": media.year,
                    "cover_url": media.cover_url,
                    "mention_count": mention_counts.get(media.id, 0),
                    "episode_count": episode_counts.get(media.id, 0),
                    "created_at": (
                        media.created_at.isoformat()
                        if isinstance(media.created_at, datetime)
                        else str(media.created_at)
                    ),
                    "url": f"/media/{media.id}",
                }
                documents.append(doc)

            if documents:
                self.client.add_documents(MeilisearchClient.INDEX_MEDIA, documents)
                synced += len(documents)

        logger.info("search_sync_media_completed", total=total, synced=synced)
        return {"total": total, "synced": synced}

    def sync_episodes(self) -> dict[str, Any]:
        """Sync all episodes to Meilisearch.

        Returns:
            Sync statistics.
        """
        logger.info("search_sync_episodes_started")

        # Get mention counts per episode
        mention_counts = dict(
            self.db.query(Mention.episode_id, func.count(Mention.id))
            .group_by(Mention.episode_id)
            .all()
        )

        # Get podcast names
        podcast_names = dict(self.db.query(Podcast.id, Podcast.name).all())

        total = self.db.query(Episode).count()
        synced = 0

        for offset in range(0, total, self.BATCH_SIZE):
            episode_batch = (
                self.db.query(Episode)
                .order_by(Episode.id)
                .offset(offset)
                .limit(self.BATCH_SIZE)
                .all()
            )

            documents = []
            for episode in episode_batch:
                doc = {
                    "id": episode.id,
                    "title": episode.title,
                    "podcast_id": episode.podcast_id,
                    "podcast_name": podcast_names.get(episode.podcast_id, ""),
                    "episode_number": episode.episode_number,
                    "youtube_id": episode.youtube_id,
                    "thumbnail_url": episode.thumbnail_url,
                    "transcript_status": episode.transcript_status,
                    "mention_count": mention_counts.get(episode.id, 0),
                    "published_at": (
                        episode.published_at.isoformat()
                        if episode.published_at
                        else None
                    ),
                    "url": f"/episodes/{episode.id}",
                }
                documents.append(doc)

            if documents:
                self.client.add_documents(MeilisearchClient.INDEX_EPISODES, documents)
                synced += len(documents)

        logger.info("search_sync_episodes_completed", total=total, synced=synced)
        return {"total": total, "synced": synced}

    def sync_podcasts(self) -> dict[str, Any]:
        """Sync all podcasts to Meilisearch.

        Returns:
            Sync statistics.
        """
        logger.info("search_sync_podcasts_started")

        # Get episode counts per podcast
        episode_counts = dict(
            self.db.query(Episode.podcast_id, func.count(Episode.id))
            .group_by(Episode.podcast_id)
            .all()
        )

        total = self.db.query(Podcast).count()
        synced = 0

        for offset in range(0, total, self.BATCH_SIZE):
            podcast_batch = (
                self.db.query(Podcast)
                .order_by(Podcast.id)
                .offset(offset)
                .limit(self.BATCH_SIZE)
                .all()
            )

            documents = []
            for podcast in podcast_batch:
                doc = {
                    "id": podcast.id,
                    "name": podcast.name,
                    "slug": podcast.slug,
                    "description": podcast.description,
                    "cover_url": podcast.cover_url,
                    "episode_count": episode_counts.get(podcast.id, 0),
                    "status": getattr(podcast, "status", "active"),
                    "created_at": (
                        podcast.created_at.isoformat()
                        if isinstance(podcast.created_at, datetime)
                        else str(podcast.created_at)
                    ),
                    "url": f"/podcasts/{podcast.slug}",
                }
                documents.append(doc)

            if documents:
                self.client.add_documents(MeilisearchClient.INDEX_PODCASTS, documents)
                synced += len(documents)

        logger.info("search_sync_podcasts_completed", total=total, synced=synced)
        return {"total": total, "synced": synced}

    def sync_single_media(self, media_id: int) -> bool:
        """Sync a single media item to Meilisearch.

        Args:
            media_id: ID of the media to sync.

        Returns:
            True if synced successfully.
        """
        media = self.db.query(Media).filter(Media.id == media_id).first()
        if not media:
            return False

        mention_count = (
            self.db.query(func.count(Mention.id))
            .filter(Mention.media_id == media_id)
            .scalar()
            or 0
        )
        episode_count = (
            self.db.query(func.count(func.distinct(Mention.episode_id)))
            .filter(Mention.media_id == media_id)
            .scalar()
            or 0
        )

        doc = {
            "id": media.id,
            "type": media.type,
            "title": media.title,
            "author": media.author,
            "description": media.description,
            "year": media.year,
            "cover_url": media.cover_url,
            "mention_count": mention_count,
            "episode_count": episode_count,
            "created_at": (
                media.created_at.isoformat()
                if isinstance(media.created_at, datetime)
                else str(media.created_at)
            ),
            "url": f"/media/{media.id}",
        }

        self.client.add_documents(MeilisearchClient.INDEX_MEDIA, [doc])
        return True

    def sync_single_episode(self, episode_id: int) -> bool:
        """Sync a single episode to Meilisearch.

        Args:
            episode_id: ID of the episode to sync.

        Returns:
            True if synced successfully.
        """
        episode = self.db.query(Episode).filter(Episode.id == episode_id).first()
        if not episode:
            return False

        podcast = (
            self.db.query(Podcast).filter(Podcast.id == episode.podcast_id).first()
        )
        mention_count = (
            self.db.query(func.count(Mention.id))
            .filter(Mention.episode_id == episode_id)
            .scalar()
            or 0
        )

        doc = {
            "id": episode.id,
            "title": episode.title,
            "podcast_id": episode.podcast_id,
            "podcast_name": podcast.name if podcast else "",
            "episode_number": episode.episode_number,
            "youtube_id": episode.youtube_id,
            "thumbnail_url": episode.thumbnail_url,
            "transcript_status": episode.transcript_status,
            "mention_count": mention_count,
            "published_at": (
                episode.published_at.isoformat() if episode.published_at else None
            ),
            "url": f"/episodes/{episode.id}",
        }

        self.client.add_documents(MeilisearchClient.INDEX_EPISODES, [doc])
        return True

    def delete_media(self, media_id: int) -> bool:
        """Delete a media item from Meilisearch.

        Args:
            media_id: ID of the media to delete.

        Returns:
            True if deleted successfully.
        """
        self.client.delete_document(MeilisearchClient.INDEX_MEDIA, media_id)
        return True

    def delete_episode(self, episode_id: int) -> bool:
        """Delete an episode from Meilisearch.

        Args:
            episode_id: ID of the episode to delete.

        Returns:
            True if deleted successfully.
        """
        self.client.delete_document(MeilisearchClient.INDEX_EPISODES, episode_id)
        return True

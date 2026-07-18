"""Discovery orchestrator - coordinates discovery across multiple sources.

This module provides the DiscoveryOrchestrator class which:
1. Discovers episodes from multiple sources (podscripts.co, RSS, Spotify)
2. Deduplicates episodes across sources
3. Creates/updates episode records in the database
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from rapidfuzz import fuzz
from sqlalchemy.orm import Session

from podex.models import Episode
from podex.services.discovery import (
    DiscoveredEpisode,
    DiscoveryProvider,
    DiscoveryResult,
)
from podex.services.discovery_podscripts import PodscriptsDiscovery
from podex.services.discovery_rss import RSSDiscovery
from podex.services.discovery_spotify import SpotifyDiscovery

if TYPE_CHECKING:
    from podex.models import Podcast

logger = logging.getLogger(__name__)


class DiscoveryOrchestrator:
    """Coordinate discovery across multiple sources.

    Args:
        session: SQLAlchemy session for database operations
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self._sources: dict[str, DiscoveryProvider] = {
            "podscripts": PodscriptsDiscovery(),
            "rss": RSSDiscovery(),
            "spotify": SpotifyDiscovery(),
        }

    def discover_for_podcast(
        self,
        podcast: Podcast,
        since: datetime | None = None,
    ) -> DiscoveryResult:
        """Discover new episodes for a podcast from all configured sources.

        Args:
            podcast: The podcast to discover episodes for
            since: Only discover episodes published after this date

        Returns:
            DiscoveryResult with counts and any errors
        """
        discovered: list[DiscoveredEpisode] = []
        errors: list[str] = []

        # Try each source that has configuration for this podcast
        if podcast.podscripts_slug:
            try:
                source = self._sources["podscripts"]
                eps = source.discover_episodes(podcast, since=since)
                logger.info(f"Discovered {len(eps)} episodes from podscripts.co")
                discovered.extend(eps)
            except Exception as e:
                error = f"podscripts: {e}"
                logger.warning(error)
                errors.append(error)

        if podcast.rss_url:
            try:
                source = self._sources["rss"]
                eps = source.discover_episodes(podcast, since=since)
                logger.info(f"Discovered {len(eps)} episodes from RSS")
                discovered.extend(eps)
            except Exception as e:
                error = f"rss: {e}"
                logger.warning(error)
                errors.append(error)

        if podcast.spotify_id and self._sources["spotify"].is_configured:
            try:
                source = self._sources["spotify"]
                eps = source.discover_episodes(podcast, since=since)
                logger.info(f"Discovered {len(eps)} episodes from Spotify")
                discovered.extend(eps)
            except Exception as e:
                error = f"spotify: {e}"
                logger.warning(error)
                errors.append(error)

        # Deduplicate and merge
        merged = self._deduplicate_episodes(discovered)
        logger.info(f"After deduplication: {len(merged)} unique episodes")

        # Create/update episode records
        new_count, updated_count = self._upsert_episodes(podcast, merged)

        return DiscoveryResult(
            new_episodes=new_count,
            updated_episodes=updated_count,
            total_discovered=len(discovered),
            errors=errors,
            discovered_episodes=merged,
        )

    def _deduplicate_episodes(
        self,
        episodes: list[DiscoveredEpisode],
    ) -> list[DiscoveredEpisode]:
        """Deduplicate and merge episodes from multiple sources.

        Uses multiple matching strategies:
        1. Exact match on source-specific IDs and source URLs.
        2. Fuzzy match on title + date.
        """
        if not episodes:
            return []

        # Group episodes by potential matches
        merged: list[DiscoveredEpisode] = []
        used: set[int] = set()

        for i, ep1 in enumerate(episodes):
            if i in used:
                continue

            # Find all episodes that match this one
            matches = [ep1]
            used.add(i)

            for j, ep2 in enumerate(episodes):
                if j in used:
                    continue

                if self._episodes_match(ep1, ep2):
                    matches.append(ep2)
                    used.add(j)

            # Merge matched episodes
            merged_ep = self._merge_episodes(matches)
            merged.append(merged_ep)

        return merged

    def _episodes_match(
        self,
        ep1: DiscoveredEpisode,
        ep2: DiscoveredEpisode,
    ) -> bool:
        """Check if two episodes are the same.

        Returns True if they match by ID or by title similarity + date.
        """
        # Match by source-specific IDs
        if ep1.youtube_id and ep1.youtube_id == ep2.youtube_id:
            return True
        if ep1.spotify_uri and ep1.spotify_uri == ep2.spotify_uri:
            return True
        if ep1.rss_guid and ep1.rss_guid == ep2.rss_guid:
            return True
        if ep1.episode_url and ep1.episode_url == ep2.episode_url:
            return True

        # Match by episode number if both have one
        if (
            ep1.episode_number
            and ep2.episode_number
            and ep1.episode_number == ep2.episode_number
        ):
            return True

        # Match by fuzzy title + date
        title_ratio = fuzz.ratio(ep1.title.lower(), ep2.title.lower())
        if title_ratio < 80:
            return False

        # If titles are similar, check dates
        if ep1.published_at and ep2.published_at:
            # Same day = match
            if ep1.published_at.date() == ep2.published_at.date():
                return True
            # Within a week and high title match = probably match
            days_diff = abs((ep1.published_at - ep2.published_at).days)
            if days_diff <= 7 and title_ratio >= 90:
                return True

        # High title similarity alone (>95) = probably match
        return bool(title_ratio >= 95)

    def _merge_episodes(
        self,
        episodes: list[DiscoveredEpisode],
    ) -> DiscoveredEpisode:
        """Merge multiple matched episodes into one.

        Combines information from all sources, preferring non-null values.
        """
        if len(episodes) == 1:
            return episodes[0]

        # Start with the first episode
        merged = DiscoveredEpisode(
            title=episodes[0].title,
            published_at=episodes[0].published_at,
            duration_seconds=episodes[0].duration_seconds,
            episode_number=episodes[0].episode_number,
            description=episodes[0].description,
            episode_url=episodes[0].episode_url,
            thumbnail_url=episodes[0].thumbnail_url,
            youtube_id=episodes[0].youtube_id,
            spotify_uri=episodes[0].spotify_uri,
            rss_guid=episodes[0].rss_guid,
            apple_id=episodes[0].apple_id,
            has_transcript=episodes[0].has_transcript,
            discovery_source=episodes[0].discovery_source,
        )

        # Merge in values from other episodes
        for ep in episodes[1:]:
            # Prefer values from podscripts (has transcripts)
            if ep.has_transcript and not merged.has_transcript:
                merged.has_transcript = True
                # Also prefer podscripts title as it's often cleaner
                merged.title = ep.title

            # Fill in missing values
            if ep.published_at and not merged.published_at:
                merged.published_at = ep.published_at
            if ep.duration_seconds and not merged.duration_seconds:
                merged.duration_seconds = ep.duration_seconds
            if ep.episode_number and not merged.episode_number:
                merged.episode_number = ep.episode_number
            if ep.description and not merged.description:
                merged.description = ep.description
            if ep.episode_url and not merged.episode_url:
                merged.episode_url = ep.episode_url
            if ep.thumbnail_url and not merged.thumbnail_url:
                merged.thumbnail_url = ep.thumbnail_url
            if ep.youtube_id and not merged.youtube_id:
                merged.youtube_id = ep.youtube_id
            if ep.spotify_uri and not merged.spotify_uri:
                merged.spotify_uri = ep.spotify_uri
            if ep.rss_guid and not merged.rss_guid:
                merged.rss_guid = ep.rss_guid
            if ep.apple_id and not merged.apple_id:
                merged.apple_id = ep.apple_id

        return merged

    def _upsert_episodes(
        self,
        podcast: Podcast,
        episodes: list[DiscoveredEpisode],
    ) -> tuple[int, int]:
        """Create or update episodes in the database.

        Returns:
            Tuple of (new_count, updated_count)
        """
        new_count = 0
        updated_count = 0

        for discovered in episodes:
            # Try to find existing episode
            existing = self._find_existing_episode(podcast, discovered)

            if existing:
                # Update existing episode
                updated = self._update_episode(existing, discovered)
                if updated:
                    updated_count += 1
            else:
                # Create new episode
                self._create_episode(podcast, discovered)
                new_count += 1

        self.session.commit()
        return new_count, updated_count

    def _find_existing_episode(
        self,
        podcast: Podcast,
        discovered: DiscoveredEpisode,
    ) -> Episode | None:
        """Find an existing episode that matches the discovered one."""
        query = self.session.query(Episode).filter(Episode.podcast_id == podcast.id)

        # Try matching by source-specific IDs first
        if discovered.youtube_id:
            ep = query.filter(Episode.youtube_id == discovered.youtube_id).first()
            if ep:
                return ep

        if discovered.spotify_uri:
            ep = query.filter(Episode.spotify_uri == discovered.spotify_uri).first()
            if ep:
                return ep

        if discovered.rss_guid:
            ep = query.filter(Episode.rss_guid == discovered.rss_guid).first()
            if ep:
                return ep

        if discovered.episode_url:
            ep = query.filter(Episode.episode_url == discovered.episode_url).first()
            if ep:
                return ep

        # Try matching by episode number
        if discovered.episode_number:
            ep = query.filter(
                Episode.episode_number == discovered.episode_number
            ).first()
            if ep:
                return ep

        return None

    def _create_episode(
        self,
        podcast: Podcast,
        discovered: DiscoveredEpisode,
    ) -> Episode:
        """Create a new episode from discovered data."""
        episode = Episode(
            podcast_id=podcast.id,
            title=discovered.title,
            episode_number=discovered.episode_number,
            youtube_id=discovered.youtube_id,
            published_at=discovered.published_at,
            duration_seconds=discovered.duration_seconds,
            thumbnail_url=discovered.thumbnail_url,
            spotify_uri=discovered.spotify_uri,
            rss_guid=discovered.rss_guid,
            apple_id=discovered.apple_id,
            episode_url=discovered.episode_url,
            discovery_source=discovered.discovery_source,
        )
        self.session.add(episode)
        logger.debug(f"Created episode: {discovered.title}")
        return episode

    def _update_episode(
        self,
        episode: Episode,
        discovered: DiscoveredEpisode,
    ) -> bool:
        """Update an existing episode with new data.

        Returns True if any changes were made.
        """
        changed = False

        # Fill in missing fields
        if discovered.spotify_uri and not episode.spotify_uri:
            episode.spotify_uri = discovered.spotify_uri
            changed = True
        if discovered.rss_guid and not episode.rss_guid:
            episode.rss_guid = discovered.rss_guid
            changed = True
        if discovered.apple_id and not episode.apple_id:
            episode.apple_id = discovered.apple_id
            changed = True
        if discovered.episode_url and not episode.episode_url:
            episode.episode_url = discovered.episode_url
            changed = True
        if discovered.youtube_id and not episode.youtube_id:
            episode.youtube_id = discovered.youtube_id
            changed = True
        if discovered.duration_seconds and not episode.duration_seconds:
            episode.duration_seconds = discovered.duration_seconds
            changed = True
        if discovered.thumbnail_url and not episode.thumbnail_url:
            episode.thumbnail_url = discovered.thumbnail_url
            changed = True
        if discovered.published_at and not episode.published_at:
            episode.published_at = discovered.published_at
            changed = True

        if changed:
            logger.debug(f"Updated episode: {episode.title}")

        return changed

    def close(self) -> None:
        """Close any resources held by discovery sources."""
        for source in self._sources.values():
            if hasattr(source, "close"):
                source.close()

    def __enter__(self) -> DiscoveryOrchestrator:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

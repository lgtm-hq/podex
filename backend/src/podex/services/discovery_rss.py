"""RSS feed discovery service.

Discovers podcasts and episodes from RSS feeds.
"""

from __future__ import annotations

import contextlib
import logging
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any

import feedparser

from podex.services.discovery import (
    DiscoveredEpisode,
    DiscoveredPodcast,
)

if TYPE_CHECKING:
    from podex.models import Podcast

logger = logging.getLogger(__name__)


class RSSDiscovery:
    """Discover podcasts and episodes from RSS feeds."""

    @property
    def name(self) -> str:
        return "rss"

    @property
    def is_configured(self) -> bool:
        """RSS discovery is always configured (no API keys needed)."""
        return True

    def discover_podcast(self, rss_url: str) -> DiscoveredPodcast | None:
        """Discover podcast metadata from an RSS feed.

        Args:
            rss_url: The RSS feed URL

        Returns:
            DiscoveredPodcast with metadata, or None if feed is invalid
        """
        feed = feedparser.parse(rss_url)

        if feed.bozo and not feed.entries:
            logger.warning(f"Failed to parse RSS feed: {rss_url}")
            return None

        channel = feed.feed

        name = channel.get("title", "Unknown Podcast")
        description = channel.get("description") or channel.get("subtitle")
        author = channel.get("author") or channel.get("itunes_author")

        # Extract cover image
        cover_url = None
        if hasattr(channel, "image") and channel.image:
            cover_url = channel.image.get("href")
        if not cover_url and hasattr(channel, "itunes_image"):
            cover_url = channel.itunes_image.get("href")

        # Generate a slug from the name
        slug = self._generate_slug(name)

        return DiscoveredPodcast(
            name=name,
            slug=slug,
            description=description,
            author=author,
            cover_url=cover_url,
            rss_url=rss_url,
            discovery_source=self.name,
        )

    def discover_episodes(
        self,
        podcast: Podcast,
        since: datetime | None = None,
    ) -> list[DiscoveredEpisode]:
        """Discover episodes from an RSS feed.

        Args:
            podcast: The podcast to discover episodes for
            since: Only discover episodes published after this date

        Returns:
            List of discovered episodes
        """
        if not podcast.rss_url:
            logger.warning(f"Podcast {podcast.slug} has no rss_url")
            return []

        feed = feedparser.parse(podcast.rss_url)

        if feed.bozo and not feed.entries:
            logger.warning(f"Failed to parse RSS feed: {podcast.rss_url}")
            return []

        episodes = []

        for entry in feed.entries:
            episode = self._parse_episode(entry)

            if episode:
                # Filter by date if specified
                if since and episode.published_at and episode.published_at < since:
                    continue

                episodes.append(episode)

        logger.info(f"Discovered {len(episodes)} episodes from RSS feed")
        return episodes

    def _parse_episode(self, entry: Any) -> DiscoveredEpisode | None:
        """Parse an episode from a feed entry."""
        title = entry.get("title")
        if not title:
            return None

        # Parse published date
        published_at = None
        if entry.get("published"):
            try:
                published_at = parsedate_to_datetime(entry.published)
                if published_at.tzinfo is None:
                    published_at = published_at.replace(tzinfo=UTC)
            except (ValueError, TypeError):
                pass

        # Parse duration
        duration_seconds = None
        duration_str = entry.get("itunes_duration")
        if duration_str:
            duration_seconds = self._parse_duration(duration_str)

        # Extract episode number
        episode_number = None
        ep_num = entry.get("itunes_episode")
        if ep_num:
            with contextlib.suppress(ValueError, TypeError):
                episode_number = int(ep_num)

        # Get episode URL
        episode_url = entry.get("link")

        # Get GUID for deduplication
        rss_guid = entry.get("id") or entry.get("guid")

        # Get description
        description = entry.get("summary") or entry.get("description")
        if description:
            # Strip HTML tags
            description = re.sub(r"<[^>]+>", "", description).strip()
            if len(description) > 1000:
                description = description[:1000] + "..."

        # Get thumbnail
        thumbnail_url = None
        if hasattr(entry, "image") and entry.image:
            thumbnail_url = entry.image.get("href")
        if not thumbnail_url and hasattr(entry, "itunes_image"):
            thumbnail_url = entry.itunes_image.get("href")

        return DiscoveredEpisode(
            title=title,
            published_at=published_at,
            duration_seconds=duration_seconds,
            episode_number=episode_number,
            description=description,
            episode_url=episode_url,
            thumbnail_url=thumbnail_url,
            rss_guid=rss_guid,
            has_transcript=False,  # RSS doesn't include transcripts
            discovery_source=self.name,
        )

    def _parse_duration(self, duration_str: str) -> int | None:
        """Parse duration string to seconds.

        Handles formats like:
        - "3600" (seconds)
        - "60:00" (mm:ss)
        - "1:00:00" (hh:mm:ss)
        """
        if not duration_str:
            return None

        try:
            # Try as plain seconds
            if duration_str.isdigit():
                return int(duration_str)

            # Try as time format
            parts = duration_str.split(":")
            if len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
            elif len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])

        except (ValueError, TypeError):
            pass

        return None

    def _generate_slug(self, name: str) -> str:
        """Generate a URL-safe slug from a name."""
        # Lowercase and replace spaces with hyphens
        slug = name.lower().strip()
        slug = re.sub(r"[^\w\s-]", "", slug)
        slug = re.sub(r"[\s_]+", "-", slug)
        slug = re.sub(r"-+", "-", slug)
        return slug.strip("-")

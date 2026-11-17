"""RSS feed discovery service.

Discovers podcasts and episodes from RSS feeds.
"""

from __future__ import annotations

import contextlib
import logging
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from io import BytesIO
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import feedparser

from podex.services.discovery import (
    DiscoveredEpisode,
    DiscoveredPodcast,
    published_in_window,
    slugify_podcast_name,
)
from podex.services.episode_matching import guid_key
from podex.services.feed_http import FeedFetcher, FeedHttpClient

if TYPE_CHECKING:
    from podex.models import Podcast

logger = logging.getLogger(__name__)


def link_key(link: str | None) -> str | None:
    """Return the form in which two links are compared, or None for no link.

    Surrounding whitespace, the case of the scheme and host, a trailing slash
    and the fragment are ignored; the path and query compare exactly. A link
    that cannot be read as a URL compares as written, without the whitespace.

    Args:
        link: A link as written in a feed.

    Returns:
        The comparison key, or None for a missing or blank link.
    """
    if link is None or not link.strip():
        return None
    try:
        parts = urlsplit(link.strip())
    except ValueError:
        return link.strip()
    return parts._replace(
        scheme=parts.scheme.lower(),
        netloc=parts.netloc.lower(),
        path=parts.path.rstrip("/"),
        fragment="",
    ).geturl()


def _repeated(keys: Iterable[str | None]) -> frozenset[str]:
    """Return the keys that occur more than once, ignoring missing ones."""
    counts = Counter(key for key in keys if key is not None)
    return frozenset(key for key, count in counts.items() if count > 1)


@dataclass(frozen=True, slots=True)
class _FeedIdentity:
    """What tells one feed's items apart, read from the whole feed.

    A link or GUID that several items share, or that is the feed's own site
    link, names the show rather than an episode, so it is not passed on.
    """

    site_link: str | None
    shared_links: frozenset[str]
    shared_guids: frozenset[str]

    @classmethod
    def of(cls, feed: Any) -> _FeedIdentity:
        """Read the site link and the values shared by several items."""
        entries = feed.entries
        return cls(
            site_link=link_key(feed.feed.get("link")),
            shared_links=_repeated(link_key(entry.get("link")) for entry in entries),
            shared_guids=_repeated(guid_key(_entry_guid(entry)) for entry in entries),
        )

    def _names_the_show(self, link: str | None) -> bool:
        """Return whether a link key is the site link or shared by items."""
        return link is not None and (
            link == self.site_link or link in self.shared_links
        )

    def episode_url(self, link: str | None) -> str | None:
        """Return the item link, or None when it does not name one item."""
        return None if self._names_the_show(link_key(link)) else link

    def rss_guid(self, guid: str | None) -> str | None:
        """Return the item GUID, or None when it does not name one item."""
        key = guid_key(guid)
        if key is None or key in self.shared_guids:
            return None
        return None if self._names_the_show(link_key(guid)) else guid


_NO_FEED_IDENTITY = _FeedIdentity(
    site_link=None,
    shared_links=frozenset(),
    shared_guids=frozenset(),
)


def _entry_guid(entry: Any) -> str | None:
    """Return the GUID an entry carries, as the feed wrote it."""
    guid: str | None = entry.get("id") or entry.get("guid")
    return guid


def _whole_number(value: Any) -> int | None:
    """Return a whole number written in a feed, or None when it is not one."""
    with contextlib.suppress(ValueError, TypeError):
        return int(value)
    return None


class RSSDiscovery:
    """Discover podcasts and episodes from RSS feeds.

    Both entry points use the same injectable bounded byte fetcher.
    """

    def __init__(self, fetcher: FeedFetcher | None = None) -> None:
        self.fetcher = fetcher if fetcher is not None else FeedHttpClient()

    @property
    def name(self) -> str:
        """Return the discovery provider identifier."""
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
        feed = self._fetch_feed(rss_url)

        if feed.bozo and not feed.entries:
            logger.warning(
                "Failed to parse RSS feed for podcast %s",
                self._generate_slug(feed.feed.get("title", "Unknown Podcast")),
            )
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

        return DiscoveredPodcast(
            name=name,
            slug=self._generate_slug(name),
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
            since: Only discover episodes published at or after this date

        Returns:
            List of discovered episodes
        """
        if not podcast.rss_url:
            logger.warning("Podcast %s has no rss_url", podcast.slug)
            return []

        feed = self._fetch_feed(podcast.rss_url)

        if feed.bozo and not feed.entries:
            logger.warning("Failed to parse RSS feed for podcast %s", podcast.slug)
            return []

        episodes = []
        identity = _FeedIdentity.of(feed)

        for entry in feed.entries:
            episode = self._parse_episode(entry, identity)

            if episode:
                # Filter by date if specified
                if not published_in_window(episode.published_at, since):
                    continue

                episodes.append(episode)

        logger.info("Discovered %s episodes from RSS feed", len(episodes))
        return episodes

    def _fetch_feed(self, url: str) -> Any:
        """Parse bounded bytes with their final base URL and HTTP charset."""
        response = self.fetcher.fetch(url)
        # Relative values inherit the base URL, so its query (which may hold
        # an access token) must not become part of stored links or GUIDs.
        base_url = urlsplit(response.final_url)._replace(query="", fragment="")
        return feedparser.parse(
            BytesIO(response.body),
            response_headers={
                "content-type": response.content_type,
                "content-location": base_url.geturl(),
            },
        )

    def _parse_episode(
        self,
        entry: Any,
        identity: _FeedIdentity = _NO_FEED_IDENTITY,
    ) -> DiscoveredEpisode | None:
        """Parse an episode from a feed entry.

        The item link and GUID are kept only when they name this item
        (``identity``); the season is carried with the episode number.
        """
        title = entry.get("title")
        if not title:
            return None

        # Parse published date. It is reported in UTC: SQLite stores only the
        # wall-clock time, so a kept offset would be read back as another
        # instant and the title-and-time lookup would miss the stored row.
        published_at = None
        if entry.get("published"):
            try:
                published_at = parsedate_to_datetime(entry.published)
                if published_at.tzinfo is None:
                    published_at = published_at.replace(tzinfo=UTC)
                published_at = published_at.astimezone(UTC)
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
            episode_number = _whole_number(ep_num)

        # Seasons start at 1; a missing, 0 or unreadable season means none.
        season_number = _whole_number(entry.get("itunes_season"))
        if season_number is not None and season_number < 1:
            season_number = None

        # Get episode URL, unless it is the show's page rather than the item's
        episode_url = identity.episode_url(entry.get("link"))

        # Get GUID for deduplication, unless other items of the feed share it
        rss_guid = identity.rss_guid(_entry_guid(entry))

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
            season_number=season_number,
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
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])

        except (ValueError, TypeError):
            pass

        return None

    def _generate_slug(self, name: str) -> str:
        """Generate the common discovery slug for a podcast name."""
        return str(slugify_podcast_name(name))

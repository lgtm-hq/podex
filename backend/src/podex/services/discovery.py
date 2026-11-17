"""Discovery service interface and common types.

This module defines the interface for discovering podcasts and episodes
from various sources (podscripts.co, RSS, Spotify, etc.).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from podex.models import Podcast


@dataclass
# This flat data record mirrors its published fields; nesting changes the contract.
# pylint: disable-next=too-many-instance-attributes
class DiscoveredEpisode:
    """Episode discovered from a source.

    Contains all metadata we can extract about an episode,
    with source-specific IDs for deduplication across sources.
    """

    title: str
    published_at: datetime | None = None
    duration_seconds: int | None = None
    episode_number: int | None = None
    description: str | None = None
    episode_url: str | None = None
    thumbnail_url: str | None = None

    # Source-specific IDs
    youtube_id: str | None = None
    spotify_uri: str | None = None
    rss_guid: str | None = None
    apple_id: str | None = None

    # Transcript availability (podscripts.co already has transcripts)
    has_transcript: bool = False

    # Which source discovered this episode
    discovery_source: str | None = None


@dataclass
# This flat data record mirrors its published fields; nesting changes the contract.
# pylint: disable-next=too-many-instance-attributes
class DiscoveredPodcast:
    """Podcast discovered from a source."""

    name: str
    slug: str | None = None
    description: str | None = None
    cover_url: str | None = None
    author: str | None = None

    # Source-specific IDs
    rss_url: str | None = None
    spotify_id: str | None = None
    apple_id: str | None = None
    youtube_channel_id: str | None = None
    podscripts_slug: str | None = None

    # Which source discovered this podcast
    discovery_source: str | None = None


@dataclass
class DiscoveryResult:
    """Result of a discovery operation."""

    new_episodes: int = 0
    updated_episodes: int = 0
    total_discovered: int = 0
    errors: list[str] = field(default_factory=list)
    discovered_episodes: list[DiscoveredEpisode] = field(default_factory=list)


class DiscoveryProvider(Protocol):
    """Interface for episode discovery sources."""

    @property
    def name(self) -> str:
        """Return the source name."""
        ...

    @property
    def is_configured(self) -> bool:
        """Check if this source is properly configured and ready to use."""
        ...

    def discover_podcast(self, identifier: str) -> DiscoveredPodcast | None:
        """Discover podcast metadata from this source.

        Args:
            identifier: Source-specific identifier (e.g., RSS URL, Spotify ID, slug)

        Returns:
            DiscoveredPodcast with metadata, or None if not found
        """
        ...

    def discover_episodes(
        self,
        podcast: Podcast,
        since: datetime | None = None,
    ) -> list[DiscoveredEpisode]:
        """Discover episodes for a podcast.

        Args:
            podcast: The podcast to discover episodes for
            since: Only discover episodes published strictly after this
                date. Episodes without a publication date are kept, since
                nothing shows them to be older (see ``published_after``).

        Returns:
            List of discovered episodes
        """
        ...


@dataclass(frozen=True, slots=True)
class KnownEpisodes:
    """Identifiers of the episodes the catalog already holds for one podcast.

    An episode counts as known when it would match an existing catalog row by
    the identifiers a listing carries: its episode URL or its episode number.
    """

    episode_urls: frozenset[str] = frozenset()
    episode_numbers: frozenset[int] = frozenset()

    @property
    def is_empty(self) -> bool:
        """Return whether the catalog holds no identifiable episode."""
        return not self.episode_urls and not self.episode_numbers

    def includes(self, episode: DiscoveredEpisode) -> bool:
        """Return whether ``episode`` is already in the catalog.

        Args:
            episode: A discovered episode.

        Returns:
            True when its URL or episode number is already known.
        """
        if episode.episode_url and episode.episode_url in self.episode_urls:
            return True
        return bool(
            episode.episode_number and episode.episode_number in self.episode_numbers,
        )


def published_after(
    published_at: datetime | None,
    since: datetime | None,
) -> bool:
    """Return whether an episode falls inside a ``since`` discovery window.

    The window is strictly after ``since``: an episode dated exactly at the
    cut-off is outside it. Undated episodes are inside it, since nothing shows
    them to be older. Naive datetimes are read as UTC.

    Args:
        published_at: The episode's publication time, when known.
        since: The window's exclusive lower bound, or None for no bound.

    Returns:
        True when the episode belongs in the window.
    """
    if since is None or published_at is None:
        return True
    return _as_utc(published_at) > _as_utc(since)


def _as_utc(value: datetime) -> datetime:
    """Return ``value`` with UTC attached when it carries no timezone."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def slugify_podcast_name(name: str) -> str:
    """Generate the stable slug shared by podcast discovery providers.

    Args:
        name: Podcast display name.

    Returns:
        A lowercase slug with punctuation removed and separators collapsed.
    """
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")

"""Discovery service interface and common types.

This module defines the interface for discovering podcasts and episodes
from various sources (podscripts.co, RSS, Spotify, etc.).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from podex.models import Podcast


@dataclass
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
            since: Only discover episodes published after this date

        Returns:
            List of discovered episodes
        """
        ...

"""Discovery service interface and common types.

This module defines the interface for discovering podcasts and episodes
from various sources (podscripts.co, RSS, Spotify, etc.).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol, runtime_checkable

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
    # A feed may number per season; the number is then only unique within it.
    season_number: int | None = None

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
            since: Only discover episodes published at or after this date.
                Episodes without a publication date are kept, since nothing
                shows them to be older (see ``published_in_window``).

        Returns:
            List of discovered episodes
        """
        ...


@dataclass(frozen=True, slots=True)
class ListingCursor:
    """Catalog state that lets a listing walk stop before the listing ends.

    Only listing URLs count: an episode is known when the catalog already
    stores its listing URL for this podcast. A number-only match is not
    evidence that the catalog holds that listing entry.
    """

    known_episode_urls: frozenset[str]


@dataclass(slots=True)
class EpisodeListing:
    """Episodes read from one provider listing, and how the walk ended.

    Attributes:
        episodes: Episodes read, in listing order.
        errors: Failure descriptors for pages that could not be read.
        complete: True only when the walk reached the listing's real end
            (an empty page) without a failed page and without stopping early.
    """

    episodes: list[DiscoveredEpisode] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    complete: bool = False


@runtime_checkable
class ListingCursorProvider(Protocol):
    """Optional discovery capability: a paged listing that can use a cursor."""

    def discover_listing(
        self,
        podcast: Podcast,
        *,
        cursor: ListingCursor | None = None,
    ) -> EpisodeListing:
        """Walk the podcast's listing, stopping early only through ``cursor``.

        Args:
            podcast: The podcast to discover episodes for.
            cursor: Catalog state allowing an early stop, or None to walk the
                whole listing.

        Returns:
            The episodes read, page failures, and whether the walk completed.
        """
        ...

    def listing_url_prefix(self, podcast: Podcast) -> str | None:
        """Return the prefix every listing URL of ``podcast`` starts with.

        Args:
            podcast: The podcast whose listing is walked.

        Returns:
            The URL prefix, or None when the podcast has no listing here.
        """
        ...


def published_in_window(
    published_at: datetime | None,
    since: datetime | None,
) -> bool:
    """Return whether an episode falls inside a ``since`` discovery window.

    The window is at or after ``since``: an episode dated exactly at the
    cut-off is inside it, so a day-granular date at midnight UTC is not lost.
    Undated episodes are inside it, since nothing shows them to be older.
    Naive datetimes are read as UTC.

    Args:
        published_at: The episode's publication time, when known.
        since: The window's inclusive lower bound, or None for no bound.

    Returns:
        True when the episode belongs in the window.
    """
    if since is None or published_at is None:
        return True
    return as_utc(published_at) >= as_utc(since)


def as_utc(value: datetime) -> datetime:
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

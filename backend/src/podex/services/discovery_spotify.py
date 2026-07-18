"""Spotify discovery service.

Discovers podcasts and episodes from Spotify's API.
Requires Spotify API credentials (client_id and client_secret).
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from podex.services.discovery import (
    DiscoveredEpisode,
    DiscoveredPodcast,
)

if TYPE_CHECKING:
    from podex.models import Podcast

logger = logging.getLogger(__name__)


class SpotifyDiscovery:
    """Discover podcasts and episodes from Spotify's API.

    Args:
        client_id: Spotify API client ID (or from SPOTIFY_CLIENT_ID env var)
        client_secret: Spotify API client secret (or from SPOTIFY_CLIENT_SECRET env var)
    """

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
    ) -> None:
        self.client_id = client_id or os.getenv("SPOTIFY_CLIENT_ID")
        self.client_secret = client_secret or os.getenv("SPOTIFY_CLIENT_SECRET")
        self._spotify = None

    @property
    def name(self) -> str:
        return "spotify"

    @property
    def is_configured(self) -> bool:
        """Check if Spotify API credentials are configured."""
        return bool(self.client_id and self.client_secret)

    def _get_client(self) -> Any:
        """Get or create Spotify client."""
        if self._spotify is None:
            if not self.is_configured:
                raise ValueError(
                    "Spotify credentials not configured. Set "
                    "SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET env vars."
                )

            try:
                import spotipy
                from spotipy.oauth2 import SpotifyClientCredentials
            except ImportError as e:
                raise ImportError(
                    "spotipy not installed. Run: uv pip install spotipy"
                ) from e

            auth_manager = SpotifyClientCredentials(
                client_id=self.client_id,
                client_secret=self.client_secret,
            )
            self._spotify = spotipy.Spotify(auth_manager=auth_manager)

        return self._spotify

    def discover_podcast(self, spotify_id: str) -> DiscoveredPodcast | None:
        """Discover podcast metadata from Spotify.

        Args:
            spotify_id: The Spotify show ID

        Returns:
            DiscoveredPodcast with metadata, or None if not found
        """
        try:
            client = self._get_client()
            show = client.show(spotify_id, market="US")
        except Exception as e:
            logger.warning(f"Failed to fetch Spotify show {spotify_id}: {e}")
            return None

        if not show:
            return None

        name = show.get("name", "Unknown Podcast")
        description = show.get("description")
        publisher = show.get("publisher")

        # Get cover image (prefer largest)
        cover_url = None
        images = show.get("images", [])
        if images:
            # Sort by size (largest first)
            sorted_images = sorted(
                images,
                key=lambda x: x.get("height", 0) or 0,
                reverse=True,
            )
            cover_url = sorted_images[0].get("url")

        # Generate slug from name
        slug = self._generate_slug(name)

        return DiscoveredPodcast(
            name=name,
            slug=slug,
            description=description,
            author=publisher,
            cover_url=cover_url,
            spotify_id=spotify_id,
            discovery_source=self.name,
        )

    def discover_episodes(
        self,
        podcast: Podcast,
        since: datetime | None = None,
        limit: int = 50,
    ) -> list[DiscoveredEpisode]:
        """Discover episodes from Spotify.

        Args:
            podcast: The podcast to discover episodes for
            since: Only discover episodes published after this date
            limit: Maximum episodes per API call (Spotify max is 50)

        Returns:
            List of discovered episodes
        """
        if not podcast.spotify_id:
            logger.warning(f"Podcast {podcast.slug} has no spotify_id")
            return []

        try:
            client = self._get_client()
        except (ValueError, ImportError) as e:
            logger.warning(f"Cannot use Spotify discovery: {e}")
            return []

        episodes: list[DiscoveredEpisode] = []
        offset = 0
        max_episodes = 1000  # Safety limit

        while offset < max_episodes:
            try:
                result = client.show_episodes(
                    podcast.spotify_id,
                    limit=min(limit, 50),
                    offset=offset,
                    market="US",
                )
            except Exception as e:
                logger.warning(f"Failed to fetch episodes from Spotify: {e}")
                break

            items = result.get("items", [])
            if not items:
                break

            for item in items:
                episode = self._parse_episode(item)

                if episode:
                    # Filter by date if specified
                    if since and episode.published_at and episode.published_at < since:
                        # Spotify returns newest first, so we can stop
                        logger.debug(
                            f"Stopping at episode {episode.title} (before {since})"
                        )
                        return episodes

                    episodes.append(episode)

            # Check if there are more episodes
            if not result.get("next"):
                break

            offset += len(items)

        logger.info(f"Discovered {len(episodes)} episodes from Spotify")
        return episodes

    def _parse_episode(self, item: dict[str, Any]) -> DiscoveredEpisode | None:
        """Parse an episode from Spotify API response."""
        name = item.get("name")
        if not name:
            return None

        # Parse release date
        published_at = None
        release_date = item.get("release_date")
        if release_date:
            try:
                # Spotify returns dates in YYYY-MM-DD format
                if len(release_date) == 10:
                    published_at = datetime.strptime(release_date, "%Y-%m-%d").replace(
                        tzinfo=UTC
                    )
                elif len(release_date) == 7:
                    published_at = datetime.strptime(release_date, "%Y-%m").replace(
                        tzinfo=UTC
                    )
                elif len(release_date) == 4:
                    published_at = datetime.strptime(release_date, "%Y").replace(
                        tzinfo=UTC
                    )
            except ValueError:
                pass

        # Duration in milliseconds
        duration_seconds = None
        duration_ms = item.get("duration_ms")
        if duration_ms:
            duration_seconds = duration_ms // 1000

        # Episode URL
        episode_url = None
        external_urls = item.get("external_urls", {})
        episode_url = external_urls.get("spotify")

        # Spotify URI for deduplication
        spotify_uri = item.get("uri")

        # Description
        description = item.get("description")
        if description and len(description) > 1000:
            description = description[:1000] + "..."

        # Thumbnail
        thumbnail_url = None
        images = item.get("images", [])
        if images:
            sorted_images = sorted(
                images,
                key=lambda x: x.get("height", 0) or 0,
                reverse=True,
            )
            thumbnail_url = sorted_images[0].get("url")

        return DiscoveredEpisode(
            title=name,
            published_at=published_at,
            duration_seconds=duration_seconds,
            description=description,
            episode_url=episode_url,
            thumbnail_url=thumbnail_url,
            spotify_uri=spotify_uri,
            has_transcript=False,  # Spotify doesn't provide transcripts
            discovery_source=self.name,
        )

    def _generate_slug(self, name: str) -> str:
        """Generate a URL-safe slug from a name."""
        import re

        slug = name.lower().strip()
        slug = re.sub(r"[^\w\s-]", "", slug)
        slug = re.sub(r"[\s_]+", "-", slug)
        slug = re.sub(r"-+", "-", slug)
        return slug.strip("-")

    def search_podcasts(
        self,
        query: str,
        limit: int = 10,
    ) -> list[DiscoveredPodcast]:
        """Search for podcasts on Spotify.

        Args:
            query: Search query
            limit: Maximum number of results

        Returns:
            List of discovered podcasts
        """
        try:
            client = self._get_client()
            result = client.search(q=query, type="show", limit=limit, market="US")
        except Exception as e:
            logger.warning(f"Spotify search failed: {e}")
            return []

        podcasts = []
        for item in result.get("shows", {}).get("items", []):
            spotify_id = item.get("id")
            if spotify_id:
                podcast = self.discover_podcast(spotify_id)
                if podcast:
                    podcasts.append(podcast)

        return podcasts

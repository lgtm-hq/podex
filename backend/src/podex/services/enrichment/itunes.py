"""iTunes Search API enrichment provider for podcasts."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx

from podex.models.media import MediaType
from podex.services.enrichment.base import (
    EnrichmentProvider,
    EnrichmentResult,
    EnrichmentSource,
)

if TYPE_CHECKING:
    from podex.models.media import Media

logger = logging.getLogger(__name__)


class iTunesProvider(EnrichmentProvider):  # type: ignore[misc, unused-ignore]
    """Enrich podcasts from iTunes Search API.

    Free API, no authentication required.
    Rate limited to ~20 calls/minute.

    Args:
        base_url: Override for the iTunes Search API root.
        requests_per_second: Rate limit (default 0.3 = ~18/min to stay under limit).
    """

    BASE_URL = "https://itunes.apple.com"

    source = EnrichmentSource.ITUNES

    SUPPORTED_TYPES = {MediaType.PODCAST}

    def __init__(
        self,
        *,
        base_url: str | None = None,
        requests_per_second: float = 0.3,
    ) -> None:
        super().__init__(requests_per_second)
        self.client = httpx.Client(
            base_url=base_url or self.BASE_URL,
            timeout=30.0,
        )

    def supports_media_type(self, media_type: str | MediaType) -> bool:
        """Check if iTunes supports this media type."""
        try:
            return MediaType(media_type) in self.SUPPORTED_TYPES
        except ValueError:
            return False

    def search_and_enrich(self, media: Media) -> EnrichmentResult | None:
        """Search iTunes and enrich podcast.

        Args:
            media: The media item to enrich.

        Returns:
            EnrichmentResult if found, None otherwise.
        """
        if not self.supports_media_type(media.type):
            return None

        self.rate_limiter.wait_sync()

        # Search for podcast
        results = self._search_podcasts(media.title)

        if not results:
            logger.debug(f"No iTunes results for: {media.title}")
            return None

        # Find best match
        best_match = self._find_best_match(results, media)
        if not best_match:
            return None

        podcast, confidence = best_match
        return self._build_result(podcast, confidence)

    def _search_podcasts(self, query: str) -> list[dict[str, Any]]:
        """Search iTunes for podcasts.

        Args:
            query: Search query string.

        Returns:
            List of podcast results.
        """
        try:
            response = self.client.get(
                "/search",
                params={
                    "term": query,
                    "media": "podcast",
                    "entity": "podcast",
                    "limit": 10,
                },
            )
            response.raise_for_status()
            data: dict[str, Any] = response.json()
            result: list[dict[str, Any]] = data.get("results", [])
            return result
        except httpx.HTTPError as e:
            logger.warning(f"iTunes search error for '{query}': {e}")
            return []

    def _find_best_match(
        self,
        results: list[dict[str, Any]],
        media: Media,
    ) -> tuple[dict[str, Any], float] | None:
        """Find best matching podcast result.

        Args:
            results: Search results from iTunes.
            media: Original media item.

        Returns:
            Tuple of (podcast_data, confidence) or None.
        """
        if not results:
            return None

        best_podcast = None
        best_score = 0.0

        for podcast in results[:5]:  # Check top 5 results
            podcast_name = podcast.get("collectionName", "")
            artist_name = podcast.get("artistName", "")

            # Calculate title similarity
            title_score = self._calculate_title_similarity(
                media.title,
                podcast_name,
            )

            # Boost if creator/author matches artist
            author_boost = 0.0
            if media.author:
                author_sim = self._calculate_title_similarity(
                    media.author,
                    artist_name,
                )
                if author_sim > 0.6:
                    author_boost = 0.15

            total_score = title_score + author_boost

            if total_score > best_score:
                best_score = total_score
                best_podcast = podcast

        if best_podcast and best_score >= 0.5:  # Lower threshold for podcasts
            return (best_podcast, min(best_score, 1.0))

        return None

    def _build_result(
        self,
        podcast: dict[str, Any],
        confidence: float,
    ) -> EnrichmentResult:
        """Build enrichment result from iTunes podcast data.

        Args:
            podcast: iTunes podcast data.
            confidence: Match confidence score.

        Returns:
            EnrichmentResult with all available data.
        """
        # Get highest quality artwork (replace 100x100 with 600x600)
        artwork_url = podcast.get("artworkUrl600")
        if not artwork_url:
            artwork_url = podcast.get("artworkUrl100", "")
            if artwork_url:
                artwork_url = artwork_url.replace("100x100", "600x600")

        # Description (not always available in search results)
        description = None  # iTunes search doesn't return full description

        # External IDs
        external_ids: dict[str, str | int] = {}

        collection_id = podcast.get("collectionId")
        if collection_id:
            external_ids["apple_podcasts_id"] = str(collection_id)

        # Build metadata
        metadata: dict[str, Any] = {}

        # Artist/creator
        artist_name = podcast.get("artistName")
        if artist_name:
            metadata["creator"] = artist_name

        # Genre
        primary_genre = podcast.get("primaryGenreName")
        if primary_genre:
            metadata["genres"] = [primary_genre]

        # All genres
        podcast.get("genreIds", [])
        genres = podcast.get("genres", [])
        if genres:
            metadata["genres"] = genres

        # Episode count
        track_count = podcast.get("trackCount")
        if track_count:
            metadata["episode_count"] = track_count

        # Feed URL
        feed_url = podcast.get("feedUrl")
        if feed_url:
            metadata["feed_url"] = feed_url

        # Release date
        release_date = podcast.get("releaseDate")
        if release_date:
            metadata["latest_release"] = release_date

        # Country
        country = podcast.get("country")
        if country:
            metadata["country"] = country

        # Content advisory
        content_advisory = podcast.get("contentAdvisoryRating")
        if content_advisory:
            metadata["content_rating"] = content_advisory

        # iTunes URL
        collection_view_url = podcast.get("collectionViewUrl")
        if collection_view_url:
            metadata["itunes_url"] = collection_view_url

        return EnrichmentResult(
            source=self.source,
            cover_url=artwork_url or None,
            description=description,
            external_ids=external_ids,
            metadata=metadata,
            confidence=confidence,
        )

    def close(self) -> None:
        """Close the HTTP client."""
        self.client.close()

    def __enter__(self) -> iTunesProvider:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

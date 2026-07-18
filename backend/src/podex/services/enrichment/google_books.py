"""Google Books API enrichment provider."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx

from podex.services.enrichment.base import (
    EnrichmentProvider,
    EnrichmentResult,
    EnrichmentSource,
)

if TYPE_CHECKING:
    from podex.models.media import Media

logger = logging.getLogger(__name__)


class GoogleBooksProvider(EnrichmentProvider):
    """Enrich books from Google Books API.

    Args:
        api_key: Optional Google API key for higher rate limits.
        requests_per_second: Rate limit for API calls.
    """

    BASE_URL = "https://www.googleapis.com/books/v1"

    source = EnrichmentSource.GOOGLE_BOOKS

    SUPPORTED_TYPES = {"book"}

    def __init__(
        self,
        api_key: str | None = None,
        requests_per_second: float = 1.0,
    ) -> None:
        super().__init__(requests_per_second)
        self.api_key = api_key
        params = {}
        if api_key:
            params["key"] = api_key
        self.client = httpx.Client(
            base_url=self.BASE_URL,
            params=params,
            timeout=30.0,
        )

    def supports_media_type(self, media_type: str) -> bool:
        """Check if Google Books supports this media type."""
        return media_type in self.SUPPORTED_TYPES

    def search_and_enrich(self, media: Media) -> EnrichmentResult | None:
        """Search Google Books and enrich media item.

        Args:
            media: The media item to enrich.

        Returns:
            EnrichmentResult if found, None otherwise.
        """
        if not self.supports_media_type(media.type):
            return None

        # If we already have a Google Books ID, fetch directly
        if media.google_books_id:
            self.rate_limiter.wait_sync()
            volume = self._get_volume(media.google_books_id)
            if volume:
                return self._build_result(volume, confidence=1.0)

        self.rate_limiter.wait_sync()

        # Build search query
        query = self._build_search_query(media.title, media.author)
        results = self._search(query)

        if not results:
            logger.debug(f"No Google Books results for: {media.title}")
            return None

        # Find best match
        best_match = self._find_best_match(results, media)
        if not best_match:
            return None

        volume, confidence = best_match
        return self._build_result(volume, confidence)

    def _build_search_query(self, title: str, author: str | None) -> str:
        """Build Google Books search query.

        Args:
            title: Book title.
            author: Optional author name.

        Returns:
            Search query string.
        """
        query_parts = [f"intitle:{title}"]
        if author:
            query_parts.append(f"inauthor:{author}")
        return "+".join(query_parts)

    def _search(self, query: str) -> list[dict[str, Any]]:
        """Search Google Books.

        Args:
            query: Search query string.

        Returns:
            List of volume results.
        """
        try:
            response = self.client.get(
                "/volumes",
                params={
                    "q": query,
                    "maxResults": 10,
                    "printType": "books",
                    "langRestrict": "en",
                },
            )
            response.raise_for_status()
            data: dict[str, Any] = response.json()
            result: list[dict[str, Any]] = data.get("items", [])
            return result
        except httpx.HTTPError as e:
            logger.warning(f"Google Books search error: {e}")
            return []

    def _get_volume(self, volume_id: str) -> dict[str, Any] | None:
        """Fetch a specific volume by ID.

        Args:
            volume_id: Google Books volume ID.

        Returns:
            Volume data or None.
        """
        try:
            response = self.client.get(f"/volumes/{volume_id}")
            response.raise_for_status()
            result: dict[str, Any] = response.json()
            return result
        except httpx.HTTPError as e:
            logger.warning(f"Google Books volume error for {volume_id}: {e}")
            return None

    def _find_best_match(
        self,
        results: list[dict[str, Any]],
        media: Media,
    ) -> tuple[dict[str, Any], float] | None:
        """Find best matching result.

        Args:
            results: Search results from Google Books.
            media: Original media item.

        Returns:
            Tuple of (volume_data, confidence) or None.
        """
        if not results:
            return None

        best_volume = None
        best_score = 0.0

        for item in results[:5]:  # Check top 5 results
            volume_info = item.get("volumeInfo", {})
            result_title = volume_info.get("title", "")

            # Calculate title similarity
            title_score = self._calculate_title_similarity(
                media.title,
                result_title,
            )

            # Check author match
            author_boost = 0.0
            if media.author:
                authors = volume_info.get("authors", [])
                for author in authors:
                    author_sim = self._calculate_title_similarity(
                        media.author,
                        author,
                    )
                    if author_sim > 0.7:
                        author_boost = 0.15
                        break

            total_score = title_score + author_boost

            if total_score > best_score:
                best_score = total_score
                best_volume = item

        if best_volume and best_score >= 0.7:
            return (best_volume, min(best_score, 1.0))

        return None

    def _build_result(
        self, volume: dict[str, Any], confidence: float
    ) -> EnrichmentResult:
        """Build enrichment result from Google Books volume.

        Args:
            volume: Google Books volume data.
            confidence: Match confidence score.

        Returns:
            EnrichmentResult with all available data.
        """
        volume_info = volume.get("volumeInfo", {})

        # Cover URL - prefer larger images
        cover_url = None
        image_links = volume_info.get("imageLinks", {})
        for size in ["large", "medium", "small", "thumbnail"]:
            if size in image_links:
                cover_url = image_links[size]
                # Convert http to https
                if cover_url.startswith("http://"):
                    cover_url = cover_url.replace("http://", "https://")
                break

        # Description
        description = volume_info.get("description")

        # External IDs
        google_id = volume.get("id")
        external_ids: dict[str, str | int] = {}
        if google_id is not None:
            external_ids["google_books_id"] = google_id

        # Get ISBN
        identifiers = volume_info.get("industryIdentifiers", [])
        for identifier in identifiers:
            id_type = identifier.get("type", "")
            id_value = identifier.get("identifier", "")
            if id_type == "ISBN_13":
                external_ids["isbn_13"] = id_value
            elif id_type == "ISBN_10":
                external_ids["isbn_10"] = id_value

        # Build metadata
        metadata: dict[str, Any] = {}

        # Page count
        page_count = volume_info.get("pageCount")
        if page_count:
            metadata["page_count"] = page_count

        # Categories/genres
        categories = volume_info.get("categories", [])
        if categories:
            metadata["genres"] = categories

        # Rating
        average_rating = volume_info.get("averageRating")
        if average_rating:
            metadata["google_books_rating"] = average_rating
            metadata["google_books_rating_count"] = volume_info.get(
                "ratingsCount",
                0,
            )

        # Publisher
        publisher = volume_info.get("publisher")
        if publisher:
            metadata["publisher"] = publisher

        # Published date
        published_date = volume_info.get("publishedDate")
        if published_date:
            metadata["published_date"] = published_date

        # Language
        language = volume_info.get("language")
        if language:
            metadata["language"] = language

        # Authors (store full list)
        authors = volume_info.get("authors", [])
        if authors:
            metadata["authors"] = authors

        # Preview/info links
        preview_link = volume_info.get("previewLink")
        if preview_link:
            metadata["preview_link"] = preview_link

        # Maturity rating
        maturity = volume_info.get("maturityRating")
        if maturity and maturity != "NOT_MATURE":
            metadata["maturity_rating"] = maturity

        return EnrichmentResult(
            source=self.source,
            cover_url=cover_url,
            description=description,
            external_ids=external_ids,
            metadata=metadata,
            confidence=confidence,
        )

    def close(self) -> None:
        """Close the HTTP client."""
        self.client.close()

    def __enter__(self) -> GoogleBooksProvider:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

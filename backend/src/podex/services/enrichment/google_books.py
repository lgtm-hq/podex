"""Google Books API enrichment provider."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx

from podex.models.media import MediaType
from podex.services.enrichment.base import (
    EnrichmentProvider,
    EnrichmentResult,
    EnrichmentSource,
    describe_http_error,
)

if TYPE_CHECKING:
    from podex.models.media import Media

logger = logging.getLogger(__name__)


class GoogleBooksProvider(EnrichmentProvider):  # type: ignore[misc, unused-ignore]
    """Enrich books from Google Books API.

    Args:
        api_key: Optional Google API key for higher rate limits.
        base_url: Override for the Google Books API root.
        requests_per_second: Rate limit for API calls.
    """

    BASE_URL = "https://www.googleapis.com/books/v1"

    source = EnrichmentSource.GOOGLE_BOOKS

    SUPPORTED_TYPES = {MediaType.BOOK}

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        requests_per_second: float = 1.0,
    ) -> None:
        super().__init__(requests_per_second)
        self.api_key = api_key
        params = {}
        if api_key:
            params["key"] = api_key
        self.client = httpx.Client(
            base_url=base_url or self.BASE_URL,
            params=params,
            timeout=30.0,
        )

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
            logger.debug("No Google Books results for: %s", media.title)
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
            data: dict[str, Any] = self._response_json(response)
            result: list[dict[str, Any]] = data.get("items", [])
            return result
        except httpx.HTTPError as e:
            logger.warning("Google Books search error: %s", describe_http_error(e))
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
            result: dict[str, Any] = self._response_json(response)
            return result
        except httpx.HTTPError as e:
            logger.warning(
                "Google Books volume error for %s: %s",
                volume_id,
                describe_http_error(e),
            )
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

            title_author_score = self._book_match_score(
                media, result_title, volume_info.get("authors", [])
            )

            total_score = title_author_score

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
        metadata = self._build_metadata(volume_info=volume_info)

        # Keep the result field mapping explicit at this provider boundary.
        # pylint: disable=duplicate-code
        return EnrichmentResult(
            source=self.source,
            cover_url=cover_url,
            description=description,
            external_ids=external_ids,
            metadata=metadata,
            confidence=confidence,
        )
        # pylint: enable=duplicate-code

    def _build_metadata(
        self,
        volume_info: dict[str, Any],
    ) -> dict[str, Any]:
        """Collect provider-specific metadata from the response.

        Args:
            volume_info: Provider response data for the metadata fields.

        Returns:
            Metadata fields supported by this provider.
        """
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

        self._add_publication_metadata(metadata=metadata, volume_info=volume_info)

        # Maturity rating
        maturity = volume_info.get("maturityRating")
        if maturity and maturity != "NOT_MATURE":
            metadata["maturity_rating"] = maturity

        return metadata

    def _add_publication_metadata(
        self,
        metadata: dict[str, Any],
        volume_info: dict[str, Any],
    ) -> None:
        """Add publication fields to the provider metadata.

        Args:
            metadata: Metadata dictionary to populate.
            volume_info: Provider response data for these fields.
        """
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

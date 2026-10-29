"""OpenLibrary enrichment provider.

OpenLibrary is a free, open-source book database with no API key required.
Used as a fallback for books when Google Books doesn't have data.
"""

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


class OpenLibraryProvider(EnrichmentProvider):  # type: ignore[misc, unused-ignore]
    """Enrich books from OpenLibrary (free, no API key).

    Args:
        base_url: Override for the Open Library API root.
        requests_per_second: Rate limit for API calls.
    """

    BASE_URL = "https://openlibrary.org"
    COVERS_URL = "https://covers.openlibrary.org/b"

    source = EnrichmentSource.OPEN_LIBRARY

    SUPPORTED_TYPES = {MediaType.BOOK}

    def __init__(
        self,
        *,
        base_url: str | None = None,
        requests_per_second: float = 1.0,
    ) -> None:
        super().__init__(requests_per_second)
        self.base_url = base_url or self.BASE_URL
        self.client = httpx.Client(
            timeout=30.0,
            follow_redirects=True,
        )

    def search_and_enrich(self, media: Media) -> EnrichmentResult | None:
        """Search OpenLibrary and enrich media item.

        Args:
            media: The media item to enrich.

        Returns:
            EnrichmentResult if found, None otherwise.
        """
        if not self.supports_media_type(media.type):
            return None

        # If we have an OpenLibrary ID, fetch directly
        if media.open_library_id:
            self.rate_limiter.wait_sync()
            work = self._get_work(media.open_library_id)
            if work:
                return self._build_result_from_work(work, confidence=1.0)

        self.rate_limiter.wait_sync()

        # Search by title and author
        results = self._search(media.title, media.author)

        if not results:
            logger.debug("No OpenLibrary results for: %s", media.title)
            return None

        # Find best match
        best_match = self._find_best_match(results, media)
        if not best_match:
            return None

        doc, confidence = best_match

        # Get work details for more complete data
        work_key = doc.get("key")
        if work_key:
            self.rate_limiter.wait_sync()
            work = self._get_work(work_key.replace("/works/", ""))
            if work:
                return self._build_result_from_work(
                    work,
                    confidence,
                    search_doc=doc,
                )

        # Fall back to search result data only
        return self._build_result_from_search(doc, confidence)

    def _search(self, title: str, author: str | None) -> list[dict[str, Any]]:
        """Search OpenLibrary.

        Args:
            title: Book title.
            author: Optional author name.

        Returns:
            List of search result docs.
        """
        try:
            params: dict[str, str | int] = {
                "title": title,
                "limit": 10,
            }
            if author:
                params["author"] = author

            response = self.client.get(
                f"{self.base_url}/search.json",
                params=params,
            )
            data: dict[str, Any] = self._response_json(response)
            result: list[dict[str, Any]] = data.get("docs", [])
            return result
        except httpx.HTTPError as e:
            logger.warning("OpenLibrary search error: %s", e)
            return []

    def _get_work(self, work_id: str) -> dict[str, Any] | None:
        """Fetch work details by ID.

        Args:
            work_id: OpenLibrary work ID (without /works/ prefix).

        Returns:
            Work data or None.
        """
        try:
            response = self.client.get(
                f"{self.base_url}/works/{work_id}.json",
            )
            result: dict[str, Any] = self._response_json(response)
            return result
        except httpx.HTTPError as e:
            logger.warning("OpenLibrary work error for %s: %s", work_id, e)
            return None

    def _find_best_match(
        self,
        results: list[dict[str, Any]],
        media: Media,
    ) -> tuple[dict[str, Any], float] | None:
        """Find best matching result.

        Args:
            results: Search results from OpenLibrary.
            media: Original media item.

        Returns:
            Tuple of (doc, confidence) or None.
        """
        if not results:
            return None

        best_doc = None
        best_score = 0.0

        for doc in results[:5]:  # Check top 5 results
            result_title = doc.get("title", "")

            title_author_score = self._book_match_score(
                media, result_title, doc.get("author_name", [])
            )

            # Boost if publication year matches
            year_boost = 0.0
            if media.year:
                first_publish = doc.get("first_publish_year")
                if first_publish and first_publish == media.year:
                    year_boost = 0.1

            total_score = title_author_score + year_boost

            if total_score > best_score:
                best_score = total_score
                best_doc = doc

        if best_doc and best_score >= 0.7:
            return (best_doc, min(best_score, 1.0))

        return None

    def _get_cover_url(
        self,
        cover_id: int | str | None = None,
        olid: str | None = None,
        isbn: str | None = None,
        size: str = "L",
    ) -> str | None:
        """Build cover URL from various identifiers.

        Args:
            cover_id: OpenLibrary cover ID.
            olid: OpenLibrary ID (edition or work).
            isbn: ISBN-10 or ISBN-13.
            size: Size (S, M, L).

        Returns:
            Cover URL or None.
        """
        if cover_id:
            return f"{self.COVERS_URL}/id/{cover_id}-{size}.jpg"
        if olid:
            return f"{self.COVERS_URL}/olid/{olid}-{size}.jpg"
        if isbn:
            return f"{self.COVERS_URL}/isbn/{isbn}-{size}.jpg"
        return None

    def _build_result_from_work(
        self,
        work: dict[str, Any],
        confidence: float,
        search_doc: dict[str, Any] | None = None,
    ) -> EnrichmentResult:
        """Build enrichment result from work data.

        Args:
            work: OpenLibrary work data.
            confidence: Match confidence score.
            search_doc: Optional search result doc with additional data.

        Returns:
            EnrichmentResult with all available data.
        """
        # Cover URL
        cover_url = None
        covers = work.get("covers", [])
        if covers:
            cover_url = self._get_cover_url(cover_id=covers[0])

        # Fall back to search doc covers
        if not cover_url and search_doc:
            cover_i = search_doc.get("cover_i")
            if cover_i:
                cover_url = self._get_cover_url(cover_id=cover_i)

        # Description
        description = None
        desc = work.get("description")
        if isinstance(desc, dict):
            description = desc.get("value")
        elif isinstance(desc, str):
            description = desc

        # External IDs
        work_key = work.get("key", "")
        work_id = work_key.replace("/works/", "") if work_key else None
        external_ids: dict[str, str | int] = {}
        if work_id:
            external_ids["open_library_id"] = work_id

        # Get ISBN from search doc if available
        if search_doc:
            isbns = search_doc.get("isbn", [])
            if isbns:
                for isbn in isbns:
                    if len(isbn) == 13:
                        external_ids["isbn_13"] = isbn
                        break
                    if len(isbn) == 10 and "isbn_10" not in external_ids:
                        external_ids["isbn_10"] = isbn

        # Build metadata
        metadata = self._build_metadata(search_doc=search_doc, work=work)

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
        search_doc: dict[str, Any] | None,
        work: dict[str, Any],
    ) -> dict[str, Any]:
        """Collect provider-specific metadata from the response.

        Args:
            search_doc: Provider response data for the metadata fields.
            work: Provider response data for the metadata fields.

        Returns:
            Metadata fields supported by this provider.
        """
        metadata: dict[str, Any] = {}

        # Subjects/genres
        subjects = work.get("subjects", [])
        if subjects:
            # Subjects can be strings or dicts
            genre_list = []
            for subj in subjects[:10]:  # Limit to 10
                if isinstance(subj, dict):
                    genre_list.append(subj.get("name", ""))
                else:
                    genre_list.append(str(subj))
            metadata["genres"] = [g for g in genre_list if g]

        # Add data from search doc
        if search_doc:
            # Authors
            authors = search_doc.get("author_name", [])
            if authors:
                metadata["authors"] = authors

            # First publish year
            first_publish = search_doc.get("first_publish_year")
            if first_publish:
                metadata["first_publish_year"] = first_publish

            # Number of editions
            edition_count = search_doc.get("edition_count")
            if edition_count:
                metadata["edition_count"] = edition_count

            # Languages
            languages = search_doc.get("language", [])
            if languages:
                metadata["languages"] = languages

            # Number of pages (median across editions)
            pages = search_doc.get("number_of_pages_median")
            if pages:
                metadata["page_count"] = pages

        return metadata

    def _build_result_from_search(
        self,
        doc: dict[str, Any],
        confidence: float,
    ) -> EnrichmentResult:
        """Build enrichment result from search doc only.

        Args:
            doc: Search result document.
            confidence: Match confidence score.

        Returns:
            EnrichmentResult with available data.
        """
        # Cover URL
        cover_url = None
        cover_i = doc.get("cover_i")
        if cover_i:
            cover_url = self._get_cover_url(cover_id=cover_i)

        # External IDs
        external_ids: dict[str, str | int] = {}
        work_key = doc.get("key", "")
        if work_key:
            work_id = work_key.replace("/works/", "")
            external_ids["open_library_id"] = work_id

        isbns = doc.get("isbn", [])
        if isbns:
            for isbn in isbns:
                if len(isbn) == 13:
                    external_ids["isbn_13"] = isbn
                    break
                if len(isbn) == 10 and "isbn_10" not in external_ids:
                    external_ids["isbn_10"] = isbn

        # Build metadata
        metadata: dict[str, Any] = {}

        # Authors
        authors = doc.get("author_name", [])
        if authors:
            metadata["authors"] = authors

        # Subjects/genres
        subjects = doc.get("subject", [])
        if subjects:
            metadata["genres"] = subjects[:10]

        # First publish year
        first_publish = doc.get("first_publish_year")
        if first_publish:
            metadata["first_publish_year"] = first_publish

        # Number of pages
        pages = doc.get("number_of_pages_median")
        if pages:
            metadata["page_count"] = pages

        # Keep the result field mapping explicit at this provider boundary.
        # pylint: disable=duplicate-code
        return EnrichmentResult(
            source=self.source,
            cover_url=cover_url,
            description=None,  # Search doesn't include description
            external_ids=external_ids,
            metadata=metadata,
            confidence=confidence,
        )
        # pylint: enable=duplicate-code

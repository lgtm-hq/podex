"""CrossRef API enrichment provider."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import httpx

from podex.models.media import MediaType
from podex.services.enrichment.base import (
    EnrichmentProvider,
    EnrichmentResult,
    EnrichmentSource,
    canonicalize_doi,
)

if TYPE_CHECKING:
    from podex.models.media import Media

logger = logging.getLogger(__name__)


class CrossRefProvider(EnrichmentProvider):  # type: ignore[misc, unused-ignore]
    """Enrich academic papers from CrossRef DOI registry.

    Uses CrossRef REST API (free, polite pool with mailto header).

    Args:
        mailto: Email address for polite pool (higher rate limits).
        base_url: Override for the CrossRef API root.
        requests_per_second: Rate limit for API calls.
    """

    BASE_URL = "https://api.crossref.org"

    source = EnrichmentSource.CROSSREF
    SUPPORTED_TYPES = {MediaType.STUDY, MediaType.ARTICLE}

    def __init__(
        self,
        *,
        mailto: str | None = None,
        base_url: str | None = None,
        requests_per_second: float = 10.0,
    ) -> None:
        super().__init__(requests_per_second)
        self.mailto = mailto

        # Include mailto in User-Agent for polite pool
        user_agent = "Podex/1.0 (Academic Media Index)"
        if mailto:
            user_agent += f" (mailto:{mailto})"

        self.client = httpx.Client(
            base_url=base_url or self.BASE_URL,
            headers={"User-Agent": user_agent},
            timeout=30.0,
        )

    def search_and_enrich(self, media: Media) -> EnrichmentResult | None:
        """Search CrossRef and enrich media item.

        Args:
            media: The media item to enrich.

        Returns:
            EnrichmentResult if found, None otherwise.
        """
        if not self.supports_media_type(media.type):
            return None

        # 1. Direct lookup by DOI
        if media.doi:
            self.rate_limiter.wait_sync()
            work = self._get_work_by_doi(media.doi)
            if work:
                return self._build_result(work, confidence=1.0)

        # 2. Search by title
        self.rate_limiter.wait_sync()
        results = self._search(media.title, media.author)

        if not results:
            logger.debug("No CrossRef results for: %s", media.title)
            return None

        # Find best match
        best_match = self._find_best_match(results, media)
        if not best_match:
            return None

        work, confidence = best_match
        return self._build_result(work, confidence)

    def _get_work_by_doi(self, doi: str) -> dict[str, Any] | None:
        """Fetch work by DOI.

        Args:
            doi: DOI to lookup.

        Returns:
            Work data or None.
        """
        # Normalize DOI - remove common prefixes (CrossRef DOIs are
        # case-insensitive, so lowercase for a canonical path)
        normalized_doi = canonicalize_doi(doi).lower()

        try:
            response = self.client.get(f"/works/{normalized_doi}")
            if response.status_code == 404:
                return None
            data: dict[str, Any] = self._response_json(response)
            result: dict[str, Any] | None = data.get("message")
            return result
        except httpx.HTTPError as e:
            logger.warning("CrossRef lookup error for %s: %s", doi, e)
            return None

    def _search(self, title: str, author: str | None) -> list[dict[str, Any]]:
        """Search CrossRef by title and author.

        Args:
            title: Paper title.
            author: Optional author name.

        Returns:
            List of work results.
        """
        params: dict[str, str | int] = {
            "query.title": title,
            "rows": 10,
        }

        if author:
            # Take first author's last name
            first_author = author.split(",")[0].split(" and ")[0].strip()
            last_name = first_author.split()[-1] if first_author else None
            if last_name:
                params["query.author"] = last_name

        if self.mailto:
            params["mailto"] = self.mailto

        try:
            response = self.client.get("/works", params=params)
            data: dict[str, Any] = self._response_json(response)
            result: list[dict[str, Any]] = data.get("message", {}).get("items", [])
            return result
        except httpx.HTTPError as e:
            logger.warning("CrossRef search error: %s", e)
            return []

    def _find_best_match(
        self,
        results: list[dict[str, Any]],
        media: Media,
    ) -> tuple[dict[str, Any], float] | None:
        """Find best matching result.

        Args:
            results: Search results from CrossRef.
            media: Original media item.

        Returns:
            Tuple of (work, confidence) or None.
        """
        if not results:
            return None

        best_work = None
        best_score = 0.0

        for work in results[:5]:
            # CrossRef returns title as a list
            titles = work.get("title", [])
            result_title = titles[0] if titles else ""

            # Calculate title similarity
            title_score = self._calculate_title_similarity(media.title, result_title)

            # Use the same author names for matching and stored metadata.
            author_boost = 0.0
            if media.author and any(
                self._calculate_title_similarity(media.author, author_name) > 0.5
                for author_name in self._author_names(work)
            ):
                author_boost = 0.15

            # Check year match
            year_boost = 0.0
            if media.year:
                published = work.get("published-print") or work.get("published-online")
                if published:
                    date_parts = published.get("date-parts", [[]])
                    if date_parts and date_parts[0]:
                        work_year = date_parts[0][0]
                        if media.year == work_year:
                            year_boost = 0.05

            total_score = title_score + author_boost + year_boost

            if total_score > best_score:
                best_score = total_score
                best_work = work

        if best_work and best_score >= 0.6:
            return (best_work, min(best_score, 0.9))

        return None

    def _build_result(
        self, work: dict[str, Any], confidence: float
    ) -> EnrichmentResult:
        """Build enrichment result from CrossRef work.

        Args:
            work: CrossRef work data.
            confidence: Match confidence score.

        Returns:
            EnrichmentResult with all available data.
        """
        external_ids: dict[str, str | int] = {
            "doi": work["DOI"],
        }

        # CrossRef doesn't typically have other IDs directly,
        # but may have ISSN for the journal
        issn = work.get("ISSN", [])
        if issn:
            external_ids["issn"] = issn[0]

        metadata = self._build_metadata(work=work)

        # Abstract (not always available from CrossRef)
        abstract = work.get("abstract", "")
        if abstract:
            # CrossRef abstracts may have JATS XML tags, strip them

            abstract = re.sub(r"<[^>]+>", "", abstract)

        # Keep the result field mapping explicit at this provider boundary.
        # pylint: disable=duplicate-code
        return EnrichmentResult(
            source=self.source,
            cover_url=None,  # CrossRef doesn't provide cover images
            description=abstract if abstract else None,
            external_ids=external_ids,
            metadata=metadata,
            confidence=confidence,
        )
        # pylint: enable=duplicate-code

    def _build_metadata(
        self,
        work: dict[str, Any],
    ) -> dict[str, Any]:
        """Collect provider-specific metadata from the response.

        Args:
            work: Provider response data for the metadata fields.

        Returns:
            Metadata fields supported by this provider.
        """
        metadata: dict[str, Any] = {}

        # Title (explicit contract for cross-source title verification)
        titles = work.get("title", [])
        if titles:
            metadata["title"] = titles[0]

        self._add_authors_metadata(metadata=metadata, work=work)

        # Publisher
        if work.get("publisher"):
            metadata["publisher"] = work["publisher"]

        # Journal (container-title)
        container_title = work.get("container-title", [])
        if container_title:
            metadata["journal"] = container_title[0]

        self._add_publication_metadata(metadata=metadata, work=work)

        # Citation counts
        if work.get("is-referenced-by-count") is not None:
            metadata["citation_count"] = work["is-referenced-by-count"]
        if work.get("references-count") is not None:
            metadata["reference_count"] = work["references-count"]

        # Type
        if work.get("type"):
            metadata["work_type"] = work["type"]

        self._add_license_metadata(metadata=metadata, work=work)

        # Subject areas
        subjects = work.get("subject", [])
        if subjects:
            metadata["subjects"] = subjects

        return metadata

    def _add_license_metadata(
        self,
        metadata: dict[str, Any],
        work: dict[str, Any],
    ) -> None:
        """Add license fields to the provider metadata.

        Args:
            metadata: Metadata dictionary to populate.
            work: Provider response data for these fields.
        """
        # License/open access
        licenses = work.get("license", [])
        if licenses:
            # Get the most recent license
            license_url = licenses[0].get("URL")
            if license_url:
                metadata["license_url"] = license_url
                # Check if it's open access — parse the host rather than
                # substring-matching so a lookalike URL cannot spoof it.
                host = urlparse(license_url).hostname or ""
                if host == "creativecommons.org" or host.endswith(
                    ".creativecommons.org",
                ):
                    metadata["open_access"] = True

    def _add_publication_metadata(
        self,
        metadata: dict[str, Any],
        work: dict[str, Any],
    ) -> None:
        """Add publication fields to the provider metadata.

        Args:
            metadata: Metadata dictionary to populate.
            work: Provider response data for these fields.
        """
        # Publication date
        published = work.get("published-print") or work.get("published-online")
        if published:
            date_parts = published.get("date-parts", [[]])
            if date_parts and date_parts[0]:
                year = date_parts[0][0]
                month = date_parts[0][1] if len(date_parts[0]) > 1 else None
                day = date_parts[0][2] if len(date_parts[0]) > 2 else None

                if month and day:
                    metadata["publication_date"] = f"{year}-{month:02d}-{day:02d}"
                elif month:
                    metadata["publication_date"] = f"{year}-{month:02d}"
                else:
                    metadata["publication_date"] = str(year)

                metadata["year"] = year

    @staticmethod
    def _author_names(work: dict[str, Any]) -> list[str]:
        """Read the author names shared by matching and metadata.

        Args:
            work: CrossRef work response.

        Returns:
            Nonempty author names in source order.
        """
        names: list[str] = []
        for author in work.get("author", []):
            if author.get("given") and author.get("family"):
                names.append(f"{author['given']} {author['family']}")
            elif author.get("family"):
                names.append(author["family"])
            elif author.get("name"):
                names.append(author["name"])
        return names

    def _add_authors_metadata(
        self,
        metadata: dict[str, Any],
        work: dict[str, Any],
    ) -> None:
        """Add authors fields to the provider metadata.

        Args:
            metadata: Metadata dictionary to populate.
            work: Provider response data for these fields.
        """
        author_names = self._author_names(work)
        if author_names:
            metadata["authors"] = author_names

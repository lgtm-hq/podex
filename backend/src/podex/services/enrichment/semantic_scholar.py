"""Semantic Scholar API enrichment provider."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx

from podex.services.enrichment.base import (
    EnrichmentProvider,
    EnrichmentResult,
    EnrichmentSource,
    canonicalize_doi,
)

if TYPE_CHECKING:
    from podex.models.media import Media

logger = logging.getLogger(__name__)


class SemanticScholarProvider(EnrichmentProvider):  # type: ignore[misc, unused-ignore]
    """Enrich academic papers from Semantic Scholar.

    Uses Semantic Scholar Academic Graph API (free, 100 req/sec).

    Args:
        api_key: Optional API key for higher rate limits.
        requests_per_second: Rate limit for API calls.
    """

    BASE_URL = "https://api.semanticscholar.org/graph/v1"

    source = EnrichmentSource.SEMANTIC_SCHOLAR
    SUPPORTED_TYPES = {"study", "article"}

    # Fields to request from the API
    PAPER_FIELDS = ",".join(
        [
            "paperId",
            "externalIds",
            "title",
            "abstract",
            "venue",
            "year",
            "authors",
            "citationCount",
            "influentialCitationCount",
            "fieldsOfStudy",
            "publicationDate",
            "openAccessPdf",
        ]
    )

    def __init__(
        self,
        api_key: str | None = None,
        requests_per_second: float = 10.0,
    ) -> None:
        super().__init__(requests_per_second)
        headers = {}
        if api_key:
            headers["x-api-key"] = api_key
        self.client = httpx.Client(
            base_url=self.BASE_URL,
            headers=headers,
            timeout=30.0,
        )

    def supports_media_type(self, media_type: str) -> bool:
        """Check if Semantic Scholar supports this media type."""
        return media_type in self.SUPPORTED_TYPES

    def search_and_enrich(self, media: Media) -> EnrichmentResult | None:
        """Search Semantic Scholar and enrich media item.

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
            paper = self._get_paper_by_id(f"DOI:{canonicalize_doi(media.doi)}")
            if paper:
                return self._build_result(paper, confidence=1.0)

        # 2. Direct lookup by PMID
        if media.pubmed_id:
            self.rate_limiter.wait_sync()
            paper = self._get_paper_by_id(f"PMID:{media.pubmed_id}")
            if paper:
                return self._build_result(paper, confidence=0.95)

        # 3. Search by title
        self.rate_limiter.wait_sync()
        results = self._search(media.title)

        if not results:
            logger.debug(f"No Semantic Scholar results for: {media.title}")
            return None

        # Find best match
        best_match = self._find_best_match(results, media)
        if not best_match:
            return None

        paper, confidence = best_match
        return self._build_result(paper, confidence)

    def _get_paper_by_id(self, paper_id: str) -> dict[str, Any] | None:
        """Fetch paper by ID (DOI, PMID, S2 ID, etc.).

        Args:
            paper_id: Paper identifier (e.g., "DOI:10.1234/...", "PMID:12345").

        Returns:
            Paper data or None.
        """
        try:
            response = self.client.get(
                f"/paper/{paper_id}",
                params={"fields": self.PAPER_FIELDS},
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            result: dict[str, Any] = response.json()
            return result
        except httpx.HTTPError as e:
            logger.warning(f"Semantic Scholar lookup error for {paper_id}: {e}")
            return None

    def _search(self, title: str) -> list[dict[str, Any]]:
        """Search Semantic Scholar by title.

        Args:
            title: Paper title.

        Returns:
            List of paper results.
        """
        try:
            response = self.client.get(
                "/paper/search",
                params={
                    "query": title,
                    "fields": self.PAPER_FIELDS,
                    "limit": 10,
                },
            )
            response.raise_for_status()
            data: dict[str, Any] = response.json()
            result: list[dict[str, Any]] = data.get("data", [])
            return result
        except httpx.HTTPError as e:
            logger.warning(f"Semantic Scholar search error: {e}")
            return []

    def _find_best_match(
        self,
        results: list[dict[str, Any]],
        media: Media,
    ) -> tuple[dict[str, Any], float] | None:
        """Find best matching result.

        Args:
            results: Search results from Semantic Scholar.
            media: Original media item.

        Returns:
            Tuple of (paper, confidence) or None.
        """
        if not results:
            return None

        best_paper = None
        best_score = 0.0

        for paper in results[:5]:
            result_title = paper.get("title", "")

            # Calculate title similarity
            title_score = self._calculate_title_similarity(media.title, result_title)

            # Check author match
            author_boost = 0.0
            if media.author:
                paper_authors = paper.get("authors", [])
                for author in paper_authors:
                    author_name = author.get("name", "")
                    author_sim = self._calculate_title_similarity(
                        media.author,
                        author_name,
                    )
                    if author_sim > 0.5:
                        author_boost = 0.15
                        break

            # Check year match
            year_boost = 0.0
            if media.year and paper.get("year") and media.year == paper["year"]:
                year_boost = 0.05

            total_score = title_score + author_boost + year_boost

            if total_score > best_score:
                best_score = total_score
                best_paper = paper

        if best_paper and best_score >= 0.6:
            return (best_paper, min(best_score, 0.9))

        return None

    def _build_result(
        self, paper: dict[str, Any], confidence: float
    ) -> EnrichmentResult:
        """Build enrichment result from Semantic Scholar paper.

        Args:
            paper: Semantic Scholar paper data.
            confidence: Match confidence score.

        Returns:
            EnrichmentResult with all available data.
        """
        external_ids: dict[str, str | int] = {
            "semantic_scholar_id": paper["paperId"],
        }

        # Extract external IDs
        paper_external_ids = paper.get("externalIds", {})
        if paper_external_ids:
            if paper_external_ids.get("DOI"):
                external_ids["doi"] = paper_external_ids["DOI"]
            if paper_external_ids.get("PubMed"):
                external_ids["pubmed_id"] = paper_external_ids["PubMed"]
            if paper_external_ids.get("ArXiv"):
                external_ids["arxiv_id"] = paper_external_ids["ArXiv"]

        metadata: dict[str, Any] = {}

        # Authors
        authors = paper.get("authors", [])
        if authors:
            metadata["authors"] = [a.get("name", "") for a in authors]

        # Venue (journal/conference)
        if paper.get("venue"):
            metadata["journal"] = paper["venue"]

        # Year
        if paper.get("year"):
            metadata["year"] = paper["year"]

        # Publication date
        if paper.get("publicationDate"):
            metadata["publication_date"] = paper["publicationDate"]

        # Citation counts
        if paper.get("citationCount") is not None:
            metadata["citation_count"] = paper["citationCount"]
        if paper.get("influentialCitationCount") is not None:
            metadata["influential_citation_count"] = paper["influentialCitationCount"]

        # Fields of study
        if paper.get("fieldsOfStudy"):
            metadata["fields_of_study"] = paper["fieldsOfStudy"]

        # Open access PDF
        open_access = paper.get("openAccessPdf")
        if open_access and open_access.get("url"):
            metadata["open_access_pdf_url"] = open_access["url"]

        return EnrichmentResult(
            source=self.source,
            cover_url=None,  # Semantic Scholar doesn't provide cover images
            description=paper.get("abstract"),
            external_ids=external_ids,
            metadata=metadata,
            confidence=confidence,
        )

    def close(self) -> None:
        """Close the HTTP client."""
        self.client.close()

    def __enter__(self) -> SemanticScholarProvider:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

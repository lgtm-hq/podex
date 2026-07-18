"""Academic paper enrichment with multi-source verification.

Orchestrates enrichment of academic papers (studies, articles) from multiple
authoritative sources with cross-validation to ensure accuracy.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import TYPE_CHECKING, Any

from podex.services.enrichment.base import (
    EnrichmentResult,
    EnrichmentSource,
    VerifiedEnrichmentResult,
)
from podex.services.enrichment.crossref import CrossRefProvider
from podex.services.enrichment.pubmed import PubMedProvider
from podex.services.enrichment.semantic_scholar import SemanticScholarProvider

if TYPE_CHECKING:
    from podex.models.media import Media

logger = logging.getLogger(__name__)


class AcademicEnricher:
    """Multi-source verification enricher for academic papers.

    Queries PubMed, Semantic Scholar, and CrossRef in parallel,
    cross-validates results using DOI as canonical identifier,
    and requires 2+ source confirmation for high-confidence enrichment.

    Args:
        ncbi_api_key: Optional NCBI API key for PubMed (higher rate limits).
        crossref_mailto: Optional email for CrossRef polite pool.
    """

    SUPPORTED_TYPES = {"study", "article"}

    def __init__(
        self,
        ncbi_api_key: str | None = None,
        crossref_mailto: str | None = None,
    ) -> None:
        self.providers = {
            EnrichmentSource.PUBMED: PubMedProvider(api_key=ncbi_api_key),
            EnrichmentSource.SEMANTIC_SCHOLAR: SemanticScholarProvider(),
            EnrichmentSource.CROSSREF: CrossRefProvider(mailto=crossref_mailto),
        }

    def supports_media_type(self, media_type: str) -> bool:
        """Check if this enricher supports the given media type."""
        return media_type in self.SUPPORTED_TYPES

    def enrich_with_verification(
        self,
        media: Media,
        require_doi: bool = False,
        min_sources: int = 2,
    ) -> VerifiedEnrichmentResult | None:
        """Enrich with multi-source cross-validation.

        Args:
            media: The media item to enrich.
            require_doi: If True, only return results with DOI verification.
            min_sources: Minimum number of sources required for verification.

        Returns:
            VerifiedEnrichmentResult if successful, None otherwise.
        """
        if not self.supports_media_type(media.type):
            return None

        # 1. Collect results from all providers
        results: dict[EnrichmentSource, EnrichmentResult] = {}
        for source, provider in self.providers.items():
            try:
                result = provider.search_and_enrich(media)
                if result and result.confidence >= 0.5:
                    results[source] = result
                    logger.debug(
                        f"Got result from {source.value} for '{media.title}' "
                        f"(confidence: {result.confidence:.2f})"
                    )
            except Exception:
                logger.exception(f"Error querying {source.value}")
                continue

        if not results:
            logger.debug(f"No academic enrichment results for: {media.title}")
            return None

        # 2. Cross-validate by DOI
        verified = self._verify_by_doi(results)

        # 3. If DOI verification succeeded with enough sources
        if len(verified) >= min_sources:
            logger.info(
                f"DOI-verified enrichment for '{media.title}' "
                f"from {len(verified)} sources: {[s.value for s in verified]}"
            )
            return self._aggregate_results(
                verified,
                doi_verified=True,
                confidence=0.95,
            )

        # 4. Fall back to title+author matching if DOI verification failed
        if not require_doi:
            title_verified = self._verify_by_title(results)
            if len(title_verified) >= min_sources:
                logger.info(
                    f"Title-verified enrichment for '{media.title}' "
                    f"from {len(title_verified)} sources: "
                    f"{[s.value for s in title_verified]}"
                )
                return self._aggregate_results(
                    title_verified,
                    doi_verified=False,
                    confidence=0.80,
                )

        # 5. Single source fallback (lower confidence)
        if results and not require_doi:
            # Prefer source with highest confidence
            best_source = max(results.keys(), key=lambda s: results[s].confidence)
            best_result = results[best_source]

            logger.info(
                f"Single-source enrichment for '{media.title}' "
                f"from {best_source.value} (confidence: {best_result.confidence:.2f})"
            )

            return VerifiedEnrichmentResult(
                source=best_source,
                cover_url=best_result.cover_url,
                description=best_result.description,
                external_ids=best_result.external_ids,
                metadata=best_result.metadata,
                confidence=best_result.confidence * 0.9,  # Penalize single-source
                verified_by=[best_source],
                source_results={best_source: best_result},
                doi_verified=False,
            )

        return None

    def _verify_by_doi(
        self,
        results: dict[EnrichmentSource, EnrichmentResult],
    ) -> dict[EnrichmentSource, EnrichmentResult]:
        """Filter to results with matching DOI.

        Args:
            results: Results from multiple sources.

        Returns:
            Dict of sources that agree on the same DOI.
        """
        # Extract DOIs from all results
        dois: dict[EnrichmentSource, str] = {}
        for source, result in results.items():
            doi = result.external_ids.get("doi")
            if doi:
                # Normalize DOI
                normalized = self._normalize_doi(str(doi))
                dois[source] = normalized

        if not dois:
            return {}

        # Find most common DOI
        doi_counts = Counter(dois.values())
        most_common_doi, count = doi_counts.most_common(1)[0]

        if count < 2:
            return {}  # No agreement

        # Return only results with matching DOI
        return {
            source: result
            for source, result in results.items()
            if dois.get(source) == most_common_doi
        }

    def _verify_by_title(
        self,
        results: dict[EnrichmentSource, EnrichmentResult],
    ) -> dict[EnrichmentSource, EnrichmentResult]:
        """Verify results by title similarity.

        Uses the fact that all results came from the same query,
        so we check if titles are similar enough across sources.

        Args:
            results: Results from multiple sources.

        Returns:
            Dict of sources with similar titles.
        """
        if len(results) < 2:
            return results

        # Get titles from metadata or descriptions
        titles: dict[EnrichmentSource, str] = {}
        for source, result in results.items():
            title = result.metadata.get("title")
            if not title and result.description:
                # Use first sentence of description as pseudo-title
                title = result.description.split(".")[0][:100]
            if title:
                titles[source] = self._normalize_title(str(title))

        if not titles:
            # All results came from same search, assume they match
            return results

        # Check pairwise title similarity
        sources = list(titles.keys())
        verified_sources: set[EnrichmentSource] = set()

        for i, source1 in enumerate(sources):
            for source2 in sources[i + 1 :]:
                title1 = titles[source1]
                title2 = titles[source2]

                # Simple word overlap check
                words1 = set(title1.split())
                words2 = set(title2.split())
                if words1 and words2:
                    intersection = words1 & words2
                    union = words1 | words2
                    similarity = len(intersection) / len(union)

                    if similarity > 0.5:
                        verified_sources.add(source1)
                        verified_sources.add(source2)

        if len(verified_sources) >= 2:
            return {s: results[s] for s in verified_sources}

        return {}

    def _normalize_doi(self, doi: str) -> str:
        """Normalize DOI for comparison.

        Args:
            doi: DOI string.

        Returns:
            Normalized DOI.
        """
        doi = doi.lower().strip()
        # Remove common prefixes
        for prefix in ["https://doi.org/", "http://doi.org/", "doi:"]:
            if doi.startswith(prefix):
                doi = doi[len(prefix) :]
        return doi

    def _normalize_title(self, title: str) -> str:
        """Normalize title for comparison.

        Args:
            title: Title string.

        Returns:
            Normalized title.
        """
        import re

        title = title.lower().strip()
        # Remove common articles
        title = re.sub(r"^(the|a|an)\s+", "", title)
        # Remove punctuation
        title = re.sub(r"[^\w\s]", "", title)
        # Normalize whitespace
        title = " ".join(title.split())
        return title

    def _aggregate_results(
        self,
        results: dict[EnrichmentSource, EnrichmentResult],
        doi_verified: bool,
        confidence: float,
    ) -> VerifiedEnrichmentResult:
        """Merge results from multiple sources.

        Prioritizes data from more authoritative sources.

        Args:
            results: Verified results from multiple sources.
            doi_verified: Whether DOI was used for verification.
            confidence: Aggregated confidence score.

        Returns:
            Merged VerifiedEnrichmentResult.
        """
        # Priority order for data merging
        priority = [
            EnrichmentSource.PUBMED,  # Medical authority
            EnrichmentSource.CROSSREF,  # DOI authority
            EnrichmentSource.SEMANTIC_SCHOLAR,  # Rich metadata
        ]

        merged_ids: dict[str, str | int] = {}
        merged_metadata: dict[str, Any] = {}
        description = None
        primary_source = None

        for source in priority:
            if source not in results:
                continue

            result = results[source]

            # Track primary source (first available in priority order)
            if primary_source is None:
                primary_source = source

            # Merge external IDs (first wins)
            for key, value in result.external_ids.items():
                if key not in merged_ids:
                    merged_ids[key] = value

            # Merge metadata (first wins)
            for key, value in result.metadata.items():
                if key not in merged_metadata:
                    merged_metadata[key] = value

            # First description wins
            if not description and result.description:
                description = result.description

        # Collect citation counts from all sources for comparison
        citation_counts = []
        for result in results.values():
            if "citation_count" in result.metadata:
                citation_counts.append(result.metadata["citation_count"])
        if citation_counts:
            # Use the maximum citation count (most up-to-date)
            merged_metadata["citation_count"] = max(citation_counts)

        return VerifiedEnrichmentResult(
            source=primary_source or EnrichmentSource.PUBMED,
            cover_url=None,  # Academic sources don't provide covers
            description=description,
            external_ids=merged_ids,
            metadata=merged_metadata,
            confidence=confidence,
            verified_by=list(results.keys()),
            source_results=results,
            doi_verified=doi_verified,
        )

    def close(self) -> None:
        """Close all provider HTTP clients."""
        for provider in self.providers.values():
            provider.close()

    def __enter__(self) -> AcademicEnricher:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

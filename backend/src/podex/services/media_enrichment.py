"""Media enrichment service.

Orchestrates enrichment of media items from multiple external sources
based on media type (books, movies, TV shows, etc.).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from podex.services.academic_enrichment import AcademicEnricher
from podex.services.enrichment.base import (
    EnrichmentProvider,
    EnrichmentResult,
    EnrichmentSource,
    VerifiedEnrichmentResult,
)
from podex.services.enrichment.google_books import GoogleBooksProvider
from podex.services.enrichment.itunes import iTunesProvider
from podex.services.enrichment.omdb import OMDBProvider
from podex.services.enrichment.open_library import OpenLibraryProvider
from podex.services.enrichment.tmdb import TMDBProvider
from podex.services.enrichment.tmdb_person import TMDBPersonProvider
from podex.services.enrichment.wikipedia import WikipediaProvider

if TYPE_CHECKING:
    from podex.models.media import Media

logger = logging.getLogger(__name__)


# Provider priority by media type (Wikipedia is a universal fallback, added separately)
PROVIDER_PRIORITY: dict[str, list[EnrichmentSource]] = {
    "book": [EnrichmentSource.GOOGLE_BOOKS, EnrichmentSource.OPEN_LIBRARY],
    "movie": [EnrichmentSource.TMDB, EnrichmentSource.OMDB],
    "tv_show": [EnrichmentSource.TMDB, EnrichmentSource.OMDB],
    "documentary": [EnrichmentSource.TMDB, EnrichmentSource.OMDB],
    # standup_special: TMDB first (show titles), then TMDB_PERSON (comedian names)
    "standup_special": [
        EnrichmentSource.TMDB,
        EnrichmentSource.OMDB,
        EnrichmentSource.TMDB_PERSON,
    ],
    "podcast": [EnrichmentSource.ITUNES],
    "person": [EnrichmentSource.TMDB_PERSON],
    # Academic types use multi-source verification via AcademicEnricher
    "study": [
        EnrichmentSource.PUBMED,
        EnrichmentSource.SEMANTIC_SCHOLAR,
        EnrichmentSource.CROSSREF,
    ],
    "article": [
        EnrichmentSource.SEMANTIC_SCHOLAR,
        EnrichmentSource.CROSSREF,
    ],
    "place": [],  # Wikipedia only
}

# Types that should use Wikipedia as a fallback
WIKIPEDIA_FALLBACK_TYPES = {
    "book",
    "movie",
    "tv_show",
    "documentary",
    "standup_special",
    "podcast",
    "person",
    "study",
    "article",
    "place",
}


class MediaEnricher:
    """Enrich media items with external data from multiple sources.

    Args:
        tmdb_api_key: TMDB API key (required for movies/TV).
        omdb_api_key: OMDB API key (optional, for IMDB data).
        google_books_api_key: Google Books API key (optional).
        ncbi_api_key: NCBI API key for PubMed (optional, higher rate limits).
        crossref_mailto: Email for CrossRef polite pool (optional).
    """

    def __init__(
        self,
        tmdb_api_key: str | None = None,
        omdb_api_key: str | None = None,
        google_books_api_key: str | None = None,
        ncbi_api_key: str | None = None,
        crossref_mailto: str | None = None,
    ) -> None:
        self.providers: dict[EnrichmentSource, EnrichmentProvider] = {}

        # Initialize providers based on available API keys
        if tmdb_api_key:
            self.providers[EnrichmentSource.TMDB] = TMDBProvider(tmdb_api_key)
            logger.info("TMDB provider initialized")
            self.providers[EnrichmentSource.TMDB_PERSON] = TMDBPersonProvider(
                tmdb_api_key
            )
            logger.info("TMDB Person provider initialized")

        if omdb_api_key:
            self.providers[EnrichmentSource.OMDB] = OMDBProvider(omdb_api_key)
            logger.info("OMDB provider initialized")

        # Google Books works without API key, but rate limited
        self.providers[EnrichmentSource.GOOGLE_BOOKS] = GoogleBooksProvider(
            api_key=google_books_api_key,
        )
        logger.info("Google Books provider initialized")

        # OpenLibrary is free and doesn't need an API key
        self.providers[EnrichmentSource.OPEN_LIBRARY] = OpenLibraryProvider()
        logger.info("OpenLibrary provider initialized")

        # iTunes is free and doesn't need an API key (for podcasts)
        self.providers[EnrichmentSource.ITUNES] = iTunesProvider()
        logger.info("iTunes provider initialized")

        # Academic enricher for studies and articles (multi-source verification)
        self.academic_enricher = AcademicEnricher(
            ncbi_api_key=ncbi_api_key,
            crossref_mailto=crossref_mailto,
        )
        logger.info("Academic enricher initialized")

        # Wikipedia as universal fallback (works for any media type)
        self.providers[EnrichmentSource.WIKIPEDIA] = WikipediaProvider()
        logger.info("Wikipedia provider initialized")

    def enrich(
        self,
        media: Media,
        min_confidence: float = 0.7,
        use_wikipedia_fallback: bool = True,
    ) -> EnrichmentResult | None:
        """Enrich a media item based on its type.

        For academic types (study, article), uses multi-source verification.
        For other types, tries providers in priority order until one returns
        a confident match. Falls back to Wikipedia if primary providers fail.

        Args:
            media: The media item to enrich.
            min_confidence: Minimum confidence threshold (0.0-1.0).
            use_wikipedia_fallback: Whether to try Wikipedia if others fail.

        Returns:
            EnrichmentResult if successful, None otherwise.
        """
        result: EnrichmentResult | None = None

        # Use academic enricher for studies and articles
        if media.type in ("study", "article"):
            try:
                result = self.academic_enricher.enrich_with_verification(media)
                if result and result.confidence >= min_confidence:
                    logger.info(
                        f"Enriched '{media.title}' via academic enricher "
                        f"(confidence: {result.confidence:.2f}, "
                        f"verified_by: {[s.value for s in result.verified_by]})"
                    )
                    return result
                elif result:
                    logger.debug(
                        f"Low confidence academic match: {result.confidence:.2f}"
                    )
                    result = None  # Reset for Wikipedia fallback
            except Exception:
                logger.exception("Error in academic enrichment")

        # Try primary providers for this media type
        if result is None:
            provider_sources = PROVIDER_PRIORITY.get(media.type, [])

            for source in provider_sources:
                provider = self.providers.get(source)
                if not provider:
                    logger.debug(f"Provider {source} not configured, skipping")
                    continue

                try:
                    result = provider.search_and_enrich(media)
                    if result and result.confidence >= min_confidence:
                        logger.info(
                            f"Enriched '{media.title}' from {source} "
                            f"(confidence: {result.confidence:.2f})"
                        )
                        return result
                    elif result:
                        conf = result.confidence
                        logger.debug(f"Low confidence from {source}: {conf:.2f}")
                        result = None  # Reset for next provider
                except Exception:
                    logger.exception(f"Error enriching from {source}")
                    continue

        # Wikipedia fallback for any media type
        if (
            result is None
            and use_wikipedia_fallback
            and media.type in WIKIPEDIA_FALLBACK_TYPES
        ):
            wikipedia = self.providers.get(EnrichmentSource.WIKIPEDIA)
            if wikipedia:
                try:
                    result = wikipedia.search_and_enrich(media)
                    if result and result.confidence >= min_confidence:
                        logger.info(
                            f"Enriched '{media.title}' from Wikipedia fallback "
                            f"(confidence: {result.confidence:.2f})"
                        )
                        return result
                    elif result:
                        logger.debug(
                            f"Low confidence Wikipedia match: {result.confidence:.2f}"
                        )
                except Exception:
                    logger.exception("Error in Wikipedia fallback")

        logger.debug(f"No enrichment found for: {media.title}")
        return None

    def apply_enrichment(self, media: Media, result: EnrichmentResult) -> None:
        """Apply enrichment result to a media item.

        Updates the media object in place with enriched data.

        Args:
            media: The media item to update.
            result: The enrichment result to apply.
        """
        # Update cover if we don't have one or the new one is from a better source
        if result.cover_url and not media.cover_url:
            media.cover_url = result.cover_url

        # Update description if we don't have one
        if result.description and not media.description:
            media.description = result.description

        # Update external IDs
        for key, value in result.external_ids.items():
            if key == "imdb_id" and not media.imdb_id:
                media.imdb_id = str(value)
            elif key == "tmdb_id" and not media.tmdb_id:
                media.tmdb_id = int(value) if isinstance(value, (int, str)) else None
            elif key == "google_books_id" and not media.google_books_id:
                media.google_books_id = str(value)
            elif key == "open_library_id" and not media.open_library_id:
                media.open_library_id = str(value)
            elif key == "wikipedia_id" and not media.wikipedia_id:
                media.wikipedia_id = str(value)
            elif key == "doi" and not media.doi:
                media.doi = str(value)
            elif key == "pubmed_id" and not media.pubmed_id:
                media.pubmed_id = str(value)
            elif key == "semantic_scholar_id" and not media.semantic_scholar_id:
                media.semantic_scholar_id = str(value)

        # Merge metadata
        if result.metadata:
            if media.metadata_json:
                # Merge, preferring new data for missing keys
                merged = {**result.metadata, **media.metadata_json}
                media.metadata_json = merged
            else:
                media.metadata_json = result.metadata

        # Update enrichment tracking
        media.enriched_at = datetime.now(UTC)
        media.enrichment_source = result.source.value
        media.enrichment_confidence = result.confidence

        # Handle VerifiedEnrichmentResult fields
        if isinstance(result, VerifiedEnrichmentResult):
            media.verification_sources = [s.value for s in result.verified_by]
            media.doi_verified = result.doi_verified

    def enrich_and_apply(
        self,
        media: Media,
        min_confidence: float = 0.7,
    ) -> bool:
        """Enrich a media item and apply the result.

        Convenience method that combines enrich() and apply_enrichment().

        Args:
            media: The media item to enrich.
            min_confidence: Minimum confidence threshold.

        Returns:
            True if enrichment was successful and applied, False otherwise.
        """
        result = self.enrich(media, min_confidence)
        if result:
            self.apply_enrichment(media, result)
            return True
        return False

    def get_available_providers(self) -> list[str]:
        """Get list of configured provider names."""
        return [source.value for source in self.providers]

    def close(self) -> None:
        """Close all provider HTTP clients."""
        for provider in self.providers.values():
            provider.close()
        self.academic_enricher.close()

    def __enter__(self) -> MediaEnricher:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

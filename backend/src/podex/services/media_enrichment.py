"""Media enrichment service.

Orchestrates enrichment of media items from multiple external sources
based on media type (books, movies, TV shows, etc.).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from podex.config import EnrichmentSettings, get_settings
from podex.models.media import MediaType
from podex.services.academic_enrichment import AcademicEnricher
from podex.services.enrichment.base import (
    EnrichmentProvider,
    EnrichmentResult,
    EnrichmentSource,
    VerifiedEnrichmentResult,
)
from podex.services.enrichment.crossref import CrossRefProvider
from podex.services.enrichment.google_books import GoogleBooksProvider
from podex.services.enrichment.itunes import iTunesProvider
from podex.services.enrichment.omdb import OMDBProvider
from podex.services.enrichment.open_library import OpenLibraryProvider
from podex.services.enrichment.pubmed import PubMedProvider
from podex.services.enrichment.semantic_scholar import SemanticScholarProvider
from podex.services.enrichment.tmdb import TMDBProvider
from podex.services.enrichment.tmdb_person import TMDBPersonProvider
from podex.services.enrichment.wikipedia import WikipediaProvider

if TYPE_CHECKING:
    from podex.models.media import Media

logger = logging.getLogger(__name__)


# Provider priority by media type (Wikipedia is a universal fallback, added separately)
PROVIDER_PRIORITY: dict[MediaType, list[EnrichmentSource]] = {
    MediaType.BOOK: [EnrichmentSource.GOOGLE_BOOKS, EnrichmentSource.OPEN_LIBRARY],
    MediaType.MOVIE: [EnrichmentSource.TMDB, EnrichmentSource.OMDB],
    MediaType.TV_SHOW: [EnrichmentSource.TMDB, EnrichmentSource.OMDB],
    MediaType.DOCUMENTARY: [EnrichmentSource.TMDB, EnrichmentSource.OMDB],
    MediaType.PODCAST: [EnrichmentSource.ITUNES],
    MediaType.PERSON: [EnrichmentSource.TMDB_PERSON],
    # Academic types use multi-source verification via AcademicEnricher
    MediaType.STUDY: [
        EnrichmentSource.PUBMED,
        EnrichmentSource.SEMANTIC_SCHOLAR,
        EnrichmentSource.CROSSREF,
    ],
    MediaType.ARTICLE: [
        EnrichmentSource.SEMANTIC_SCHOLAR,
        EnrichmentSource.CROSSREF,
    ],
    MediaType.PLACE: [],  # Wikipedia only
}

_ACADEMIC_SOURCES: frozenset[EnrichmentSource] = frozenset(
    {
        EnrichmentSource.PUBMED,
        EnrichmentSource.SEMANTIC_SCHOLAR,
        EnrichmentSource.CROSSREF,
    },
)


@dataclass(frozen=True)
class ProviderSpec:
    """Declarative description of one enrichment provider.

    The registry decides availability only; ``PROVIDER_PRIORITY`` alone
    decides ordering.
    """

    source: EnrichmentSource
    provider_class: type[Any]
    settings_accessor: Callable[[EnrichmentSettings], Any]
    requires_key: bool


_PROVIDER_SPECS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        EnrichmentSource.TMDB,
        TMDBProvider,
        lambda s: s.tmdb,
        True,
    ),
    ProviderSpec(
        EnrichmentSource.TMDB_PERSON,
        TMDBPersonProvider,
        lambda s: s.tmdb,
        True,
    ),
    ProviderSpec(
        EnrichmentSource.OMDB,
        OMDBProvider,
        lambda s: s.omdb,
        True,
    ),
    ProviderSpec(
        EnrichmentSource.GOOGLE_BOOKS,
        GoogleBooksProvider,
        lambda s: s.google_books,
        False,
    ),
    ProviderSpec(
        EnrichmentSource.OPEN_LIBRARY,
        OpenLibraryProvider,
        lambda s: s.open_library,
        False,
    ),
    ProviderSpec(
        EnrichmentSource.ITUNES,
        iTunesProvider,
        lambda s: s.itunes,
        False,
    ),
    ProviderSpec(
        EnrichmentSource.WIKIPEDIA,
        WikipediaProvider,
        lambda s: s.wikipedia,
        False,
    ),
    ProviderSpec(
        EnrichmentSource.PUBMED,
        PubMedProvider,
        lambda s: s.pubmed,
        False,
    ),
    ProviderSpec(
        EnrichmentSource.SEMANTIC_SCHOLAR,
        SemanticScholarProvider,
        lambda s: s.semantic_scholar,
        False,
    ),
    ProviderSpec(
        EnrichmentSource.CROSSREF,
        CrossRefProvider,
        lambda s: s.crossref,
        False,
    ),
)


def _instantiate_provider(
    spec: ProviderSpec,
    provider_settings: Any,
) -> EnrichmentProvider:
    """Build one provider instance from its settings sub-model."""
    kwargs: dict[str, Any] = {"base_url": provider_settings.base_url}
    if hasattr(provider_settings, "mailto"):
        if provider_settings.mailto:
            kwargs["mailto"] = provider_settings.mailto
    elif hasattr(provider_settings, "api_key") and provider_settings.api_key:
        kwargs["api_key"] = provider_settings.api_key
    return spec.provider_class(**kwargs)


def _build_providers(
    settings: EnrichmentSettings,
) -> dict[EnrichmentSource, EnrichmentProvider]:
    """Instantiate enabled providers from settings via the registry table."""
    providers: dict[EnrichmentSource, EnrichmentProvider] = {}
    for spec in _PROVIDER_SPECS:
        provider_settings = spec.settings_accessor(settings)
        if not provider_settings.enabled:
            logger.debug("Provider %s disabled in settings, skipping", spec.source)
            continue
        if spec.requires_key and not getattr(provider_settings, "api_key", ""):
            logger.debug(
                "Provider %s requires an API key and none is configured, skipping",
                spec.source,
            )
            continue
        providers[spec.source] = _instantiate_provider(spec, provider_settings)
        logger.info("%s provider initialized", spec.source.value)
    return providers


def _split_academic(
    providers: dict[EnrichmentSource, EnrichmentProvider],
) -> tuple[
    dict[EnrichmentSource, EnrichmentProvider],
    dict[EnrichmentSource, EnrichmentProvider],
]:
    """Partition providers into non-academic and academic maps."""
    academic = {
        source: provider
        for source, provider in providers.items()
        if source in _ACADEMIC_SOURCES
    }
    non_academic = {
        source: provider
        for source, provider in providers.items()
        if source not in _ACADEMIC_SOURCES
    }
    return non_academic, academic


# External-ID keys persisted to dedicated Media columns (string-valued).
# ``tmdb_id`` is handled separately because its column is an integer.
_EXTERNAL_ID_COLUMNS: frozenset[str] = frozenset(
    {
        "imdb_id",
        "google_books_id",
        "open_library_id",
        "wikipedia_id",
        "doi",
        "pubmed_id",
        "semantic_scholar_id",
    },
)


def apply_external_ids(
    *,
    media: Media,
    external_ids: dict[str, str | int],
) -> None:
    """Persist provider-emitted external IDs onto a media item.

    Keys with dedicated columns (imdb_id, tmdb_id, google_books_id,
    open_library_id, wikipedia_id, doi, pubmed_id, semantic_scholar_id)
    are written to those columns only when the column is currently falsy.
    Every remaining key (e.g. apple_podcasts_id, isbn_13, pmc_id, arxiv_id)
    is preserved in ``media.metadata_json["external_ids"]`` as a str->str
    map, merged without overwriting existing entries.

    Args:
        media: The media item to update in place.
        external_ids: Provider-emitted external identifiers.
    """
    extra_ids: dict[str, str] = {}
    for key, value in external_ids.items():
        if key == "tmdb_id":
            if not media.tmdb_id:
                media.tmdb_id = int(value) if isinstance(value, (int, str)) else None
        elif key in _EXTERNAL_ID_COLUMNS:
            if not getattr(media, key):
                setattr(media, key, str(value))
        else:
            extra_ids[key] = str(value)

    if extra_ids:
        metadata = dict(media.metadata_json or {})
        existing_ids = dict(metadata.get("external_ids") or {})
        metadata["external_ids"] = {**extra_ids, **existing_ids}
        media.metadata_json = metadata


class MediaEnricher:
    """Enrich media items with external data from multiple sources.

    Args:
        settings: Enrichment settings; defaults to ``get_settings().enrichment``.
        providers: Injected provider map that bypasses the registry. Injected
            providers are not closed by :meth:`close` (caller owns lifecycle).
    """

    def __init__(
        self,
        settings: EnrichmentSettings | None = None,
        providers: dict[EnrichmentSource, EnrichmentProvider] | None = None,
    ) -> None:
        if providers is not None:
            self._owns_providers = False
            non_academic, academic = _split_academic(providers)
            self.providers = non_academic
            self.academic_enricher = AcademicEnricher(providers=academic)
            self.academic_enricher._owns_providers = False
            return

        self._owns_providers = True
        if settings is None:
            settings = get_settings().enrichment
        built = _build_providers(settings)
        non_academic, academic = _split_academic(built)
        self.providers = non_academic
        self.academic_enricher = AcademicEnricher(providers=academic)
        self.academic_enricher._owns_providers = False
        logger.info("Academic enricher initialized")

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
        if media.type in (MediaType.STUDY, MediaType.ARTICLE):
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
            and media.type in PROVIDER_PRIORITY
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

        # Update external IDs (columns plus metadata_json fallback)
        apply_external_ids(media=media, external_ids=result.external_ids)

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

        # Handle VerifiedEnrichmentResult fields; ordinary results still
        # record their source so the pending-enrichment filter is satisfied.
        if isinstance(result, VerifiedEnrichmentResult):
            media.verification_sources = [s.value for s in result.verified_by]
            media.doi_verified = result.doi_verified
        elif media.verification_sources is None:
            media.verification_sources = [result.source.value]

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
        """Close provider HTTP clients owned by this enricher."""
        if not self._owns_providers:
            return
        for provider in self.providers.values():
            provider.close()
        for provider in self.academic_enricher.providers.values():
            provider.close()

    def __enter__(self) -> MediaEnricher:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

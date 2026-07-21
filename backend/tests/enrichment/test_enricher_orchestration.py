"""Tests for MediaEnricher orchestration and apply helpers."""

from typing import Any

import httpx
from assertpy import assert_that

from podex.models import Media, MediaType
from podex.services.enrichment.base import EnrichmentResult, EnrichmentSource
from podex.services.media_enrichment import PROVIDER_PRIORITY, MediaEnricher
from tests.enrichment.conftest import (
    _CountingLimiter,
    _enricher_with,
    _media,
    _StubProvider,
    _swap_client,
)

INTENTIONAL_PROVIDER_PRIORITY_EXCLUSIONS: frozenset[MediaType] = frozenset()


def test_enricher_uses_priority_provider() -> None:
    """The first confident provider result wins for a book."""
    hit = EnrichmentResult(
        source=EnrichmentSource.OPEN_LIBRARY,
        description="Desert planet saga.",
        confidence=0.9,
    )
    enricher = _enricher_with(
        {
            EnrichmentSource.GOOGLE_BOOKS: _StubProvider(None),
            EnrichmentSource.OPEN_LIBRARY: _StubProvider(hit),
        },
    )

    result = enricher.enrich(
        _media("Dune", MediaType.BOOK),
        use_wikipedia_fallback=False,
    )
    enricher.close()

    assert_that(result).is_equal_to(hit)


def test_provider_priority_exhaustively_covers_media_types() -> None:
    """Every MediaType is either routed or intentionally excluded."""
    missing = (
        set(MediaType)
        - set(PROVIDER_PRIORITY)
        - set(INTENTIONAL_PROVIDER_PRIORITY_EXCLUSIONS)
    )

    assert_that(missing).is_empty()


def test_enricher_falls_back_to_wikipedia() -> None:
    """When primaries miss, the Wikipedia fallback is consulted."""
    wiki_hit = EnrichmentResult(
        source=EnrichmentSource.WIKIPEDIA,
        description="From the free encyclopedia.",
        confidence=0.8,
    )
    enricher = _enricher_with(
        {
            EnrichmentSource.GOOGLE_BOOKS: _StubProvider(None),
            EnrichmentSource.OPEN_LIBRARY: _StubProvider(None),
            EnrichmentSource.WIKIPEDIA: _StubProvider(wiki_hit),
        },
    )

    result = enricher.enrich(_media("Dune", MediaType.BOOK))
    enricher.close()

    assert_that(result).is_equal_to(wiki_hit)


def test_enricher_rejects_low_confidence() -> None:
    """Sub-threshold results are discarded rather than returned."""
    weak = EnrichmentResult(
        source=EnrichmentSource.OPEN_LIBRARY,
        description="Maybe.",
        confidence=0.2,
    )
    enricher = _enricher_with(
        {EnrichmentSource.OPEN_LIBRARY: _StubProvider(weak)},
    )

    result = enricher.enrich(
        _media("Dune", MediaType.BOOK),
        min_confidence=0.7,
        use_wikipedia_fallback=False,
    )
    enricher.close()

    assert_that(result).is_none()


def test_enricher_academic_path_and_apply() -> None:
    """Study media route via the academic enricher; apply writes fields."""
    from podex.services.enrichment.base import VerifiedEnrichmentResult

    verified = VerifiedEnrichmentResult(
        source=EnrichmentSource.CROSSREF,
        description="A sleep study.",
        external_ids={"doi": "10.1000/example.doi", "pubmed_id": "12345"},
        metadata={"title": "Sleep study"},
        confidence=0.92,
        verified_by=[
            EnrichmentSource.CROSSREF,
            EnrichmentSource.SEMANTIC_SCHOLAR,
        ],
    )

    class _StubAcademic:
        def enrich_with_verification(
            self,
            media: Media,
        ) -> VerifiedEnrichmentResult:
            del media
            return verified

        def close(self) -> None:
            pass

    enricher = _enricher_with({})
    enricher.academic_enricher = _StubAcademic()  # type: ignore[assignment, unused-ignore]
    media = _media("Sleep study", MediaType.STUDY)

    result = enricher.enrich(media, use_wikipedia_fallback=False)
    assert_that(result).is_equal_to(verified)

    applied = enricher.enrich_and_apply(media)
    assert_that(applied).is_true()
    assert_that(media.enriched_at).is_not_none()
    assert_that(media.doi or "").contains("10.1000")
    assert_that(enricher.get_available_providers()).is_instance_of(list)
    with enricher:
        pass


def test_apply_enrichment_fills_media_fields() -> None:
    """apply_enrichment writes cover, description, ids, and provenance."""
    result = EnrichmentResult(
        source=EnrichmentSource.OPEN_LIBRARY,
        cover_url="https://img.invalid/cover.jpg",
        description="A rich description of the work.",
        external_ids={
            "open_library_id": "OL1W",
            "google_books_id": "gb1",
            "imdb_id": "tt1",
            "tmdb_id": 7,
            "wikipedia_id": "Dune_(novel)",
            "pubmed_id": "777",
            "doi": "10.1000/x",
            "semantic_scholar_id": "abc",
        },
        metadata={"subjects": ["Fiction"]},
        confidence=0.95,
    )
    enricher = _enricher_with({})
    media = _media("Dune", MediaType.BOOK)
    enricher.apply_enrichment(media, result)
    enricher.close()

    assert_that(media.cover_url).is_equal_to("https://img.invalid/cover.jpg")
    assert_that(media.description).is_equal_to(
        "A rich description of the work.",
    )
    assert_that(media.open_library_id).is_equal_to("OL1W")
    assert_that(media.google_books_id).is_equal_to("gb1")
    assert_that(media.imdb_id).is_equal_to("tt1")
    assert_that(media.tmdb_id).is_equal_to(7)
    assert_that(media.wikipedia_id).is_equal_to("Dune_(novel)")
    assert_that(media.pubmed_id).is_equal_to("777")
    assert_that(media.doi).is_equal_to("10.1000/x")
    assert_that(media.semantic_scholar_id).is_equal_to("abc")
    assert_that(media.enrichment_source).is_equal_to("open_library")
    assert_that(media.enrichment_confidence).is_equal_to(0.95)
    assert_that(media.enriched_at).is_not_none()


def test_enricher_initializes_all_configured_providers() -> None:
    """API keys switch on their providers in the registry."""
    enricher = MediaEnricher(
        tmdb_api_key="k1",
        omdb_api_key="k2",
        google_books_api_key="k3",
        ncbi_api_key="k4",
        crossref_mailto="ops@example.com",
    )
    names = enricher.get_available_providers()
    enricher.close()

    assert_that(len(names)).is_greater_than(5)


def test_pipeline_apply_and_alias_helpers() -> None:
    """The pipeline's apply/merge/alias helpers cover every id branch."""
    from datetime import UTC, datetime

    from podex.services.media_enrichment_pipeline import (
        _apply_enrichment_result,
        _extract_enrichment_aliases,
        _merge_metadata,
    )

    media = _media("Dune", MediaType.BOOK)
    result = EnrichmentResult(
        source=EnrichmentSource.OPEN_LIBRARY,
        cover_url="https://img.invalid/c.jpg",
        description="Applied description.",
        external_ids={
            "imdb_id": "tt1",
            "tmdb_id": "7",
            "google_books_id": "gb1",
            "open_library_id": "OL1W",
            "wikipedia_id": "Dune",
            "doi": "10.1000/x",
            "pubmed_id": "777",
            "semantic_scholar_id": "abc",
        },
        metadata={
            "title": "Dune",
            "original_title": "Dune Original",
            "also_known_as": ["Der Wüstenplanet", 42],
        },
        confidence=0.9,
    )
    _apply_enrichment_result(
        media=media,
        result=result,
        now=datetime.now(UTC),
    )

    assert_that(media.imdb_id).is_equal_to("tt1")
    assert_that(media.tmdb_id).is_equal_to(7)
    assert_that(media.google_books_id).is_equal_to("gb1")
    assert_that(media.open_library_id).is_equal_to("OL1W")
    assert_that(media.wikipedia_id).is_equal_to("Dune")
    assert_that(media.doi).is_equal_to("10.1000/x")
    assert_that(media.pubmed_id).is_equal_to("777")
    assert_that(media.semantic_scholar_id).is_equal_to("abc")

    merged = _merge_metadata(
        existing={"kept": "old"},
        incoming={"kept": "new", "added": "yes"},
    )
    assert_that(merged).is_equal_to({"kept": "old", "added": "yes"})
    assert_that(_merge_metadata(existing=None, incoming={})).is_none()

    aliases = _extract_enrichment_aliases(metadata=result.metadata)
    assert_that(aliases).contains("Dune Original")
    assert_that(aliases).contains("Der Wüstenplanet")


def test_provider_error_logs_redact_api_key(caplog: Any) -> None:
    """A 401 response log line never contains the api key value."""
    import logging as _logging

    from podex.services.enrichment import OMDBProvider

    provider = OMDBProvider("sekret-key")
    provider.rate_limiter = _CountingLimiter()
    _swap_client(
        provider,
        lambda request: httpx.Response(401, text="unauthorized"),
    )
    media = Media(type=MediaType.MOVIE, title="Dune")
    media.id = 24
    with caplog.at_level(_logging.WARNING):
        result = provider.search_and_enrich(media)
    provider.close()

    assert_that(result).is_none()
    assert_that(caplog.text).contains("HTTP 401")
    assert_that(caplog.text).does_not_contain("sekret-key")


def test_provider_context_managers_close_clients() -> None:
    """Context-manager protocol closes every API-key provider."""
    from podex.services.enrichment import (
        CrossRefProvider,
        GoogleBooksProvider,
        OMDBProvider,
        PubMedProvider,
        SemanticScholarProvider,
        TMDBPersonProvider,
        TMDBProvider,
        iTunesProvider,
    )

    factories: tuple[Any, ...] = (
        lambda: CrossRefProvider(),
        lambda: GoogleBooksProvider(),
        lambda: OMDBProvider("key"),
        lambda: PubMedProvider(),
        lambda: SemanticScholarProvider(),
        lambda: TMDBProvider("key"),
        lambda: TMDBPersonProvider("key"),
        lambda: iTunesProvider(),
    )
    for factory in factories:
        provider = factory()
        client = provider.client
        with provider:
            assert_that(provider).is_not_none()
        assert_that(client.is_closed).is_true()


def test_apply_external_ids_maps_columns_and_metadata() -> None:
    """Column-backed keys hit columns; other keys land in metadata_json."""
    from podex.services.media_enrichment import apply_external_ids

    media = _media("Dune", MediaType.BOOK)
    apply_external_ids(
        media=media,
        external_ids={
            "imdb_id": "tt1",
            "tmdb_id": "7",
            "apple_podcasts_id": "ap1",
            "isbn_13": "9780000000000",
        },
    )

    assert_that(media.imdb_id).is_equal_to("tt1")
    assert_that(media.tmdb_id).is_equal_to(7)
    assert_that(media.metadata_json).is_equal_to(
        {
            "external_ids": {
                "apple_podcasts_id": "ap1",
                "isbn_13": "9780000000000",
            },
        },
    )


def test_apply_external_ids_preserves_existing_values() -> None:
    """Existing column and metadata values are never overwritten."""
    from podex.services.media_enrichment import apply_external_ids

    media = _media("Dune", MediaType.BOOK)
    media.imdb_id = "tt-existing"
    media.metadata_json = {
        "keep": "me",
        "external_ids": {"apple_podcasts_id": "ap-existing"},
    }
    apply_external_ids(
        media=media,
        external_ids={
            "imdb_id": "tt-new",
            "apple_podcasts_id": "ap-new",
            "isbn_10": "1111111111",
        },
    )

    assert_that(media.imdb_id).is_equal_to("tt-existing")
    assert_that(media.metadata_json).is_equal_to(
        {
            "keep": "me",
            "external_ids": {
                "apple_podcasts_id": "ap-existing",
                "isbn_10": "1111111111",
            },
        },
    )


def test_apply_enrichment_persists_extra_external_ids() -> None:
    """apply_enrichment keeps unmapped identifiers in metadata_json."""
    result = EnrichmentResult(
        source=EnrichmentSource.ITUNES,
        external_ids={"apple_podcasts_id": "ap1", "isbn_13": "978"},
        metadata={"genre": "Technology"},
        confidence=0.9,
    )
    enricher = _enricher_with({})
    media = _media("Podcast", MediaType.PODCAST)
    enricher.apply_enrichment(media, result)
    enricher.close()

    assert_that(media.metadata_json or {}).contains_key("external_ids")
    assert_that((media.metadata_json or {})["external_ids"]).is_equal_to(
        {"apple_podcasts_id": "ap1", "isbn_13": "978"},
    )
    assert_that((media.metadata_json or {})["genre"]).is_equal_to("Technology")


def test_apply_enrichment_records_single_source_verification() -> None:
    """Ordinary results record their source as verification provenance."""
    result = EnrichmentResult(
        source=EnrichmentSource.OPEN_LIBRARY,
        external_ids={"open_library_id": "OL1W"},
        confidence=0.9,
    )
    enricher = _enricher_with({})
    media = _media("Dune", MediaType.BOOK)
    enricher.apply_enrichment(media, result)
    enricher.close()

    assert_that(media.verification_sources).is_equal_to(["open_library"])


def test_apply_enrichment_preserves_existing_verification_sources() -> None:
    """An existing verification_sources value is never overwritten."""
    result = EnrichmentResult(
        source=EnrichmentSource.OPEN_LIBRARY,
        external_ids={"open_library_id": "OL1W"},
        confidence=0.9,
    )
    enricher = _enricher_with({})
    media = _media("Dune", MediaType.BOOK)
    media.verification_sources = ["manual"]
    enricher.apply_enrichment(media, result)
    enricher.close()

    assert_that(media.verification_sources).is_equal_to(["manual"])

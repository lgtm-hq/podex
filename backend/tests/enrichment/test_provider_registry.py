"""Tests for the settings-driven enrichment provider registry."""

from assertpy import assert_that

from podex.config import (
    EnrichmentSettings,
    OmdbEnrichmentSettings,
    OpenLibraryEnrichmentSettings,
    TmdbEnrichmentSettings,
    WikipediaEnrichmentSettings,
)
from podex.models import MediaType
from podex.services.academic_enrichment import AcademicEnricher
from podex.services.enrichment.base import EnrichmentResult, EnrichmentSource
from podex.services.media_enrichment import (
    _PROVIDER_SPECS,
    MediaEnricher,
)
from tests.enrichment.conftest import _media, _StubProvider

#: Sources intentionally absent from the registry (no provider class yet).
INTENTIONAL_REGISTRY_EXCLUSIONS: frozenset[EnrichmentSource] = frozenset(
    {EnrichmentSource.SPOTIFY},
)


def test_provider_spec_exhaustiveness() -> None:
    """Every EnrichmentSource has a registry spec or a documented exclusion."""
    registered = {spec.source for spec in _PROVIDER_SPECS}
    all_sources = set(EnrichmentSource)

    assert_that(registered & INTENTIONAL_REGISTRY_EXCLUSIONS).is_empty()
    assert_that(all_sources - registered - INTENTIONAL_REGISTRY_EXCLUSIONS).is_empty()
    assert_that(INTENTIONAL_REGISTRY_EXCLUSIONS).contains(EnrichmentSource.SPOTIFY)


def test_keyed_provider_skipped_without_api_key() -> None:
    """TMDB/OMDB are omitted when their api_key is empty."""
    settings = EnrichmentSettings(
        tmdb=TmdbEnrichmentSettings(api_key=""),
        omdb=OmdbEnrichmentSettings(api_key=""),
    )
    enricher = MediaEnricher(settings=settings)
    try:
        assert_that(enricher.providers).does_not_contain_key(EnrichmentSource.TMDB)
        assert_that(enricher.providers).does_not_contain_key(
            EnrichmentSource.TMDB_PERSON,
        )
        assert_that(enricher.providers).does_not_contain_key(EnrichmentSource.OMDB)
    finally:
        enricher.close()


def test_disabled_provider_is_skipped() -> None:
    """enabled=False skips a provider even when it would otherwise build."""
    settings = EnrichmentSettings(
        open_library=OpenLibraryEnrichmentSettings(enabled=False),
        wikipedia=WikipediaEnrichmentSettings(enabled=False),
    )
    enricher = MediaEnricher(settings=settings)
    try:
        assert_that(enricher.providers).does_not_contain_key(
            EnrichmentSource.OPEN_LIBRARY,
        )
        assert_that(enricher.providers).does_not_contain_key(
            EnrichmentSource.WIKIPEDIA,
        )
        assert_that(enricher.providers).contains_key(EnrichmentSource.GOOGLE_BOOKS)
    finally:
        enricher.close()


def test_base_url_override_honored() -> None:
    """Provider clients (or instance attrs) use the configured base_url."""
    settings = EnrichmentSettings(
        tmdb=TmdbEnrichmentSettings(
            api_key="k",
            base_url="https://tmdb.mock/3",
        ),
        omdb=OmdbEnrichmentSettings(
            api_key="k",
            base_url="https://omdb.mock",
        ),
        open_library=OpenLibraryEnrichmentSettings(
            base_url="https://ol.mock",
        ),
        wikipedia=WikipediaEnrichmentSettings(
            base_url="https://wiki.mock/w/api.php",
        ),
    )
    enricher = MediaEnricher(settings=settings)
    try:
        tmdb = enricher.providers[EnrichmentSource.TMDB]
        omdb = enricher.providers[EnrichmentSource.OMDB]
        open_library = enricher.providers[EnrichmentSource.OPEN_LIBRARY]
        wikipedia = enricher.providers[EnrichmentSource.WIKIPEDIA]

        assert_that(str(tmdb.client.base_url)).contains("tmdb.mock")
        assert_that(str(omdb.client.base_url)).contains("omdb.mock")
        assert_that(open_library.base_url).is_equal_to("https://ol.mock")
        assert_that(wikipedia.API_URL).is_equal_to("https://wiki.mock/w/api.php")
    finally:
        enricher.close()


def test_default_construction_matches_keyless_provider_set() -> None:
    """No env keys → today's keyless provider set only."""
    enricher = MediaEnricher(settings=EnrichmentSettings())
    try:
        assert_that(set(enricher.providers)).is_equal_to(
            {
                EnrichmentSource.GOOGLE_BOOKS,
                EnrichmentSource.OPEN_LIBRARY,
                EnrichmentSource.ITUNES,
                EnrichmentSource.WIKIPEDIA,
            },
        )
        assert_that(set(enricher.academic_enricher.providers)).is_equal_to(
            {
                EnrichmentSource.PUBMED,
                EnrichmentSource.SEMANTIC_SCHOLAR,
                EnrichmentSource.CROSSREF,
            },
        )
    finally:
        enricher.close()


def test_injected_providers_not_closed_by_media_enricher() -> None:
    """Injected providers keep their lifecycle; MediaEnricher.close is a no-op."""
    stub = _StubProvider(
        EnrichmentResult(
            source=EnrichmentSource.GOOGLE_BOOKS,
            description="stub",
            confidence=0.9,
        ),
    )
    academic_stub = _StubProvider(
        EnrichmentResult(
            source=EnrichmentSource.PUBMED,
            description="academic stub",
            confidence=0.9,
        ),
    )
    enricher = MediaEnricher(
        providers={
            EnrichmentSource.GOOGLE_BOOKS: stub,
            EnrichmentSource.PUBMED: academic_stub,
        },
    )
    enricher.close()

    assert_that(stub.closed).is_false()
    assert_that(academic_stub.closed).is_false()


def test_academic_enricher_degrades_with_missing_providers() -> None:
    """Empty or partial provider maps yield None rather than raising."""
    empty = AcademicEnricher(providers={})
    assert_that(
        empty.enrich_with_verification(_media("Missing", MediaType.STUDY)),
    ).is_none()
    empty.close()

    only_crossref = AcademicEnricher(
        providers={
            EnrichmentSource.CROSSREF: _StubProvider(None),
        },
    )
    assert_that(
        only_crossref.enrich_with_verification(_media("Sparse", MediaType.STUDY)),
    ).is_none()
    only_crossref.close()

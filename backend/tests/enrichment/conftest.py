"""Shared helpers for enrichment tests."""

import time
from typing import Any

import httpx

from podex.models import Media, MediaType
from podex.services.academic_enrichment import AcademicEnricher
from podex.services.enrichment.base import (
    EnrichmentProvider,
    EnrichmentResult,
    EnrichmentSource,
)
from podex.services.media_enrichment import MediaEnricher


def _media(title: str, media_type: MediaType) -> Media:
    media = Media(type=media_type, title=title, author="Jane Doe")
    media.id = 1
    return media


class _StubProvider:
    """Provider double returning a fixed result."""

    def __init__(self, result: EnrichmentResult | None):
        self.result = result
        self.closed = False

    def search_and_enrich(self, media: Media) -> EnrichmentResult | None:
        """Return the canned result."""
        del media
        return self.result

    def supports_media_type(self, media_type: str) -> bool:
        """Support everything."""
        del media_type
        return True

    def close(self) -> None:
        """Record closure."""
        self.closed = True


def _enricher_with(providers: dict[EnrichmentSource, Any]) -> MediaEnricher:
    return MediaEnricher(providers=providers)


def _swap_client(provider: Any, handler: Any) -> None:
    provider.client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://mock.invalid",
    )


# This adapter implements one protocol operation; extra methods have no purpose.
# pylint: disable-next=too-few-public-methods
class _CountingLimiter:
    """Rate-limiter double counting wait_sync calls without sleeping."""

    def __init__(self) -> None:
        self.waits = 0

    def wait_sync(self) -> None:
        """Count the wait instead of sleeping."""
        self.waits += 1


class _RaisingProvider:
    """Provider double that always raises."""

    def search_and_enrich(self, media: Media) -> EnrichmentResult | None:
        """Raise unconditionally."""
        del media
        raise RuntimeError("provider exploded")

    def close(self) -> None:
        """No-op close."""


class _SlowProvider:
    """Provider double that sleeps past the aggregate deadline."""

    def __init__(self, result: EnrichmentResult, delay: float) -> None:
        self.result = result
        self.delay = delay

    def search_and_enrich(self, media: Media) -> EnrichmentResult | None:
        """Sleep, then return the canned result."""

        del media
        time.sleep(self.delay)
        return self.result

    def close(self) -> None:
        """No-op close."""


def _academic_with(providers: dict[EnrichmentSource, Any], **kwargs: Any) -> Any:
    """Build an AcademicEnricher whose providers are the given doubles."""
    return AcademicEnricher(providers=providers, **kwargs)


def require_result(result: EnrichmentResult | None) -> EnrichmentResult:
    """Fail immediately when enrichment unexpectedly returns no result."""
    if result is None:
        raise AssertionError("Expected an enrichment result")
    return result


def tmdb_person_search_result() -> dict[str, Any]:
    """Return a fresh representative upstream payload for enrichment tests."""
    return {
        "id": 505710,
        "name": "Frank Herbert",
        "known_for_department": "Writing",
        "popularity": 5.0,
        "profile_path": "/fh.jpg",
    }


def dune_movie_details() -> dict[str, Any]:
    """Return a fresh representative upstream payload for enrichment tests."""
    return {
        "id": 438631,
        "title": "Dune",
        "overview": "Desert planet epic.",
        "release_date": "2021-10-22",
        "poster_path": "/dune.jpg",
    }


def crossref_sleep_work() -> dict[str, Any]:
    """Return a fresh representative upstream payload for enrichment tests."""
    return {
        "DOI": "10.1000/example.doi",
        "title": ["Sleep and memory consolidation"],
        "author": [{"given": "Jane", "family": "Doe"}],
        "issued": {"date-parts": [[2020, 1, 1]]},
        "container-title": ["Journal of Sleep"],
        "URL": "https://doi.invalid/example",
    }


def semantic_sleep_metadata() -> dict[str, Any]:
    """Return a fresh representative upstream payload for enrichment tests."""
    return {
        "year": 2020,
        "citationCount": 100,
        "authors": [{"name": "Jane Doe"}],
        "externalIds": {"DOI": "10.1000/example.doi"},
        "url": "https://ss.invalid/abc123",
        "venue": "Journal of Sleep",
    }


def enrich_result(provider: EnrichmentProvider, media: Media) -> EnrichmentResult:
    """Require a match and always close the provider after exercising it."""
    with provider:
        return require_result(provider.search_and_enrich(media))


def swap_search_details_client(
    provider: Any, *, search_payload: dict[str, Any], detail_payload: dict[str, Any]
) -> None:
    """Route a provider search and its follow-up detail lookup to canned responses."""

    def handler(request: httpx.Request) -> httpx.Response:
        """Dispatch the mock provider's search or detail response."""
        payload = search_payload if "/search/" in request.url.path else detail_payload
        return httpx.Response(200, json=payload)

    _swap_client(provider, handler)

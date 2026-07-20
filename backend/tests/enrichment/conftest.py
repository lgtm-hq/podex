"""Shared helpers for enrichment tests."""

from typing import Any

import httpx

from podex.models import Media, MediaType
from podex.services.academic_enrichment import AcademicEnricher
from podex.services.enrichment.base import EnrichmentResult, EnrichmentSource
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
    enricher = MediaEnricher()
    for provider in enricher.providers.values():
        provider.close()
    enricher.academic_enricher.close()
    enricher.providers = providers
    return enricher


def _swap_client(provider: Any, handler: Any) -> None:
    provider.client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://mock.invalid",
    )


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
        import time

        del media
        time.sleep(self.delay)
        return self.result

    def close(self) -> None:
        """No-op close."""


def _academic_with(providers: dict[EnrichmentSource, Any], **kwargs: Any) -> Any:
    """Build an AcademicEnricher whose providers are replaced by doubles."""
    enricher = AcademicEnricher(**kwargs)
    for provider in enricher.providers.values():
        provider.close()
    enricher.providers = providers
    return enricher

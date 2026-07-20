"""Base enrichment provider protocol and data structures."""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum, auto
from typing import TYPE_CHECKING, Any, ClassVar

import httpx

if TYPE_CHECKING:
    from podex.models.media import Media

#: Prefixes stripped from DOI values, matched case-insensitively.
_DOI_PREFIXES = (
    "https://doi.org/",
    "http://doi.org/",
    "doi.org/",
    "doi:",
)


def canonicalize_doi(value: str) -> str:
    """Canonicalize a DOI by stripping URL/scheme prefixes and whitespace.

    Strips ``https://doi.org/``, ``http://doi.org/``, ``doi.org/``, and
    ``doi:`` prefixes (case-insensitively, repeatedly) so the bare DOI can
    be used in provider-specific query syntax. The DOI body's case is
    preserved.

    Args:
        value: Raw DOI value, possibly prefixed (e.g. from user input).

    Returns:
        The bare DOI (e.g. ``10.1000/x``).
    """
    doi = value.strip()
    stripped = True
    while stripped:
        stripped = False
        lowered = doi.lower()
        for prefix in _DOI_PREFIXES:
            if lowered.startswith(prefix):
                doi = doi[len(prefix) :].strip()
                stripped = True
                break
    return doi


def describe_http_error(e: httpx.HTTPError) -> str:
    """Describe an HTTP error without leaking URLs or credentials.

    ``str(e)`` on httpx errors can include the full request URL, which may
    carry API keys in query parameters. This helper returns only the status
    code or the exception class name.

    Args:
        e: The httpx error to describe.

    Returns:
        ``"HTTP <status>"`` for status errors, else the exception class name.
    """
    if isinstance(e, httpx.HTTPStatusError):
        return f"HTTP {e.response.status_code}"
    return type(e).__name__


class EnrichmentSource(StrEnum):
    """Sources for media enrichment."""

    GOOGLE_BOOKS = auto()
    OPEN_LIBRARY = auto()
    TMDB = auto()
    TMDB_PERSON = auto()
    OMDB = auto()
    PUBMED = auto()
    SEMANTIC_SCHOLAR = auto()
    CROSSREF = auto()
    SPOTIFY = auto()
    ITUNES = auto()
    WIKIPEDIA = auto()


@dataclass
class EnrichmentResult:
    """Result of enriching a media item."""

    source: EnrichmentSource
    cover_url: str | None = None
    description: str | None = None
    external_ids: dict[str, str | int] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0

    def has_useful_data(self) -> bool:
        """Check if the result contains any useful enrichment data."""
        return bool(
            self.cover_url or self.description or self.external_ids or self.metadata
        )


@dataclass
class VerifiedEnrichmentResult(EnrichmentResult):
    """Enrichment result with multi-source verification.

    This extends EnrichmentResult to track which sources confirmed the match
    and whether DOI-based verification was used.
    """

    # Which sources confirmed this match
    verified_by: list[EnrichmentSource] = field(default_factory=list)

    # Individual results from each source (for debugging/audit)
    source_results: dict[EnrichmentSource, EnrichmentResult] = field(
        default_factory=dict,
    )

    # Was DOI used for verification?
    doi_verified: bool = False


class RateLimiter:
    """Simple rate limiter for API calls.

    Args:
        requests_per_second: Maximum requests per second allowed.
    """

    def __init__(self, requests_per_second: float = 2.0) -> None:
        self.min_interval = 1.0 / requests_per_second
        self.last_request = 0.0

    async def wait(self) -> None:
        """Wait if necessary to respect rate limit."""
        elapsed = time.time() - self.last_request
        if elapsed < self.min_interval:
            await asyncio.sleep(self.min_interval - elapsed)
        self.last_request = time.time()

    def wait_sync(self) -> None:
        """Synchronous wait for rate limiting."""
        elapsed = time.time() - self.last_request
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self.last_request = time.time()


class EnrichmentProvider(ABC):
    """Abstract base class for enrichment providers.

    Args:
        requests_per_second: Maximum requests per second.
    """

    source: ClassVar[EnrichmentSource]
    rate_limiter: RateLimiter

    def __init__(self, requests_per_second: float = 2.0) -> None:
        self.rate_limiter = RateLimiter(requests_per_second)

    @abstractmethod
    def search_and_enrich(self, media: Media) -> EnrichmentResult | None:
        """Search for and enrich a media item.

        Args:
            media: The media item to enrich.

        Returns:
            EnrichmentResult if found, None otherwise.
        """
        ...

    @abstractmethod
    def supports_media_type(self, media_type: str) -> bool:
        """Check if this provider supports the given media type.

        Args:
            media_type: The type of media (book, movie, etc.).

        Returns:
            True if this provider can enrich this media type.
        """
        ...

    @abstractmethod
    def close(self) -> None:
        """Close the provider's HTTP client and release resources."""
        ...

    def _calculate_title_similarity(self, title1: str, title2: str) -> float:
        """Calculate similarity between two titles.

        Simple case-insensitive comparison with normalization.
        Returns a score between 0.0 and 1.0.
        """
        t1 = self._normalize_title(title1)
        t2 = self._normalize_title(title2)

        if t1 == t2:
            return 1.0

        # Check if one contains the other
        if t1 in t2 or t2 in t1:
            shorter = min(len(t1), len(t2))
            longer = max(len(t1), len(t2))
            return shorter / longer

        # Calculate word overlap
        words1 = set(t1.split())
        words2 = set(t2.split())
        if not words1 or not words2:
            return 0.0

        intersection = words1 & words2
        union = words1 | words2
        return len(intersection) / len(union)

    def _normalize_title(self, title: str) -> str:
        """Normalize a title for comparison."""
        import re

        # Lowercase
        title = title.lower()
        # Remove common articles
        title = re.sub(r"^(the|a|an)\s+", "", title)
        # Remove punctuation
        title = re.sub(r"[^\w\s]", "", title)
        # Normalize whitespace
        title = " ".join(title.split())
        return title

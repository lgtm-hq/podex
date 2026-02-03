"""Media enrichment providers."""

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

__all__ = [
    "CrossRefProvider",
    "EnrichmentProvider",
    "EnrichmentResult",
    "EnrichmentSource",
    "GoogleBooksProvider",
    "iTunesProvider",
    "OMDBProvider",
    "OpenLibraryProvider",
    "PubMedProvider",
    "SemanticScholarProvider",
    "TMDBPersonProvider",
    "TMDBProvider",
    "VerifiedEnrichmentResult",
    "WikipediaProvider",
]

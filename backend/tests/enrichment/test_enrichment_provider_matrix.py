"""Matrix tests for the API-key enrichment providers."""

from typing import Any

import httpx
import pytest
from assertpy import assert_that

from podex.models import Media, MediaType
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
from tests.enrichment.conftest import _swap_client


def _media(title: str, media_type: MediaType) -> Media:
    media = Media(type=media_type, title=title, author="Frank Herbert")
    media.id = 1
    return media


_EMPTY_RESPONSES: dict[str, dict[str, Any]] = {
    "google_books": {"items": []},
    "itunes": {"results": []},
    "omdb": {"Response": "False", "Error": "Movie not found!"},
    "crossref": {"message": {"items": []}},
    "semantic_scholar": {"data": []},
    "tmdb": {"results": []},
    "tmdb_person": {"results": []},
}

_PROVIDERS: list[tuple[str, Any, MediaType]] = [
    ("google_books", lambda: GoogleBooksProvider(), MediaType.BOOK),
    ("itunes", lambda: iTunesProvider(), MediaType.PODCAST),
    ("omdb", lambda: OMDBProvider("key"), MediaType.MOVIE),
    ("crossref", lambda: CrossRefProvider(), MediaType.ARTICLE),
    (
        "semantic_scholar",
        lambda: SemanticScholarProvider(),
        MediaType.STUDY,
    ),
    ("tmdb", lambda: TMDBProvider("key"), MediaType.MOVIE),
    ("tmdb_person", lambda: TMDBPersonProvider("key"), MediaType.PERSON),
]


@pytest.mark.parametrize(("name", "factory", "media_type"), _PROVIDERS)
def test_provider_handles_empty_results(
    name: str,
    factory: Any,
    media_type: MediaType,
) -> None:
    """Empty upstream responses yield None without raising."""
    provider = factory()
    payload = _EMPTY_RESPONSES[name]
    _swap_client(provider, lambda request: httpx.Response(200, json=payload))

    result = provider.search_and_enrich(_media("Nothing Findable", media_type))
    provider.close()

    assert_that(result).is_none()


@pytest.mark.parametrize(("name", "factory", "media_type"), _PROVIDERS)
def test_provider_handles_http_errors(
    name: str,
    factory: Any,
    media_type: MediaType,
) -> None:
    """Server errors yield None without raising."""
    provider = factory()
    _swap_client(provider, lambda request: httpx.Response(500, text="boom"))

    result = provider.search_and_enrich(_media("Dune", media_type))
    provider.close()

    assert_that(result).is_none()


@pytest.mark.parametrize(("name", "factory", "media_type"), _PROVIDERS)
def test_provider_rejects_unsupported_types(
    name: str,
    factory: Any,
    media_type: MediaType,
) -> None:
    """Providers decline media types they do not support."""
    del media_type
    provider = factory()
    _swap_client(
        provider,
        lambda request: httpx.Response(200, json={}),
    )
    unsupported = _media("Dune", MediaType.PLACE)

    result = provider.search_and_enrich(unsupported)
    provider.close()

    assert_that(result).is_none()


def test_pubmed_empty_and_error_paths() -> None:
    """PubMed's XML flow degrades to None on empty and error responses."""
    empty = PubMedProvider()
    _swap_client(
        empty,
        lambda request: httpx.Response(
            200,
            json={"esearchresult": {"idlist": []}},
        ),
    )
    erroring = PubMedProvider()
    _swap_client(erroring, lambda request: httpx.Response(503, text="down"))

    assert_that(
        empty.search_and_enrich(_media("Sleep study", MediaType.STUDY)),
    ).is_none()
    assert_that(
        erroring.search_and_enrich(_media("Sleep study", MediaType.STUDY)),
    ).is_none()
    empty.close()
    erroring.close()


def test_google_books_happy_path() -> None:
    """A matching volume produces a useful enrichment result."""
    payload = {
        "items": [
            {
                "id": "gb1",
                "volumeInfo": {
                    "title": "Dune",
                    "authors": ["Frank Herbert"],
                    "publishedDate": "1965-08-01",
                    "description": "A desert planet saga.",
                    "pageCount": 412,
                    "categories": ["Fiction"],
                    "averageRating": 4.5,
                    "language": "en",
                    "imageLinks": {"thumbnail": "https://img.invalid/d.jpg"},
                    "industryIdentifiers": [
                        {"type": "ISBN_13", "identifier": "9780441013593"},
                    ],
                    "previewLink": "https://books.invalid/dune",
                },
            },
        ],
    }
    provider = GoogleBooksProvider()
    _swap_client(provider, lambda request: httpx.Response(200, json=payload))

    result = provider.search_and_enrich(_media("Dune", MediaType.BOOK))
    provider.close()

    assert_that(result).is_not_none()
    if result is not None:
        assert_that(result.has_useful_data()).is_true()


def test_itunes_happy_path() -> None:
    """A matching podcast produces a useful enrichment result."""
    payload = {
        "results": [
            {
                "collectionId": 12,
                "collectionName": "Example Podcast",
                "artistName": "Example Author",
                "feedUrl": "https://feeds.invalid/pod.xml",
                "collectionViewUrl": "https://podcasts.invalid/12",
                "artworkUrl600": "https://img.invalid/a.jpg",
                "primaryGenreName": "Science",
                "genres": ["Science"],
                "trackCount": 100,
                "releaseDate": "2020-01-01T00:00:00Z",
                "country": "USA",
            },
        ],
    }
    provider = iTunesProvider()
    _swap_client(provider, lambda request: httpx.Response(200, json=payload))

    result = provider.search_and_enrich(
        _media("Example Podcast", MediaType.PODCAST),
    )
    provider.close()

    assert_that(result).is_not_none()
    if result is not None:
        assert_that(result.has_useful_data()).is_true()


def test_omdb_happy_path() -> None:
    """A matching movie produces IMDB ids and metadata."""
    payload = {
        "Response": "True",
        "Title": "Dune",
        "Year": "2021",
        "imdbID": "tt1160419",
        "Plot": "A noble family's heir travels to a desert planet.",
        "Poster": "https://img.invalid/dune.jpg",
        "Director": "Denis Villeneuve",
        "Actors": "Timothee Chalamet",
        "Genre": "Sci-Fi",
        "Rated": "PG-13",
        "Released": "22 Oct 2021",
        "Runtime": "155 min",
        "imdbRating": "8.0",
        "imdbVotes": "700,000",
        "Ratings": [{"Source": "Internet Movie Database", "Value": "8.0/10"}],
        "Awards": "Won 6 Oscars",
        "BoxOffice": "$108,327,830",
    }
    provider = OMDBProvider("key")
    _swap_client(provider, lambda request: httpx.Response(200, json=payload))

    result = provider.search_and_enrich(_media("Dune", MediaType.MOVIE))
    provider.close()

    assert_that(result).is_not_none()
    if result is not None:
        assert_that(str(result.external_ids)).contains("tt1160419")


def test_tmdb_happy_path() -> None:
    """A matching movie search plus details produce external ids."""
    search_payload = {
        "results": [
            {
                "id": 438631,
                "title": "Dune",
                "release_date": "2021-10-22",
                "overview": "Desert planet epic.",
                "poster_path": "/dune.jpg",
                "vote_average": 7.8,
                "vote_count": 10000,
                "popularity": 100.0,
            },
        ],
    }
    detail_payload = {
        "id": 438631,
        "title": "Dune",
        "overview": "Desert planet epic.",
        "release_date": "2021-10-22",
        "poster_path": "/dune.jpg",
        "imdb_id": "tt1160419",
        "external_ids": {"imdb_id": "tt1160419"},
        "genres": [{"name": "Science Fiction"}],
        "runtime": 155,
        "status": "Released",
        "tagline": "Beyond fear, destiny awaits.",
        "vote_average": 7.8,
        "vote_count": 10000,
        "budget": 165000000,
        "revenue": 402000000,
        "spoken_languages": [{"english_name": "English"}],
        "credits": {
            "cast": [{"name": "Actor Example", "character": "Paul"}],
            "crew": [{"name": "Denis Villeneuve", "job": "Director"}],
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if "/search/" in request.url.path:
            return httpx.Response(200, json=search_payload)
        return httpx.Response(200, json=detail_payload)

    provider = TMDBProvider("key")
    _swap_client(provider, handler)
    result = provider.search_and_enrich(_media("Dune", MediaType.MOVIE))
    provider.close()

    assert_that(result).is_not_none()
    if result is not None:
        assert_that(str(result.external_ids)).contains("438631")


def test_tmdb_person_happy_path() -> None:
    """A matching person search plus details produce a useful result."""
    search_payload = {
        "results": [
            {
                "id": 505710,
                "name": "Frank Herbert",
                "known_for_department": "Writing",
                "popularity": 5.0,
                "profile_path": "/fh.jpg",
            },
        ],
    }
    detail_payload = {
        "id": 505710,
        "name": "Frank Herbert",
        "biography": "American science fiction author.",
        "birthday": "1920-10-08",
        "deathday": "1986-02-11",
        "place_of_birth": "Tacoma, Washington",
        "profile_path": "/fh.jpg",
        "imdb_id": "nm0378394",
        "known_for_department": "Writing",
        "popularity": 5.0,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if "/search/person" in request.url.path:
            return httpx.Response(200, json=search_payload)
        return httpx.Response(200, json=detail_payload)

    provider = TMDBPersonProvider("key")
    _swap_client(provider, handler)
    result = provider.search_and_enrich(
        _media("Frank Herbert", MediaType.PERSON),
    )
    provider.close()

    assert_that(result).is_not_none()
    if result is not None:
        assert_that(result.has_useful_data()).is_true()


def test_crossref_happy_path() -> None:
    """A matching work produces DOI-backed enrichment."""
    payload = {
        "message": {
            "items": [
                {
                    "DOI": "10.1000/example.doi",
                    "title": ["Sleep and memory consolidation"],
                    "author": [{"given": "Jane", "family": "Doe"}],
                    "issued": {"date-parts": [[2020, 1, 1]]},
                    "container-title": ["Journal of Sleep"],
                    "URL": "https://doi.invalid/example",
                    "abstract": "A study of sleep and memory.",
                    "is-referenced-by-count": 42,
                    "type": "journal-article",
                    "publisher": "Example Press",
                },
            ],
        },
    }
    provider = CrossRefProvider()
    _swap_client(provider, lambda request: httpx.Response(200, json=payload))
    result = provider.search_and_enrich(
        _media("Sleep and memory consolidation", MediaType.ARTICLE),
    )
    provider.close()

    assert_that(result).is_not_none()
    if result is not None:
        assert_that(str(result.external_ids)).contains("10.1000")
        assert_that(result.metadata["title"]).is_equal_to(
            "Sleep and memory consolidation",
        )


def test_semantic_scholar_happy_path() -> None:
    """A matching paper produces id-backed enrichment."""
    payload = {
        "data": [
            {
                "paperId": "abc123",
                "title": "Sleep and memory consolidation",
                "abstract": "A study of sleep and memory.",
                "year": 2020,
                "citationCount": 100,
                "authors": [{"name": "Jane Doe"}],
                "externalIds": {"DOI": "10.1000/example.doi"},
                "url": "https://ss.invalid/abc123",
                "venue": "Journal of Sleep",
            },
        ],
    }
    provider = SemanticScholarProvider()
    _swap_client(provider, lambda request: httpx.Response(200, json=payload))
    result = provider.search_and_enrich(
        _media("Sleep and memory consolidation", MediaType.STUDY),
    )
    provider.close()

    assert_that(result).is_not_none()
    if result is not None:
        assert_that(result.has_useful_data()).is_true()
        assert_that(result.metadata["title"]).is_equal_to(
            "Sleep and memory consolidation",
        )

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
from tests.enrichment.conftest import (
    _swap_client,
    crossref_sleep_work,
    dune_movie_details,
    enrich_result,
    semantic_sleep_metadata,
    swap_search_details_client,
    tmdb_person_search_result,
)


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
    ("google_books", GoogleBooksProvider, MediaType.BOOK),
    ("itunes", iTunesProvider, MediaType.PODCAST),
    ("omdb", lambda: OMDBProvider(api_key="key"), MediaType.MOVIE),
    ("crossref", CrossRefProvider, MediaType.ARTICLE),
    (
        "semantic_scholar",
        SemanticScholarProvider,
        MediaType.STUDY,
    ),
    ("tmdb", lambda: TMDBProvider(api_key="key"), MediaType.MOVIE),
    ("tmdb_person", lambda: TMDBPersonProvider(api_key="key"), MediaType.PERSON),
]


@pytest.mark.parametrize(("_name", "factory", "media_type"), _PROVIDERS)
def test_provider_handles_empty_results(
    _name: str,
    factory: Any,
    media_type: MediaType,
) -> None:
    """Empty upstream responses yield None without raising."""
    provider = factory()
    payload = _EMPTY_RESPONSES[_name]
    _swap_client(provider, lambda request: httpx.Response(200, json=payload))

    result = provider.search_and_enrich(_media("Nothing Findable", media_type))
    provider.close()

    assert_that(result).is_none()


@pytest.mark.parametrize(("_name", "factory", "media_type"), _PROVIDERS)
def test_provider_handles_http_errors(
    _name: str,
    factory: Any,
    media_type: MediaType,
) -> None:
    """Server errors yield None without raising."""
    provider = factory()
    _swap_client(provider, lambda request: httpx.Response(500, text="boom"))

    result = provider.search_and_enrich(_media("Dune", media_type))
    provider.close()

    assert_that(result).is_none()


@pytest.mark.parametrize(("_name", "factory", "media_type"), _PROVIDERS)
def test_provider_rejects_unsupported_types(
    _name: str,
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

    result = enrich_result(provider, _media("Dune", MediaType.BOOK))
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

    result = enrich_result(provider, _media("Example Podcast", MediaType.PODCAST))
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
    provider = OMDBProvider(api_key="key")
    _swap_client(provider, lambda request: httpx.Response(200, json=payload))

    result = enrich_result(provider, _media("Dune", MediaType.MOVIE))
    assert_that(str(result.external_ids)).contains("tt1160419")


def test_tmdb_happy_path() -> None:
    """A matching movie search plus details produce external ids."""
    search_payload = {
        "results": [
            {
                **dune_movie_details(),
                "vote_average": 7.8,
                "vote_count": 10000,
                "popularity": 100.0,
            },
        ],
    }
    detail_payload = {
        **dune_movie_details(),
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

    provider = TMDBProvider(api_key="key")
    swap_search_details_client(
        provider, search_payload=search_payload, detail_payload=detail_payload
    )
    result = enrich_result(provider, _media("Dune", MediaType.MOVIE))
    assert_that(str(result.external_ids)).contains("438631")


def test_tmdb_person_happy_path() -> None:
    """A matching person search plus details produce a useful result."""
    search_payload = {
        "results": [
            tmdb_person_search_result(),
        ],
    }
    detail_payload = {
        **tmdb_person_search_result(),
        "biography": "American science fiction author.",
        "birthday": "1920-10-08",
        "deathday": "1986-02-11",
        "place_of_birth": "Tacoma, Washington",
        "imdb_id": "nm0378394",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if "/search/person" in request.url.path:
            return httpx.Response(200, json=search_payload)
        return httpx.Response(200, json=detail_payload)

    provider = TMDBPersonProvider(api_key="key")
    _swap_client(provider, handler)
    result = enrich_result(provider, _media("Frank Herbert", MediaType.PERSON))
    assert_that(result.has_useful_data()).is_true()


def test_crossref_happy_path() -> None:
    """A matching work produces DOI-backed enrichment."""
    payload = {
        "message": {
            "items": [
                {
                    **crossref_sleep_work(),
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
    result = enrich_result(
        provider, _media("Sleep and memory consolidation", MediaType.ARTICLE)
    )
    assert_that(str(result.external_ids)).contains("10.1000")
    assert_that(result.metadata["title"]).is_equal_to(
        "Sleep and memory consolidation",
    )


def test_semantic_scholar_happy_path() -> None:
    """A matching paper produces id-backed enrichment."""
    title = "Sleep and memory consolidation"
    payload = {
        "data": [
            {
                **semantic_sleep_metadata(),
                "paperId": "abc123",
                "title": title,
                "abstract": "A study of sleep and memory.",
            },
        ],
    }
    provider = SemanticScholarProvider()
    _swap_client(provider, lambda request: httpx.Response(200, json=payload))
    result = enrich_result(provider, _media(title, MediaType.STUDY))
    assert_that(result.has_useful_data()).is_true()
    assert_that(result.metadata["title"]).is_equal_to(
        title,
    )

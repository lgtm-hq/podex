"""Tests for the OMDB enrichment provider."""

import httpx
from assertpy import assert_that

from podex.models import Media, MediaType
from tests.enrichment.conftest import (
    _CountingLimiter,
    _swap_client,
)


def test_omdb_series_path() -> None:
    """TV shows query OMDB with the series type."""
    from podex.services.enrichment import OMDBProvider

    payload = {
        "Response": "True",
        "Title": "Example Show",
        "Year": "2019-2022",
        "imdbID": "tt9999999",
        "Plot": "A show about examples.",
        "Poster": "https://img.invalid/es.jpg",
        "Genre": "Drama",
        "imdbRating": "8.0",
        "imdbVotes": "5,000",
        "totalSeasons": "3",
    }
    provider = OMDBProvider(api_key="key")
    _swap_client(
        provider,
        lambda request: httpx.Response(200, json=payload),
    )
    media = Media(type=MediaType.TV_SHOW, title="Example Show")
    media.id = 3
    result = provider.search_and_enrich(media)
    provider.close()

    assert_that(result).is_not_none()
    if result is not None:
        assert_that(str(result.external_ids)).contains("tt9999999")


def test_omdb_year_and_documentary_paths() -> None:
    """Documentaries and year-bearing media flow through OMDB search."""
    from podex.services.enrichment import OMDBProvider

    payload = {
        "Response": "True",
        "Title": "Example Documentary",
        "Year": "2020",
        "imdbID": "tt7777777",
        "Plot": "A documentary about examples.",
        "Genre": "Documentary",
        "imdbRating": "7.5",
        "imdbVotes": "1,000",
    }
    provider = OMDBProvider(api_key="key")
    _swap_client(
        provider,
        lambda request: httpx.Response(200, json=payload),
    )
    media = Media(
        type=MediaType.DOCUMENTARY,
        title="Example Documentary",
        year=2020,
    )
    media.id = 4
    result = provider.search_and_enrich(media)
    provider.close()

    assert_that(result).is_not_none()
    if result is not None:
        assert_that(str(result.external_ids)).contains("tt7777777")


def test_omdb_falls_back_to_title_search_variants() -> None:
    """A miss on the exact title yields None (no variant retry exists)."""
    from podex.services.enrichment import OMDBProvider

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url.params.get("t", "")))
        if len(calls) < 2:
            return httpx.Response(
                200,
                json={"Response": "False", "Error": "Movie not found!"},
            )
        return httpx.Response(
            200,
            json={
                "Response": "True",
                "Title": "Dune",
                "Year": "2021",
                "imdbID": "tt1160419",
                "Plot": "Found on retry.",
                "imdbRating": "8.0",
                "imdbVotes": "1",
            },
        )

    provider = OMDBProvider(api_key="key")
    _swap_client(provider, handler)
    result = provider.search_and_enrich(
        Media(type=MediaType.MOVIE, title="Dune: Part One"),
    )
    provider.close()

    assert_that(calls).is_length(1)
    assert_that(calls[0]).is_equal_to("Dune: Part One")
    assert_that(result).is_none()


def test_omdb_direct_imdb_lookup_and_crossref_year_boost() -> None:
    """OMDB imdb-id fetch and CrossRef year matching both execute."""
    from podex.services.enrichment import CrossRefProvider, OMDBProvider

    omdb_payload = {
        "Response": "True",
        "Title": "Dune",
        "Year": "2021",
        "imdbID": "tt1160419",
        "Plot": "Direct fetch.",
        "imdbRating": "8.0",
        "imdbVotes": "1",
    }
    omdb = OMDBProvider(api_key="key")
    _swap_client(
        omdb,
        lambda request: httpx.Response(200, json=omdb_payload),
    )
    media = Media(type=MediaType.MOVIE, title="Dune")
    media.id = 11
    media.imdb_id = "tt1160419"
    direct = omdb.search_and_enrich(media)
    omdb.close()

    assert_that(direct).is_not_none()
    if direct is not None:
        assert_that(direct.confidence).is_equal_to(1.0)

    crossref_payload = {
        "message": {
            "items": [
                {
                    "DOI": "10.1000/yr",
                    "title": ["Sleep and memory consolidation"],
                    "author": [{"given": "Jane", "family": "Doe"}],
                    "published-print": {"date-parts": [[2020]]},
                    "container-title": ["Journal of Sleep"],
                    "type": "journal-article",
                },
            ],
        },
    }
    cr = CrossRefProvider()
    _swap_client(
        cr,
        lambda request: httpx.Response(200, json=crossref_payload),
    )
    study = Media(
        type=MediaType.STUDY,
        title="Sleep and memory consolidation",
        year=2020,
    )
    study.id = 12
    boosted = cr.search_and_enrich(study)
    cr.close()

    assert_that(boosted).is_not_none()
    if boosted is not None:
        assert_that(boosted.confidence).is_greater_than(0.5)


def test_omdb_waits_before_every_request() -> None:
    """A failed IMDB-ID lookup plus title search wait once per request."""
    from podex.services.enrichment import OMDBProvider

    requests_seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(str(request.url))
        if request.url.params.get("i"):
            return httpx.Response(
                200,
                json={"Response": "False", "Error": "Incorrect IMDb ID."},
            )
        return httpx.Response(
            200,
            json={
                "Response": "True",
                "Title": "Dune",
                "Year": "2021",
                "imdbID": "tt1160419",
                "Plot": "Found by title.",
                "imdbRating": "8.0",
                "imdbVotes": "1",
            },
        )

    provider = OMDBProvider(api_key="key")
    limiter = _CountingLimiter()
    provider.rate_limiter = limiter
    _swap_client(provider, handler)
    media = Media(type=MediaType.MOVIE, title="Dune")
    media.id = 21
    media.imdb_id = "tt0000000"
    result = provider.search_and_enrich(media)
    provider.close()

    assert_that(result).is_not_none()
    assert_that(len(requests_seen)).is_equal_to(2)
    assert_that(limiter.waits).is_equal_to(len(requests_seen))


def test_omdb_uses_https_base_url() -> None:
    """The OMDB base URL uses the https scheme (api key in query)."""
    from podex.services.enrichment import OMDBProvider

    assert_that(OMDBProvider.BASE_URL).starts_with("https://")

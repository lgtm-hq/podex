"""Tests for TMDB enrichment providers."""

import httpx
from assertpy import assert_that

from podex.models import Media, MediaType
from tests.enrichment.conftest import (
    _CountingLimiter,
    _media,
    _swap_client_matrix,
)

def test_tmdb_tv_show_path() -> None:
    """TV shows search the tv endpoint and parse air dates."""
    from podex.services.enrichment import TMDBProvider

    search_payload = {
        "results": [
            {
                "id": 42009,
                "name": "Example Show",
                "first_air_date": "2019-01-01",
                "overview": "A show about examples.",
                "poster_path": "/es.jpg",
                "vote_average": 8.0,
                "vote_count": 5000,
                "popularity": 60.0,
            },
        ],
    }
    detail_payload = {
        "id": 42009,
        "name": "Example Show",
        "overview": "A show about examples.",
        "first_air_date": "2019-01-01",
        "last_air_date": "2022-01-01",
        "number_of_seasons": 3,
        "number_of_episodes": 30,
        "episode_run_time": [45],
        "poster_path": "/es.jpg",
        "external_ids": {"imdb_id": "tt9999999"},
        "genres": [{"name": "Drama"}],
        "status": "Ended",
        "vote_average": 8.0,
        "vote_count": 5000,
        "credits": {"cast": [], "crew": []},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if "/search/" in request.url.path:
            return httpx.Response(200, json=search_payload)
        return httpx.Response(200, json=detail_payload)

    provider = TMDBProvider("key")
    _swap_client_matrix(provider, handler)
    media = Media(type=MediaType.TV_SHOW, title="Example Show")
    media.id = 2
    result = provider.search_and_enrich(media)
    provider.close()

    assert_that(result).is_not_none()
    if result is not None:
        assert_that(result.has_useful_data()).is_true()

def test_tmdb_person_detail_failure_yields_none() -> None:
    """Person detail failures yield None (no search-doc fallback exists)."""
    from podex.services.enrichment import TMDBPersonProvider

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

    def handler(request: httpx.Request) -> httpx.Response:
        if "/search/person" in request.url.path:
            return httpx.Response(200, json=search_payload)
        return httpx.Response(500, text="boom")

    provider = TMDBPersonProvider("key")
    _swap_client_matrix(provider, handler)
    result = provider.search_and_enrich(
        _media("Frank Herbert", MediaType.PERSON),
    )
    provider.close()

    assert_that(result).is_none()

def test_tmdb_year_filter_and_missing_details() -> None:
    """Year-bearing media filter results; detail failures yield None."""
    from podex.services.enrichment import TMDBProvider

    search_payload = {
        "results": [
            {
                "id": 1,
                "title": "Dune",
                "release_date": "1984-12-14",
                "overview": "Original adaptation.",
                "vote_average": 6.0,
                "vote_count": 1000,
                "popularity": 20.0,
            },
            {
                "id": 2,
                "title": "Dune",
                "release_date": "2021-10-22",
                "overview": "New adaptation.",
                "vote_average": 7.8,
                "vote_count": 10000,
                "popularity": 100.0,
            },
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if "/search/" in request.url.path:
            return httpx.Response(200, json=search_payload)
        return httpx.Response(500, text="down")

    provider = TMDBProvider("key")
    _swap_client_matrix(provider, handler)
    media = Media(type=MediaType.MOVIE, title="Dune", year=2021)
    media.id = 5
    result = provider.search_and_enrich(media)
    provider.close()

    assert_that(result).is_none()

def test_tmdb_person_by_known_id() -> None:
    """A stored tmdb id is ignored: the person provider always searches."""
    from podex.services.enrichment import TMDBPersonProvider

    detail = {
        "id": 505710,
        "name": "Frank Herbert",
        "biography": "Author.",
        "birthday": "1920-10-08",
        "profile_path": "/fh.jpg",
        "imdb_id": "nm0378394",
        "known_for_department": "Writing",
        "popularity": 5.0,
    }
    provider = TMDBPersonProvider("key")
    _swap_client_matrix(
        provider,
        lambda request: httpx.Response(200, json=detail),
    )
    media = _media("Frank Herbert", MediaType.PERSON)
    media.tmdb_id = 505710
    result = provider.search_and_enrich(media)
    provider.close()

    assert_that(result).is_none()

def test_tmdb_waits_before_every_request() -> None:
    """Search-with-year, search-without-year, and details each wait once."""
    from podex.services.enrichment import TMDBProvider

    requests_seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(str(request.url))
        if "/search/" in request.url.path:
            if request.url.params.get("year"):
                return httpx.Response(200, json={"results": []})
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": 2,
                            "title": "Dune",
                            "release_date": "2021-10-22",
                            "popularity": 100.0,
                        },
                    ],
                },
            )
        return httpx.Response(
            200,
            json={
                "id": 2,
                "title": "Dune",
                "overview": "Desert planet epic.",
                "release_date": "2021-10-22",
            },
        )

    provider = TMDBProvider("key")
    limiter = _CountingLimiter()
    provider.rate_limiter = limiter
    _swap_client_matrix(provider, handler)
    media = Media(type=MediaType.MOVIE, title="Dune", year=2021)
    media.id = 20
    result = provider.search_and_enrich(media)
    provider.close()

    assert_that(result).is_not_none()
    assert_that(len(requests_seen)).is_equal_to(3)
    assert_that(limiter.waits).is_equal_to(len(requests_seen))

def test_tmdb_direct_id_lookup_skips_search() -> None:
    """A stored tmdb_id fetches details directly without any search."""
    from podex.services.enrichment import TMDBProvider

    seen_paths: list[str] = []
    detail_payload = {
        "id": 438631,
        "title": "Dune",
        "overview": "Desert planet epic.",
        "release_date": "2021-10-22",
        "poster_path": "/dune.jpg",
        "external_ids": {"imdb_id": "tt1160419"},
        "credits": {"cast": [], "crew": []},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        if request.url.path == "/movie/438631":
            return httpx.Response(200, json=detail_payload)
        return httpx.Response(404, text="not found")

    provider = TMDBProvider("key")
    provider.rate_limiter = _CountingLimiter()
    _swap_client_matrix(provider, handler)
    media = Media(type=MediaType.MOVIE, title="Dune")
    media.id = 22
    media.tmdb_id = 438631
    result = provider.search_and_enrich(media)
    provider.close()

    assert_that(seen_paths).is_equal_to(["/movie/438631"])
    assert_that(result).is_not_none()
    if result is not None:
        assert_that(result.confidence).is_equal_to(1.0)
        assert_that(str(result.external_ids)).contains("438631")

def test_tmdb_direct_id_404_falls_back_to_search() -> None:
    """A 404 on the stored tmdb_id falls back to title search."""
    from podex.services.enrichment import TMDBProvider

    seen_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        if "/search/" in request.url.path:
            return httpx.Response(200, json={"results": []})
        return httpx.Response(404, text="not found")

    provider = TMDBProvider("key")
    provider.rate_limiter = _CountingLimiter()
    _swap_client_matrix(provider, handler)
    media = Media(type=MediaType.MOVIE, title="Dune")
    media.id = 23
    media.tmdb_id = 999999
    result = provider.search_and_enrich(media)
    provider.close()

    assert_that(result).is_none()
    assert_that(seen_paths[0]).is_equal_to("/movie/999999")
    assert_that(seen_paths[1]).is_equal_to("/tv/999999")
    assert_that(
        [p for p in seen_paths if p.startswith("/search/")],
    ).is_not_empty()

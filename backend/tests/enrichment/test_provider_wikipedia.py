"""Tests for the Wikipedia enrichment provider."""

import httpx
from assertpy import assert_that

from podex.models import Media, MediaType
from tests.enrichment.conftest import _media, _swap_client


def test_wikipedia_person_and_place_paths() -> None:
    """Person and place media types walk their variation flows."""
    from podex.services.enrichment import WikipediaProvider

    search_payload = {
        "query": {
            "search": [
                {
                    "pageid": 88,
                    "title": "Frank Herbert",
                    "snippet": "American science fiction author",
                },
            ],
        },
    }
    page_payload = {
        "query": {
            "pages": {
                "88": {
                    "pageid": 88,
                    "title": "Frank Herbert",
                    "extract": "Frank Herbert was an American science "
                    "fiction author best known for the novel Dune.",
                    "fullurl": "https://en.wikipedia.org/wiki/Frank_Herbert",
                    "thumbnail": {
                        "original": "https://upload.invalid/fh.jpg",
                    },
                    "categories": [
                        {"title": "Category:American science fiction writers"},
                        {"title": "Category:1920 births"},
                    ],
                },
            },
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        if params.get("list") == "search":
            return httpx.Response(200, json=search_payload)
        return httpx.Response(200, json=page_payload)

    person_provider = WikipediaProvider(requests_per_second=1000)
    _swap_client(person_provider, handler)
    person = person_provider.search_and_enrich(
        _media("Frank Herbert", MediaType.PERSON),
    )
    person_provider.close()

    place_provider = WikipediaProvider(requests_per_second=1000)
    _swap_client(place_provider, handler)
    place_media = Media(type=MediaType.PLACE, title="Frank Herbert")
    place_media.id = 9
    place = place_provider.search_and_enrich(place_media)
    place_provider.close()

    assert_that(person).is_not_none()
    if person is not None:
        assert_that(person.has_useful_data()).is_true()
    del place


def test_wikipedia_variations_across_types() -> None:
    """Every media type's search-variation builder executes."""
    import pytest as _pytest

    from podex.services.enrichment import WikipediaProvider

    del _pytest
    for media_type in (
        MediaType.MOVIE,
        MediaType.TV_SHOW,
        MediaType.DOCUMENTARY,
        MediaType.BOOK,
        MediaType.PODCAST,
    ):
        provider = WikipediaProvider(requests_per_second=1000)
        _swap_client(
            provider,
            lambda request: httpx.Response(
                200,
                json={"query": {"search": []}},
            ),
        )
        media = Media(type=media_type, title="Example Title")
        media.id = 10
        assert_that(provider.search_and_enrich(media)).is_none()
        provider.close()

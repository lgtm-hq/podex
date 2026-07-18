"""Tests for the enrichment provider base, utils, and free providers."""

from typing import Any

import httpx
from assertpy import assert_that

from podex.models import Media, MediaType
from podex.services.enrichment import OpenLibraryProvider, WikipediaProvider
from podex.services.enrichment.search_utils import (
    calculate_similarity,
    generate_search_variations,
    normalize_title,
    strip_articles,
)


def _media(title: str, media_type: MediaType = MediaType.BOOK) -> Media:
    media = Media(type=media_type, title=title, author="Frank Herbert")
    media.id = 1
    return media


def _mock_client(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_search_utils_normalize_and_variations() -> None:
    """Titles normalize and search variations include stripped forms."""
    assert_that(strip_articles("The Dune")).is_equal_to("Dune")
    assert_that(normalize_title("Dune: Part Two!")).contains("dune")
    variations = generate_search_variations(
        "The Dune: A Saga (Deluxe)",
        author="Frank Herbert",
    )
    assert_that(len(variations)).is_greater_than(1)
    assert_that(calculate_similarity("Dune", "Dune")).is_equal_to(1.0)


def test_open_library_enriches_book(monkeypatch: Any) -> None:
    """A matching search doc plus work detail produce a useful result."""
    search_payload = {
        "docs": [
            {
                "key": "/works/OL893415W",
                "title": "Dune",
                "author_name": ["Frank Herbert"],
                "first_publish_year": 1965,
                "cover_i": 12345,
            },
        ],
    }
    work_payload = {
        "key": "/works/OL893415W",
        "title": "Dune",
        "description": "A desert planet saga.",
        "covers": [12345],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search.json":
            return httpx.Response(200, json=search_payload)
        return httpx.Response(200, json=work_payload)

    provider = OpenLibraryProvider(requests_per_second=1000)
    provider.client = _mock_client(handler)
    result = provider.search_and_enrich(_media("Dune"))
    provider.close()

    assert_that(result).is_not_none()
    if result is not None:
        assert_that(result.has_useful_data()).is_true()
        assert_that(str(result.external_ids)).contains("OL893415W")


def test_open_library_handles_no_results_and_errors() -> None:
    """Empty results and HTTP errors both yield None, not exceptions."""
    empty = OpenLibraryProvider(requests_per_second=1000)
    empty.client = _mock_client(
        lambda request: httpx.Response(200, json={"docs": []}),
    )
    erroring = OpenLibraryProvider(requests_per_second=1000)
    erroring.client = _mock_client(
        lambda request: httpx.Response(500, text="boom"),
    )

    assert_that(empty.search_and_enrich(_media("Nothing Here"))).is_none()
    assert_that(erroring.search_and_enrich(_media("Dune"))).is_none()
    assert_that(
        empty.search_and_enrich(_media("Dune", MediaType.MOVIE)),
    ).is_none()
    empty.close()
    erroring.close()


def test_wikipedia_provider_returns_none_gracefully() -> None:
    """Wikipedia provider degrades to None on empty search results."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"query": {"search": []}},
        )

    provider = WikipediaProvider(requests_per_second=1000)
    provider.client = _mock_client(handler)
    result = provider.search_and_enrich(_media("Completely Unknown Thing"))
    provider.close()

    assert_that(result).is_none()


def test_wikipedia_provider_error_path() -> None:
    """HTTP failures yield None rather than raising."""
    provider = WikipediaProvider(requests_per_second=1000)
    provider.client = _mock_client(
        lambda request: httpx.Response(503, text="unavailable"),
    )

    assert_that(provider.search_and_enrich(_media("Dune"))).is_none()
    provider.close()


def test_wikipedia_enriches_book_via_search_and_page() -> None:
    """A search hit plus page detail produce a useful Wikipedia result."""
    search_payload = {
        "query": {
            "search": [
                {
                    "pageid": 42,
                    "title": "Dune (novel)",
                    "snippet": "science fiction novel by Frank Herbert",
                },
            ],
        },
    }
    page_payload = {
        "query": {
            "pages": {
                "42": {
                    "pageid": 42,
                    "title": "Dune (novel)",
                    "extract": "Dune is a 1965 science fiction novel "
                    "written by Frank Herbert about a desert planet.",
                    "fullurl": "https://en.wikipedia.org/wiki/Dune_(novel)",
                    "thumbnail": {
                        "original": "https://upload.wikimedia.org/dune.jpg",
                    },
                    "categories": [
                        {"title": "Category:1965 American novels"},
                        {"title": "Category:Novels by Frank Herbert"},
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

    provider = WikipediaProvider(requests_per_second=1000)
    provider.client = _mock_client(handler)
    result = provider.search_and_enrich(_media("Dune"))
    provider.close()

    assert_that(result).is_not_none()
    if result is not None:
        assert_that(result.has_useful_data()).is_true()
        assert_that(result.description or "").contains("Frank Herbert")


def test_search_utils_helper_coverage() -> None:
    """Subtitle/parenthetical/name/keyword helpers behave as documented."""
    from podex.services.enrichment.search_utils import (
        extract_key_words,
        get_primary_name,
        is_likely_match,
        remove_parentheticals,
        remove_subtitle,
        remove_with_clause,
    )

    assert_that(remove_subtitle("Dune: The Saga")).is_equal_to("Dune")
    assert_that(remove_parentheticals("Dune (Deluxe)")).is_equal_to("Dune")
    assert_that(remove_with_clause("An Evening with Frank Herbert")).does_not_contain(
        "with Frank",
    )
    assert_that(get_primary_name("Frank Herbert, Kevin Anderson") or "").contains(
        "Herbert",
    )
    assert_that(get_primary_name("Frank Herbert and Kevin Anderson") or "").contains(
        "Herbert",
    )
    key_words = extract_key_words(
        "The Complete and Utterly Definitive History of the Desert Planet",
    )
    assert_that(len(key_words)).is_greater_than(0)
    likely, score = is_likely_match("Dune", "Dune")
    assert_that(likely).is_true()
    assert_that(score).is_greater_than(0.9)
    unlikely, _ = is_likely_match("Dune", "Cooking with Gas")
    assert_that(unlikely).is_false()


def test_open_library_direct_id_fetch() -> None:
    """A known open_library_id fetches the work directly at confidence 1."""
    work_payload = {
        "key": "/works/OL893415W",
        "title": "Dune",
        "description": {"type": "/type/text", "value": "Desert planet saga."},
        "covers": [12345],
        "subjects": ["Science fiction"],
    }
    provider = OpenLibraryProvider(requests_per_second=1000)
    provider.client = _mock_client(
        lambda request: httpx.Response(200, json=work_payload),
    )
    media = _media("Dune")
    media.open_library_id = "OL893415W"
    result = provider.search_and_enrich(media)
    provider.close()

    assert_that(result).is_not_none()
    if result is not None:
        assert_that(result.confidence).is_equal_to(1.0)
        assert_that(result.description or "").contains("Desert planet")


def test_open_library_falls_back_to_search_doc() -> None:
    """When the work fetch fails, the search doc still builds a result."""
    search_payload = {
        "docs": [
            {
                "key": "/works/OL893415W",
                "title": "Dune",
                "author_name": ["Frank Herbert"],
                "first_publish_year": 1965,
                "first_sentence": ["A desert planet."],
                "cover_i": 12345,
                "subject": ["Science fiction"],
            },
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search.json":
            return httpx.Response(200, json=search_payload)
        return httpx.Response(404, text="missing")

    provider = OpenLibraryProvider(requests_per_second=1000)
    provider.client = _mock_client(handler)
    result = provider.search_and_enrich(_media("Dune"))
    provider.close()

    assert_that(result).is_not_none()
    if result is not None:
        assert_that(result.has_useful_data()).is_true()


def test_wikipedia_study_search_path() -> None:
    """Study-type media walk the study flow through search and page fetch."""
    search_payload = {
        "query": {
            "search": [
                {
                    "pageid": 77,
                    "title": "Sleep and memory consolidation study",
                    "snippet": "landmark research study on sleep",
                },
            ],
        },
    }
    page_payload = {
        "query": {
            "pages": {
                "77": {
                    "pageid": 77,
                    "title": "Sleep and memory consolidation study",
                    "extract": "A landmark research study examining how "
                    "sleep consolidates memory in controlled experiments.",
                    "fullurl": "https://en.wikipedia.org/wiki/Sleep_study",
                    "categories": [
                        {"title": "Category:Clinical research"},
                        {"title": "Category:Sleep studies"},
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

    provider = WikipediaProvider(requests_per_second=1000)
    provider.client = _mock_client(handler)
    result = provider.search_and_enrich(
        _media("Sleep and memory consolidation", MediaType.STUDY),
    )
    empty = WikipediaProvider(requests_per_second=1000)
    empty.client = _mock_client(
        lambda request: httpx.Response(200, json={"query": {"search": []}}),
    )
    none_result = empty.search_and_enrich(_media("Nothing", MediaType.STUDY))
    provider.close()
    empty.close()

    assert_that(none_result).is_none()
    if result is not None:
        assert_that(result.has_useful_data()).is_true()


def test_base_title_similarity() -> None:
    """The base similarity helper scores near-identical titles high."""
    provider = OpenLibraryProvider(requests_per_second=1000)
    same = provider._calculate_title_similarity("Dune", "Dune")
    partial = provider._calculate_title_similarity("Dune", "The Dune Saga")
    provider.close()

    assert_that(same).is_equal_to(1.0)
    assert_that(partial).is_less_than(1.0)


def test_rate_limiter_and_context_managers() -> None:
    """Rate limiting waits between calls; providers work as context managers."""
    import asyncio

    from podex.services.enrichment.base import RateLimiter

    limiter = RateLimiter(requests_per_second=10000)
    limiter.wait_sync()
    limiter.wait_sync()
    asyncio.run(limiter.wait())

    with OpenLibraryProvider(requests_per_second=1000) as ol:
        assert_that(
            ol._calculate_title_similarity("Dune Saga", "Saga of Dune"),
        ).is_greater_than(0.0)
        assert_that(
            ol._calculate_title_similarity("Dune", ""),
        ).is_equal_to(0.0)
    with WikipediaProvider(requests_per_second=1000) as wiki:
        assert_that(wiki.supports_media_type("book")).is_true()


def test_provider_mismatch_and_variation_paths() -> None:
    """Low-similarity docs are rejected; movie-type variations execute."""
    mismatch = OpenLibraryProvider(requests_per_second=1000)
    mismatch.client = _mock_client(
        lambda request: httpx.Response(
            200,
            json={
                "docs": [
                    {
                        "key": "/works/OL1W",
                        "title": "Cooking with Gas",
                        "author_name": ["Someone Else"],
                    },
                ],
            },
        ),
    )
    assert_that(mismatch.search_and_enrich(_media("Dune"))).is_none()
    mismatch.close()

    movie = WikipediaProvider(requests_per_second=1000)
    movie.client = _mock_client(
        lambda request: httpx.Response(200, json={"query": {"search": []}}),
    )
    assert_that(
        movie.search_and_enrich(_media("Dune", MediaType.MOVIE)),
    ).is_none()
    movie.close()

"""Tests for book enrichment providers."""

import httpx
from assertpy import assert_that

from podex.models import MediaType
from tests.enrichment.conftest import _media, _swap_client_matrix


def test_google_books_isbn10_and_sparse_volume() -> None:
    """ISBN-10-only volumes without images still enrich."""
    from podex.services.enrichment import GoogleBooksProvider

    payload = {
        "items": [
            {
                "id": "gb2",
                "volumeInfo": {
                    "title": "Dune",
                    "authors": ["Frank Herbert"],
                    "publishedDate": "1965",
                    "industryIdentifiers": [
                        {"type": "ISBN_10", "identifier": "0441013597"},
                    ],
                },
            },
        ],
    }
    provider = GoogleBooksProvider(api_key="key")
    _swap_client_matrix(
        provider,
        lambda request: httpx.Response(200, json=payload),
    )
    result = provider.search_and_enrich(_media("Dune", MediaType.BOOK))
    provider.close()

    assert_that(result).is_not_none()
    if result is not None:
        assert_that(result.has_useful_data()).is_true()


def test_google_books_second_item_selected_on_better_match() -> None:
    """Best-match selection scans beyond the first volume."""
    from podex.services.enrichment import GoogleBooksProvider

    payload = {
        "items": [
            {
                "id": "gbX",
                "volumeInfo": {
                    "title": "Completely Unrelated",
                    "authors": ["Nobody"],
                },
            },
            {
                "id": "gbY",
                "volumeInfo": {
                    "title": "Dune",
                    "authors": ["Frank Herbert"],
                    "publishedDate": "1965",
                    "description": "The desert planet novel.",
                    "language": "en",
                    "averageRating": 4.5,
                    "maturityRating": "NOT_MATURE",
                },
            },
        ],
    }
    provider = GoogleBooksProvider()
    _swap_client_matrix(
        provider,
        lambda request: httpx.Response(200, json=payload),
    )
    result = provider.search_and_enrich(_media("Dune", MediaType.BOOK))
    provider.close()

    assert_that(result).is_not_none()
    if result is not None:
        assert_that(str(result.external_ids)).contains("gbY")

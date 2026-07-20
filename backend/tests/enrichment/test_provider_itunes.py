"""Tests for the iTunes enrichment provider."""

import httpx
from assertpy import assert_that

from podex.models import MediaType
from tests.enrichment.conftest import _media, _swap_client_matrix


def test_itunes_rejects_dissimilar_podcasts() -> None:
    """Results with unrelated names are rejected as matches."""
    from podex.services.enrichment import iTunesProvider

    payload = {
        "results": [
            {
                "collectionId": 99,
                "collectionName": "A Completely Different Program",
                "artistName": "Someone",
            },
        ],
    }
    provider = iTunesProvider()
    _swap_client_matrix(
        provider,
        lambda request: httpx.Response(200, json=payload),
    )
    result = provider.search_and_enrich(
        _media("Example Podcast", MediaType.PODCAST),
    )
    provider.close()

    assert_that(result).is_none()

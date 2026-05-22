"""Tests for endpoint-level API rate limits."""

from collections.abc import Generator
from dataclasses import dataclass

import pytest
from assertpy import assert_that
from fastapi.testclient import TestClient

from podex.api.rate_limits import endpoint_rate_limiter
from podex.config import get_settings
from podex.services.search.meilisearch_client import get_search_client


@dataclass(frozen=True, slots=True)
class RateLimitSettingsSnapshot:
    """Mutable settings values restored after rate-limit tests."""

    rate_limit_enabled: bool
    public_search_rate_limit_per_minute: int
    ops_rate_limit_per_minute: int
    meilisearch_enabled: bool


@pytest.fixture
def rate_limit_settings() -> Generator[None, None, None]:
    """Temporarily reduce rate limits for deterministic tests."""
    settings = get_settings()
    snapshot = RateLimitSettingsSnapshot(
        rate_limit_enabled=settings.rate_limit_enabled,
        public_search_rate_limit_per_minute=(
            settings.public_search_rate_limit_per_minute
        ),
        ops_rate_limit_per_minute=settings.ops_rate_limit_per_minute,
        meilisearch_enabled=settings.meilisearch_enabled,
    )
    settings.rate_limit_enabled = True
    settings.public_search_rate_limit_per_minute = 2
    settings.ops_rate_limit_per_minute = 1
    settings.meilisearch_enabled = False
    get_search_client.cache_clear()
    endpoint_rate_limiter.clear()
    try:
        yield
    finally:
        settings.rate_limit_enabled = snapshot.rate_limit_enabled
        settings.public_search_rate_limit_per_minute = (
            snapshot.public_search_rate_limit_per_minute
        )
        settings.ops_rate_limit_per_minute = snapshot.ops_rate_limit_per_minute
        settings.meilisearch_enabled = snapshot.meilisearch_enabled
        get_search_client.cache_clear()
        endpoint_rate_limiter.clear()


def test_public_search_is_rate_limited(
    *,
    client: TestClient,
    rate_limit_settings: None,
) -> None:
    """Verify public search returns 429 after its configured bucket is exhausted."""
    first = client.get("/api/v2/search?q=test")
    second = client.get("/api/v2/search?q=test")
    third = client.get("/api/v2/search?q=test")

    assert_that(first.status_code).is_equal_to(200)
    assert_that(second.status_code).is_equal_to(200)
    assert_that(third.status_code).is_equal_to(429)
    assert_that(third.json()).contains_entry({"bucket": "public-search"})
    assert_that(third.headers["X-RateLimit-Bucket"]).is_equal_to("public-search")
    assert_that(third.headers["Retry-After"]).is_not_empty()


def test_ops_routes_use_separate_rate_limit_bucket(
    *,
    client: TestClient,
    rate_limit_settings: None,
) -> None:
    """Verify ops routes use the ops bucket instead of the public search bucket."""
    first = client.get("/api/v2/ops/dashboard")
    second = client.get("/api/v2/ops/dashboard")

    assert_that(first.status_code).is_equal_to(200)
    assert_that(first.headers["X-RateLimit-Bucket"]).is_equal_to("ops")
    assert_that(second.status_code).is_equal_to(429)
    assert_that(second.json()).contains_entry({"bucket": "ops"})


def test_unmatched_routes_are_not_rate_limited(
    *,
    client: TestClient,
    rate_limit_settings: None,
) -> None:
    """Verify ordinary public read routes do not consume endpoint-limit buckets."""
    responses = [client.get("/api/v2/podcasts") for _ in range(3)]

    assert_that([response.status_code for response in responses]).is_equal_to(
        [200, 200, 200],
    )
    assert_that(responses[-1].headers).does_not_contain_key("X-RateLimit-Bucket")

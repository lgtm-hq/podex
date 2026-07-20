"""Tests for the ``/api/v2/stats`` endpoint and its cache wiring."""

from collections.abc import Iterator

import pytest
from assertpy import assert_that
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from podex.api.deps import get_app_cache
from podex.config import Settings, StatsCacheSettings
from podex.database import get_db
from podex.main import create_app
from podex.models import Episode, Media, MediaType, Mention, Podcast
from podex.services.cache import TTLCache
from podex.services.stats_queries import (
    CATALOG_STATS_CACHE_KEY,
    compute_catalog_stats,
    get_catalog_stats,
)


class _Clock:
    """Advancable clock used to exercise TTL expiry without sleeping."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


def _seed_catalog(db: Session) -> None:
    """Populate a small but realistic mixed catalog for stats tests."""
    podcast = Podcast(name="Show", slug="show")
    db.add(podcast)
    db.commit()
    episode = Episode(podcast_id=podcast.id, title="Pilot")
    dune = Media(type=MediaType.BOOK, title="Dune")
    foundation = Media(type=MediaType.BOOK, title="Foundation")
    arrival = Media(type=MediaType.MOVIE, title="Arrival")
    db.add_all([episode, dune, foundation, arrival])
    db.commit()
    db.add(Mention(episode_id=episode.id, media_id=dune.id, timestamp_seconds=10))
    db.add(Mention(episode_id=episode.id, media_id=arrival.id, timestamp_seconds=20))
    db.commit()


def test_compute_catalog_stats_reports_counts_and_top_types(
    db_session: Session,
) -> None:
    """The uncached helper returns real counts and a stable top-types list."""
    _seed_catalog(db_session)

    stats = compute_catalog_stats(db_session)

    assert_that(stats.podcasts).is_equal_to(1)
    assert_that(stats.episodes).is_equal_to(1)
    assert_that(stats.media).is_equal_to(3)
    assert_that(stats.mentions).is_equal_to(2)
    top = [(row.media_type, row.count) for row in stats.top_media_types]
    assert_that(top).is_equal_to([("book", 2), ("movie", 1)])


def test_get_catalog_stats_uses_cache_on_second_call(db_session: Session) -> None:
    """A cached value short-circuits the DB query on subsequent calls."""
    _seed_catalog(db_session)
    cache = TTLCache()

    first = get_catalog_stats(db_session, cache=cache, ttl_seconds=60)
    # Mutate the DB after the first call. If the cache is honored the counts
    # returned from the second call should be unchanged.
    db_session.add(Podcast(name="Another", slug="another"))
    db_session.commit()
    second = get_catalog_stats(db_session, cache=cache, ttl_seconds=60)

    assert_that(second).is_equal_to(first)
    assert_that(second.podcasts).is_equal_to(1)


def test_get_catalog_stats_recomputes_after_ttl_expiry(db_session: Session) -> None:
    """Once the TTL has elapsed the next call recomputes from the DB."""
    _seed_catalog(db_session)
    clock = _Clock()
    cache = TTLCache(clock=clock)

    first = get_catalog_stats(db_session, cache=cache, ttl_seconds=10)
    db_session.add(Podcast(name="Another", slug="another"))
    db_session.commit()
    clock.now += 11
    second = get_catalog_stats(db_session, cache=cache, ttl_seconds=10)

    assert_that(first.podcasts).is_equal_to(1)
    assert_that(second.podcasts).is_equal_to(2)


def test_get_catalog_stats_bypasses_cache_when_ttl_zero(db_session: Session) -> None:
    """A non-positive TTL disables caching (used to opt out via settings)."""
    _seed_catalog(db_session)
    cache = TTLCache()

    get_catalog_stats(db_session, cache=cache, ttl_seconds=0)

    assert_that(cache.get(CATALOG_STATS_CACHE_KEY)).is_none()


@pytest.fixture
def stats_test_client(db_session: Session) -> Iterator[tuple[TestClient, TTLCache]]:
    """Return a client + shared TTLCache override so tests can inspect it."""
    app = create_app()
    cache = TTLCache()

    def override_get_db() -> Iterator[Session]:
        """Yield the in-memory session used across the test module."""
        yield db_session

    def override_get_app_cache() -> TTLCache:
        """Return the shared cache instance the test can inspect directly."""
        return cache

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_app_cache] = override_get_app_cache
    with TestClient(app) as test_client:
        yield test_client, cache


def test_stats_endpoint_returns_counts(
    stats_test_client: tuple[TestClient, TTLCache],
    db_session: Session,
) -> None:
    """The endpoint serves the aggregate payload with 200 OK."""
    client, _cache = stats_test_client
    _seed_catalog(db_session)

    response = client.get("/api/v2/stats")

    assert_that(response.status_code).is_equal_to(200)
    body = response.json()
    assert_that(body["podcasts"]).is_equal_to(1)
    assert_that(body["episodes"]).is_equal_to(1)
    assert_that(body["media"]).is_equal_to(3)
    assert_that(body["mentions"]).is_equal_to(2)
    assert_that([row["media_type"] for row in body["top_media_types"]]).is_equal_to(
        ["book", "movie"],
    )


def test_stats_endpoint_serves_cache_on_repeat_call(
    stats_test_client: tuple[TestClient, TTLCache],
    db_session: Session,
) -> None:
    """A repeat call within the TTL window returns the cached payload."""
    client, cache = stats_test_client
    _seed_catalog(db_session)

    first = client.get("/api/v2/stats").json()
    # Mutate the DB after the first call. If the cache is honored the counts
    # returned from the second call should be identical to the first.
    db_session.add(Podcast(name="Another", slug="another"))
    db_session.commit()
    second = client.get("/api/v2/stats").json()

    assert_that(second).is_equal_to(first)
    assert_that(cache.get(CATALOG_STATS_CACHE_KEY)).is_not_none()


def test_stats_endpoint_honors_settings_from_create_app(
    db_session: Session,
) -> None:
    """``create_app(settings=...)`` reaches the ``/api/v2/stats`` handler.

    Explicitly building the app with ``settings.stats_cache.ttl_seconds=0`` and no
    other override must produce a response that bypasses the cache — i.e.
    each call recomputes and no entry lands in ``app.state.cache``. If the
    dependency instead resolved to the cached global settings, the default
    30-second TTL would apply and this test would fail.
    """
    settings = Settings(stats_cache=StatsCacheSettings(ttl_seconds=0))
    app = create_app(settings=settings)

    def override_get_db() -> Iterator[Session]:
        """Return the in-memory session shared across the test module."""
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        _seed_catalog(db_session)
        response_before = client.get("/api/v2/stats")
        db_session.add(Podcast(name="Another", slug="another"))
        db_session.commit()
        response_after = client.get("/api/v2/stats")

    assert_that(response_before.status_code).is_equal_to(200)
    assert_that(response_after.status_code).is_equal_to(200)
    assert_that(response_before.json()["podcasts"]).is_equal_to(1)
    assert_that(response_after.json()["podcasts"]).is_equal_to(2)
    # ttl=0 means no caching → the process-wide cache stays empty even after
    # a successful request.
    assert_that(app.state.cache.get(CATALOG_STATS_CACHE_KEY)).is_none()

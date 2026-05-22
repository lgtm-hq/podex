"""Tests for read-model caching helpers."""

from assertpy import assert_that
from pytest import MonkeyPatch
from sqlalchemy.orm import Session

from podex.models import Media, MediaType
from podex.services import trend_queries
from podex.services.read_model_cache import TtlCache


def test_ttl_cache_reuses_value_until_expiry() -> None:
    """Verify cache hits reuse the loaded value until the TTL expires."""
    now = 10.0
    cache: TtlCache[str] = TtlCache(now=lambda: now)
    calls = 0

    def load_value() -> str:
        nonlocal calls
        calls += 1
        return f"value-{calls}"

    first = cache.get_or_set(key="stats", ttl_seconds=30, loader=load_value)
    second = cache.get_or_set(key="stats", ttl_seconds=30, loader=load_value)

    assert_that(first).is_equal_to("value-1")
    assert_that(second).is_equal_to("value-1")
    assert_that(calls).is_equal_to(1)

    now = 41.0
    third = cache.get_or_set(key="stats", ttl_seconds=30, loader=load_value)

    assert_that(third).is_equal_to("value-2")
    assert_that(calls).is_equal_to(2)


def test_ttl_cache_can_be_disabled_with_zero_ttl() -> None:
    """Verify a non-positive TTL bypasses storage."""
    cache: TtlCache[int] = TtlCache()
    calls = 0

    def load_value() -> int:
        nonlocal calls
        calls += 1
        return calls

    assert_that(
        cache.get_or_set(key="stats", ttl_seconds=0, loader=load_value)
    ).is_equal_to(1)
    assert_that(
        cache.get_or_set(key="stats", ttl_seconds=0, loader=load_value)
    ).is_equal_to(2)


def test_trend_query_cache_reuses_uncached_loader(
    db_session: Session,
    monkeypatch: MonkeyPatch,
) -> None:
    """Verify trend read models cache by query shape and catalog signature."""
    trend_queries.clear_trend_cache()
    calls = 0

    def load_stats_by_type(
        *,
        db: Session,
    ) -> list[trend_queries.MediaTypeStatsData]:
        nonlocal calls
        calls += 1
        return [
            trend_queries.MediaTypeStatsData(
                type=MediaType.BOOK,
                count=1,
                mention_count=0,
            )
        ]

    monkeypatch.setattr(
        trend_queries,
        "_get_stats_by_type_uncached",
        load_stats_by_type,
    )

    first = trend_queries.get_stats_by_type(db=db_session)
    second = trend_queries.get_stats_by_type(db=db_session)

    assert_that(first).is_equal_to(second)
    assert_that(calls).is_equal_to(1)


def test_trend_query_cache_invalidates_when_catalog_signature_changes(
    db_session: Session,
) -> None:
    """Verify catalog changes force a new cached stats value."""
    trend_queries.clear_trend_cache()

    empty_stats = trend_queries.get_overview_stats(db=db_session)
    db_session.add(Media(type=MediaType.BOOK.value, title="New Book"))
    db_session.commit()
    updated_stats = trend_queries.get_overview_stats(db=db_session)

    assert_that(empty_stats.total_media).is_equal_to(0)
    assert_that(updated_stats.total_media).is_equal_to(1)

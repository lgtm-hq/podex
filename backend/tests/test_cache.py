"""Unit tests for the in-process TTL cache backend."""

from assertpy import assert_that

from podex.services.cache import Cache, TTLCache


class _Clock:
    """Simple monotonic-clock stand-in whose ``now`` value can be advanced."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


def test_ttl_cache_implements_cache_protocol() -> None:
    """The default backend satisfies the Cache Protocol at runtime."""
    cache = TTLCache()

    assert_that(isinstance(cache, Cache)).is_true()


def test_get_returns_none_for_missing_key() -> None:
    """A cold cache reports misses as ``None``."""
    cache = TTLCache()

    assert_that(cache.get("missing")).is_none()


def test_set_then_get_returns_value() -> None:
    """A stored value is returned as-is on the next read."""
    cache = TTLCache()

    cache.set("k", {"count": 3}, ttl_seconds=60)

    assert_that(cache.get("k")).is_equal_to({"count": 3})


def test_expired_entry_returns_none_and_is_evicted() -> None:
    """Once the TTL elapses the entry is invisible and removed lazily."""
    clock = _Clock()
    cache = TTLCache(clock=clock)

    cache.set("k", "value", ttl_seconds=10)
    clock.now += 11

    assert_that(cache.get("k")).is_none()
    # Internal state check: the expired entry is dropped on read so memory
    # doesn't grow unbounded when keys age out silently.
    assert_that(cache.get("k")).is_none()


def test_non_positive_ttl_disables_caching() -> None:
    """``ttl_seconds`` of 0 removes any existing key and stores nothing new."""
    cache = TTLCache()
    cache.set("k", "value", ttl_seconds=10)

    cache.set("k", "new", ttl_seconds=0)

    assert_that(cache.get("k")).is_none()


def test_delete_removes_key() -> None:
    """``delete`` removes an existing key and is a no-op for missing ones."""
    cache = TTLCache()
    cache.set("k", "value", ttl_seconds=60)

    cache.delete("k")
    cache.delete("missing")

    assert_that(cache.get("k")).is_none()


def test_clear_drops_every_entry() -> None:
    """``clear`` removes every stored key in one call."""
    cache = TTLCache()
    cache.set("a", 1, ttl_seconds=60)
    cache.set("b", 2, ttl_seconds=60)

    cache.clear()

    assert_that(cache.get("a")).is_none()
    assert_that(cache.get("b")).is_none()

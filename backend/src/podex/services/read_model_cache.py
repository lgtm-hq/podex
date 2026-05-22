"""Small in-process cache for projection-style read models."""

from collections.abc import Callable, Hashable
from dataclasses import dataclass
from threading import RLock
from time import monotonic
from typing import Generic, TypeVar

ValueT = TypeVar("ValueT")


@dataclass(frozen=True, slots=True)
class CacheEntry(Generic[ValueT]):
    """Cached value with an absolute expiry timestamp."""

    value: ValueT
    expires_at: float


class TtlCache(Generic[ValueT]):
    """Thread-safe TTL cache for deterministic read-model reuse."""

    def __init__(
        self,
        *,
        now: Callable[[], float] = monotonic,
    ) -> None:
        """Initialize the cache.

        Args:
            now: Clock used for expiry checks.
        """
        self._items: dict[Hashable, CacheEntry[ValueT]] = {}
        self._now = now
        self._lock = RLock()

    def get_or_set(
        self,
        *,
        key: Hashable,
        ttl_seconds: int,
        loader: Callable[[], ValueT],
    ) -> ValueT:
        """Return a cached value or populate it through the loader.

        Args:
            key: Stable cache key for the read model.
            ttl_seconds: Number of seconds to reuse a populated value.
            loader: Callable used to compute a cache miss.

        Returns:
            Cached or freshly loaded value.
        """
        if ttl_seconds <= 0:
            return loader()

        now = self._now()
        with self._lock:
            entry = self._items.get(key)
            if entry is not None and entry.expires_at > now:
                return entry.value

            value = loader()
            self._items[key] = CacheEntry(
                value=value,
                expires_at=now + ttl_seconds,
            )
            return value

    def clear(self) -> None:
        """Remove all cached entries."""
        with self._lock:
            self._items.clear()

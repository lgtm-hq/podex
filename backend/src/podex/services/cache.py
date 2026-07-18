"""Small in-process TTL cache for cheap read-through caching.

The cache is intentionally minimal: a thread-safe ``dict`` of entries with
absolute-expiry timestamps. It is a good fit for a single dev/prod process
(the current deployment shape) and covers the aggregate/stats surfaces where
we tolerate a few seconds of staleness in exchange for skipping repeated
aggregation queries.

The public surface is deliberately shaped around a small :class:`Cache`
protocol so a Redis-backed implementation can be dropped in later without
touching call sites. Invalidation is TTL-based only for now; write-time hooks
(e.g. purge stats on ingestion) will be added when the ingestion pipeline
lands.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

MonotonicClock = Callable[[], float]
"""Zero-arg callable returning a monotonic timestamp in seconds."""


@runtime_checkable
class Cache(Protocol):
    """Minimal cache contract shared by every backend.

    Implementations must be safe to use concurrently from multiple threads.
    """

    def get(self, key: str) -> Any | None:
        """Return the cached value for ``key`` or ``None`` if absent/expired."""

    def set(self, key: str, value: Any, *, ttl_seconds: float) -> None:
        """Store ``value`` under ``key`` for ``ttl_seconds`` seconds."""

    def delete(self, key: str) -> None:
        """Remove ``key`` from the cache. A no-op if it was not present."""

    def clear(self) -> None:
        """Drop every entry (useful for tests and manual invalidation)."""


@dataclass(frozen=True)
class _Entry:
    """A single cached value plus the monotonic instant it expires at."""

    value: Any
    expires_at: float


class TTLCache:
    """Thread-safe, in-process TTL cache.

    Values are keyed by opaque strings. Each ``set`` records an absolute
    monotonic deadline; ``get`` returns ``None`` once the deadline has passed
    and lazily evicts the expired entry so memory doesn't grow unbounded.

    Args:
        clock: Zero-arg callable returning a monotonic timestamp in seconds.
            Defaults to :func:`time.monotonic`; tests inject a fake clock to
            exercise TTL expiry without sleeping.
    """

    def __init__(self, *, clock: MonotonicClock = time.monotonic) -> None:
        self._clock: MonotonicClock = clock
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        """Return the cached value for ``key`` or ``None`` if absent/expired.

        Expired entries are removed opportunistically on read so a rarely-hit
        key does not linger in memory once its TTL elapses.
        """
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if entry.expires_at <= self._clock():
                del self._entries[key]
                return None
            return entry.value

    def set(self, key: str, value: Any, *, ttl_seconds: float) -> None:
        """Store ``value`` for ``ttl_seconds`` seconds.

        A non-positive TTL is treated as "already expired" — the key is
        removed and no new entry is written. This makes it trivial to disable
        caching from configuration by setting the TTL to ``0``.
        """
        if ttl_seconds <= 0:
            with self._lock:
                self._entries.pop(key, None)
            return
        expires_at = self._clock() + ttl_seconds
        with self._lock:
            self._entries[key] = _Entry(value=value, expires_at=expires_at)

    def delete(self, key: str) -> None:
        """Remove ``key`` from the cache. A no-op if it was not present."""
        with self._lock:
            self._entries.pop(key, None)

    def clear(self) -> None:
        """Drop every entry."""
        with self._lock:
            self._entries.clear()

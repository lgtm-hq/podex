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
from typing import Any, Final, Protocol, TypeVar, cast, runtime_checkable

MonotonicClock = Callable[[], float]
"""Zero-arg callable returning a monotonic timestamp in seconds."""

T = TypeVar("T")

_MISSING: Final = object()
"""Internal sentinel distinguishing "no entry" from a cached ``None``."""


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

    def get_or_compute(
        self,
        key: str,
        *,
        ttl_seconds: float,
        loader: Callable[[], T],
    ) -> T:
        """Return the cached value or compute-and-store it single-flight.

        Implementations must ensure that concurrent callers observing a cache
        miss only invoke ``loader`` once per key; the remaining callers must
        wait for that in-flight computation and return the freshly stored
        value. A ``loader`` returning ``None`` is a valid cached result and
        must be served from the store for the full TTL, not recomputed.
        """


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

    :meth:`get_or_compute` layers a per-key lock on top of the store so
    concurrent cold-miss callers do not stampede an expensive loader (see
    issue #15).

    Args:
        clock: Zero-arg callable returning a monotonic timestamp in seconds.
            Defaults to :func:`time.monotonic`; tests inject a fake clock to
            exercise TTL expiry without sleeping.
    """

    def __init__(self, *, clock: MonotonicClock = time.monotonic) -> None:
        self._clock: MonotonicClock = clock
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.Lock()
        # Per-key coordinate locks used by ``get_or_compute`` to serialize
        # concurrent cold misses. Guarded by ``self._lock`` on lookup so the
        # mapping stays consistent; individual keys can then be locked without
        # blocking unrelated ones.
        self._key_locks: dict[str, threading.Lock] = {}

    def get(self, key: str) -> Any | None:
        """Return the cached value for ``key`` or ``None`` if absent/expired.

        ``None`` is ambiguous here by design (a cached ``None`` and a miss
        look identical); :meth:`get_or_compute` uses the sentinel-based
        :meth:`_lookup` internally so it never confuses the two.
        """
        value = self._lookup(key)
        return None if value is _MISSING else value

    def _lookup(self, key: str) -> Any:
        """Return the live value for ``key`` or ``_MISSING`` if absent/expired.

        Expired entries are removed opportunistically on read so a rarely-hit
        key does not linger in memory once its TTL elapses.
        """
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return _MISSING
            if entry.expires_at <= self._clock():
                del self._entries[key]
                return _MISSING
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

    def _get_key_lock(self, key: str) -> threading.Lock:
        """Return the per-key lock for ``key``, creating it on first use.

        The mapping itself is protected by ``self._lock`` so lookups stay
        consistent under concurrent access; the returned lock can then be
        acquired independently without blocking unrelated keys.
        """
        with self._lock:
            existing = self._key_locks.get(key)
            if existing is not None:
                return existing
            new_lock = threading.Lock()
            self._key_locks[key] = new_lock
            return new_lock

    def get_or_compute(
        self,
        key: str,
        *,
        ttl_seconds: float,
        loader: Callable[[], T],
    ) -> T:
        """Return the cached value or compute-and-store it single-flight.

        A fast-path :meth:`_lookup` avoids taking the per-key lock when the
        value is already cached. On a miss the per-key lock is acquired and
        the cache re-checked (double-checked locking) before ``loader`` runs,
        so concurrent cold callers wait for the in-flight computation instead
        of each recomputing the value. Hits are detected with an internal
        sentinel rather than ``None``, so a loader that legitimately returns
        ``None`` has that result cached and served for the full TTL.

        A non-positive ``ttl_seconds`` disables caching entirely: ``loader``
        is invoked directly and its result is returned without touching the
        store, matching :meth:`set` semantics.

        Args:
            key: Cache key to read and (on miss) populate.
            ttl_seconds: TTL applied when the loader result is stored.
            loader: Zero-arg callable that computes the value on a miss.

        Returns:
            The cached value if present and unexpired, otherwise the freshly
            computed value returned by ``loader``.
        """
        if ttl_seconds <= 0:
            return loader()
        cached = self._lookup(key)
        if cached is not _MISSING:
            return cast("T", cached)
        with self._get_key_lock(key):
            cached = self._lookup(key)
            if cached is not _MISSING:
                return cast("T", cached)
            fresh = loader()
            self.set(key, fresh, ttl_seconds=ttl_seconds)
            return fresh

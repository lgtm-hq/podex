"""In-memory per-client rate limiting.

This limiter keeps a sliding window of request timestamps per client key in
process memory. It is intentionally simple and process-local: each worker
enforces its own counts. Follow-up #107 will replace this store with a shared
backend (e.g. Redis) so limits hold across multiple workers/instances.
"""

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimitDecision:
    """Outcome of a single rate-limit check.

    Attributes:
        allowed: Whether the request is permitted under the current window.
        limit: The maximum number of requests allowed per window.
        remaining: Requests still permitted in the current window.
        reset_after: Seconds until the window has fully drained.
        retry_after: Seconds the client should wait before retrying. ``0`` when
            the request is allowed.
    """

    allowed: bool
    limit: int
    remaining: int
    reset_after: float
    retry_after: float


class SlidingWindowRateLimiter:
    """Thread-safe sliding-window rate limiter keyed by an arbitrary client id.

    A request is allowed when the number of hits recorded within the trailing
    ``window_seconds`` is below ``max_requests``. Timestamps outside the window
    are evicted lazily on each check.
    """

    def __init__(self, *, max_requests: int, window_seconds: float) -> None:
        """Initialise the limiter.

        Args:
            max_requests: Maximum requests permitted per rolling window. Must be
                a positive integer.
            window_seconds: Length of the rolling window in seconds. Must be
                positive.

        Raises:
            ValueError: If ``max_requests`` or ``window_seconds`` is not
                positive.
        """
        if max_requests <= 0:
            raise ValueError("max_requests must be positive")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    @property
    def max_requests(self) -> int:
        """Return the configured per-window request ceiling."""
        return self._max_requests

    @property
    def window_seconds(self) -> float:
        """Return the configured rolling window length in seconds."""
        return self._window_seconds

    def check(self, key: str, *, now: float | None = None) -> RateLimitDecision:
        """Record and evaluate a request for ``key`` against the window.

        When the request is allowed its timestamp is recorded; when it is denied
        no timestamp is added, so a rejected client does not extend its own
        penalty window.

        Args:
            key: Stable identifier for the client (typically the remote IP).
            now: Optional monotonic timestamp override, primarily for tests. When
                omitted, :func:`time.monotonic` is used.

        Returns:
            RateLimitDecision: The evaluation result including remaining quota
            and retry hints.
        """
        current = time.monotonic() if now is None else now
        window_start = current - self._window_seconds
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= window_start:
                hits.popleft()

            if len(hits) >= self._max_requests:
                oldest = hits[0]
                retry_after = max(0.0, oldest + self._window_seconds - current)
                return RateLimitDecision(
                    allowed=False,
                    limit=self._max_requests,
                    remaining=0,
                    reset_after=retry_after,
                    retry_after=retry_after,
                )

            hits.append(current)
            remaining = self._max_requests - len(hits)
            reset_after = hits[0] + self._window_seconds - current
            return RateLimitDecision(
                allowed=True,
                limit=self._max_requests,
                remaining=remaining,
                reset_after=reset_after,
                retry_after=0.0,
            )

    def reset(self) -> None:
        """Clear all recorded hits, primarily to isolate tests."""
        with self._lock:
            self._hits.clear()

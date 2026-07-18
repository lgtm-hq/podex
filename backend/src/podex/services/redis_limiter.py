"""Redis-backed sliding-window rate limiter.

The in-memory :class:`~podex.services.limiter.SlidingWindowRateLimiter` keeps
per-client state in each worker, so N workers effectively multiply the
configured budget by N and every restart forgets recent hits. This module
implements the same sliding-window contract on top of a shared Redis store so
limits hold across workers and process restarts.

The critical section (evict stale hits, count, conditionally record the new
hit, refresh TTL) runs as a single ``EVAL`` so racing workers cannot both
observe "room for one more" and each admit a request. A denied request is not
recorded, mirroring the in-memory limiter's behaviour so a rejected client
never extends its own penalty window.
"""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING, cast

from podex.services.limiter import RateLimitDecision

if TYPE_CHECKING:
    from redis import Redis


_LUA_SLIDING_WINDOW = """
local zkey = KEYS[1]
local now = tonumber(ARGV[1])
local window_start = tonumber(ARGV[2])
local max_requests = tonumber(ARGV[3])
local ttl_seconds = tonumber(ARGV[4])
local member = ARGV[5]

redis.call('ZREMRANGEBYSCORE', zkey, '-inf', window_start)
local count = redis.call('ZCARD', zkey)
local allowed = 0
if count < max_requests then
    redis.call('ZADD', zkey, now, member)
    count = count + 1
    allowed = 1
end
redis.call('EXPIRE', zkey, ttl_seconds)
local first = redis.call('ZRANGE', zkey, 0, 0)
local oldest_score = 0
if #first >= 1 then
    local score = redis.call('ZSCORE', zkey, first[1])
    if score then
        oldest_score = tonumber(score)
    end
end
return {allowed, count, tostring(oldest_score)}
"""


class RedisSlidingWindowRateLimiter:
    """Sliding-window rate limiter backed by a shared Redis instance.

    State is stored in one sorted set per client key: scores are wall-clock
    timestamps (``time.time``) and members are unique per hit so identical
    timestamps do not collide. All mutation happens inside a Lua script so the
    evict/count/admit sequence is atomic across concurrent workers.

    Args:
        client: A ``redis.Redis``-compatible client (real or ``fakeredis``).
            Must support ``eval`` with the script above.
        max_requests: Maximum requests permitted per rolling window.
        window_seconds: Length of the rolling window in seconds.
        key_prefix: Prefix applied to every Redis key so multiple applications
            can share one Redis without colliding.

    Raises:
        ValueError: If ``max_requests`` or ``window_seconds`` is not positive.
    """

    def __init__(
        self,
        client: Redis,
        *,
        max_requests: int,
        window_seconds: float,
        key_prefix: str = "podex:ratelimit",
    ) -> None:
        if max_requests <= 0:
            raise ValueError("max_requests must be positive")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self._client = client
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._key_prefix = key_prefix
        self._ttl_seconds = max(1, int(window_seconds) + 1)

    @property
    def max_requests(self) -> int:
        """Return the configured per-window request ceiling."""
        return self._max_requests

    @property
    def window_seconds(self) -> float:
        """Return the configured rolling window length in seconds."""
        return self._window_seconds

    def _redis_key(self, key: str) -> str:
        """Return the fully namespaced Redis sorted-set key for ``key``."""
        return f"{self._key_prefix}:{key}"

    def check(self, key: str, *, now: float | None = None) -> RateLimitDecision:
        """Record and evaluate a request for ``key`` against the shared window.

        Args:
            key: Stable identifier for the client (typically the remote IP).
            now: Optional wall-clock timestamp override, primarily for tests.
                When omitted, :func:`time.time` is used so scores are directly
                comparable across processes.

        Returns:
            RateLimitDecision: The evaluation result including remaining quota
            and retry hints.
        """
        current = time.time() if now is None else now
        window_start = current - self._window_seconds
        member = f"{current}:{uuid.uuid4().hex}"

        raw = self._client.eval(
            _LUA_SLIDING_WINDOW,
            1,
            self._redis_key(key),
            current,
            window_start,
            self._max_requests,
            self._ttl_seconds,
            member,
        )
        result = cast(list[object], raw)
        allowed_flag = int(cast(int, result[0]))
        count = int(cast(int, result[1]))
        oldest_score = float(cast(str | bytes, result[2]))

        if not allowed_flag:
            retry_after = max(0.0, oldest_score + self._window_seconds - current)
            return RateLimitDecision(
                allowed=False,
                limit=self._max_requests,
                remaining=0,
                reset_after=retry_after,
                retry_after=retry_after,
            )

        remaining = self._max_requests - count
        reset_after = max(0.0, oldest_score + self._window_seconds - current)
        return RateLimitDecision(
            allowed=True,
            limit=self._max_requests,
            remaining=remaining,
            reset_after=reset_after,
            retry_after=0.0,
        )

    def reset(self) -> None:
        """Remove every rate-limit key under this limiter's prefix.

        Intended for isolating tests: matches
        :meth:`SlidingWindowRateLimiter.reset` semantically. Uses ``SCAN`` so
        the operation is safe against large keyspaces.
        """
        pattern = f"{self._key_prefix}:*"
        for raw_key in self._client.scan_iter(match=pattern):
            self._client.delete(raw_key)

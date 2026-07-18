"""Redis-backed sliding-window rate limiter.

The in-memory :class:`~podex.services.limiter.SlidingWindowRateLimiter` keeps
per-client state in each worker, so N workers effectively multiply the
configured budget by N and every restart forgets recent hits. This module
implements the same sliding-window contract on top of a shared Redis store so
limits hold across workers and process restarts.

Design notes:

* The critical section (evict stale hits, count, conditionally record the new
  hit, refresh TTL) runs as a single ``EVAL`` so racing workers cannot both
  observe "room for one more" and each admit a request.
* The timestamp used for scoring comes from Redis' own ``TIME`` command inside
  the script rather than from each worker's local clock. Otherwise clock skew
  between workers could evict another worker's hits early or over-block
  clients. Tests still override the clock via ``now=`` for determinism.
* A denied request is not recorded, mirroring the in-memory limiter so a
  rejected client never extends its own penalty window.
* Redis outages must not take user traffic down. Every ``EVAL`` runs against a
  client with finite connect/socket timeouts (configured by the factory) and
  is guarded by a fail-open ``try``/``except redis.RedisError``: on error the
  request is admitted so a broken cache never turns into a hard 5xx.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, cast

import redis

from podex.services.limiter import RateLimitDecision

if TYPE_CHECKING:
    from redis import Redis


_logger = logging.getLogger(__name__)

_LUA_SLIDING_WINDOW = """
local zkey = KEYS[1]
local now_override = ARGV[1]
local window_seconds = tonumber(ARGV[2])
local max_requests = tonumber(ARGV[3])
local ttl_seconds = tonumber(ARGV[4])
local member = ARGV[5]

local now
if now_override == '' then
    local t = redis.call('TIME')
    now = tonumber(t[1]) + tonumber(t[2]) / 1000000
else
    now = tonumber(now_override)
end

local window_start = now - window_seconds
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
return {allowed, count, tostring(oldest_score), tostring(now)}
"""


class RedisSlidingWindowRateLimiter:
    """Sliding-window rate limiter backed by a shared Redis instance.

    State is stored in one sorted set per client key: scores are Redis-provided
    wall-clock timestamps (from ``redis.call('TIME')``) and members are unique
    per hit so identical timestamps do not collide. All mutation happens inside
    a Lua script so the evict/count/admit sequence is atomic across concurrent
    workers.

    Args:
        client: A ``redis.Redis``-compatible client (real or ``fakeredis``).
            Should be configured with finite ``socket_connect_timeout`` and
            ``socket_timeout`` so a wedged Redis does not stall the request
            path. :func:`podex.services.limiter_factory.build_rate_limiter`
            wires those in when it constructs the client.
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

    def _fail_open_decision(self) -> RateLimitDecision:
        """Return an allow decision used when Redis is unreachable.

        Rate limiting is a soft guarantee; taking the API down whenever the
        shared cache is unavailable would be a worse outcome than briefly
        admitting more traffic than the limit. The middleware exposes the
        configured ceiling but reports full remaining quota so a subsequent
        healthy check can enforce again.
        """
        return RateLimitDecision(
            allowed=True,
            limit=self._max_requests,
            remaining=self._max_requests,
            reset_after=0.0,
            retry_after=0.0,
        )

    def check(self, key: str, *, now: float | None = None) -> RateLimitDecision:
        """Record and evaluate a request for ``key`` against the shared window.

        The Lua script derives its own timestamp from ``redis.call('TIME')``
        in production so every worker agrees on the clock. Callers may pass
        ``now`` to override this for deterministic tests.

        Args:
            key: Stable identifier for the client (typically the remote IP).
            now: Optional wall-clock timestamp override, intended for tests.
                When omitted, the script uses Redis' own clock so scores are
                directly comparable across workers regardless of local skew.

        Returns:
            RateLimitDecision: The evaluation result including remaining quota
            and retry hints. If Redis is unreachable the limiter fails open
            (see :meth:`_fail_open_decision`).
        """
        now_arg = "" if now is None else str(now)
        member = uuid.uuid4().hex

        try:
            raw = self._client.eval(
                _LUA_SLIDING_WINDOW,
                1,
                self._redis_key(key),
                now_arg,
                self._window_seconds,
                self._max_requests,
                self._ttl_seconds,
                member,
            )
        except redis.RedisError:
            _logger.warning(
                "rate limiter redis check failed; failing open for key=%s",
                key,
                exc_info=True,
            )
            return self._fail_open_decision()

        result = cast(list[object], raw)
        allowed_flag = int(cast(int, result[0]))
        count = int(cast(int, result[1]))
        oldest_score = float(cast(str | bytes, result[2]))
        current = float(cast(str | bytes, result[3]))

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
        the operation is safe against large keyspaces. Redis errors are
        swallowed so ``reset`` (which callers use for test-fixture cleanup)
        never masks the real failure with a scan error.
        """
        pattern = f"{self._key_prefix}:*"
        try:
            for raw_key in self._client.scan_iter(match=pattern):
                self._client.delete(raw_key)
        except redis.RedisError:
            _logger.warning("rate limiter redis reset failed; ignoring", exc_info=True)

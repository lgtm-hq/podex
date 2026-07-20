"""Rate-limiter backend selection.

The application talks to a single :class:`~podex.services.limiter.RateLimiter`
interface; this module hides which concrete backend gets wired in. When
``settings.rate_limit.redis_url`` is configured, requests share state through
Redis so horizontal scaling and restarts don't leak budget. Otherwise we fall
back to the in-process sliding-window implementation, which is what
development, CI, and single-worker deployments actually need.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from podex.services.limiter import RateLimiter, SlidingWindowRateLimiter

if TYPE_CHECKING:
    from podex.config import Settings

# Finite Redis timeouts so a wedged or slow shared store does not stall the
# request path. Rate limiting is a soft guarantee: values are intentionally
# tight (sub-second) because the limiter runs inline in every request, and the
# limiter itself fails open on ``redis.RedisError`` so a bounded timeout is
# always preferable to hanging.
_REDIS_CONNECT_TIMEOUT_SECONDS = 0.5
_REDIS_SOCKET_TIMEOUT_SECONDS = 0.25


def build_rate_limiter(settings: Settings) -> RateLimiter:
    """Return the limiter backend selected by ``settings``.

    Selection rules:

    * ``settings.rate_limit.redis_url`` is empty → return an in-memory
      :class:`SlidingWindowRateLimiter`.
    * ``settings.rate_limit.redis_url`` is set → return a
      :class:`~podex.services.redis_limiter.RedisSlidingWindowRateLimiter`
      pointed at that URL, with its shared key prefix taken from
      ``settings.rate_limit.redis_prefix``.

    ``redis`` is imported lazily so environments that never opt into the
    shared store don't pay the import cost and don't require the extra to be
    resolvable at boot for image builds that strip it. When the Redis backend
    is selected the client is built with finite connect and socket timeouts
    so an unhealthy Redis instance surfaces quickly and the limiter's
    fail-open path (see
    :class:`~podex.services.redis_limiter.RedisSlidingWindowRateLimiter`)
    can admit requests instead of stalling them.

    Args:
        settings: The active application settings.

    Returns:
        RateLimiter: A ready-to-use limiter matching the configured backend.
    """
    if not settings.rate_limit.redis_url:
        return SlidingWindowRateLimiter(
            max_requests=settings.rate_limit.max_requests,
            window_seconds=settings.rate_limit.window_seconds,
        )

    import redis

    from podex.services.redis_limiter import RedisSlidingWindowRateLimiter

    client = redis.Redis.from_url(
        settings.rate_limit.redis_url,
        socket_connect_timeout=_REDIS_CONNECT_TIMEOUT_SECONDS,
        socket_timeout=_REDIS_SOCKET_TIMEOUT_SECONDS,
    )
    return RedisSlidingWindowRateLimiter(
        client,
        max_requests=settings.rate_limit.max_requests,
        window_seconds=settings.rate_limit.window_seconds,
        key_prefix=settings.rate_limit.redis_prefix,
    )

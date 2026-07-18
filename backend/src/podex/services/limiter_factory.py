"""Rate-limiter backend selection.

The application talks to a single :class:`~podex.services.limiter.RateLimiter`
interface; this module hides which concrete backend gets wired in. When
``rate_limit_redis_url`` is configured, requests share state through Redis so
horizontal scaling and restarts don't leak budget. Otherwise we fall back to
the in-process sliding-window implementation, which is what development, CI,
and single-worker deployments actually need.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from podex.services.limiter import RateLimiter, SlidingWindowRateLimiter

if TYPE_CHECKING:
    from podex.config import Settings


def build_rate_limiter(settings: Settings) -> RateLimiter:
    """Return the limiter backend selected by ``settings``.

    Selection rules:

    * ``settings.rate_limit_redis_url`` is empty → return an in-memory
      :class:`SlidingWindowRateLimiter`.
    * ``settings.rate_limit_redis_url`` is set → return a
      :class:`~podex.services.redis_limiter.RedisSlidingWindowRateLimiter`
      pointed at that URL, with its shared key prefix taken from
      ``settings.rate_limit_redis_prefix``.

    ``redis`` is imported lazily so environments that never opt into the
    shared store don't pay the import cost and don't require the extra to be
    resolvable at boot for image builds that strip it.

    Args:
        settings: The active application settings.

    Returns:
        RateLimiter: A ready-to-use limiter matching the configured backend.
    """
    if not settings.rate_limit_redis_url:
        return SlidingWindowRateLimiter(
            max_requests=settings.rate_limit_max_requests,
            window_seconds=settings.rate_limit_window_seconds,
        )

    import redis

    from podex.services.redis_limiter import RedisSlidingWindowRateLimiter

    client = redis.Redis.from_url(settings.rate_limit_redis_url)
    return RedisSlidingWindowRateLimiter(
        client,
        max_requests=settings.rate_limit_max_requests,
        window_seconds=settings.rate_limit_window_seconds,
        key_prefix=settings.rate_limit_redis_prefix,
    )

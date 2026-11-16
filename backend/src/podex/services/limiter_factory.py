"""Rate-limiter backend selection.

The application talks to a single :class:`~podex.services.limiter.RateLimiter`
interface; this module hides which concrete backend gets wired in. When
``settings.rate_limit.redis_url`` is configured, requests share state through
Redis so horizontal scaling and restarts don't leak budget. Otherwise we fall
back to the in-process sliding-window implementation, which is what
development, CI, and single-worker deployments actually need.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from podex.services.limiter import RateLimiter, SlidingWindowRateLimiter
from podex.unusable_settings import without_setting_text

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

    Raises:
        UnusableSettingError: When the Redis URL cannot be parsed, or its
            connection options are rejected by the client's connection class;
            the message names the setting and the error class only.
    """
    redis_url = settings.rate_limit.redis_url.get_secret_value()
    if not redis_url:
        return SlidingWindowRateLimiter(
            max_requests=settings.rate_limit.max_requests,
            window_seconds=settings.rate_limit.window_seconds,
        )

    import redis

    from podex.services.redis_limiter import RedisSlidingWindowRateLimiter

    # Parsing the URL can quote part of its password (a "/", "#" or "?" in it
    # moves the rest into the port or the query): parse it with the client's
    # own parser first, which raises ValueError for bad input, where that is
    # reported by class only. The client is then built outside that check.
    parse_redis_url: Callable[[str], dict[str, object]] = redis.connection.parse_url
    without_setting_text(
        lambda: parse_redis_url(redis_url),
        source="PODEX_RATE_LIMIT__REDIS_URL",
        failures=(ValueError,),
    )
    client: redis.Redis = redis.Redis.from_url(
        redis_url,
        socket_connect_timeout=_REDIS_CONNECT_TIMEOUT_SECONDS,
        socket_timeout=_REDIS_SOCKET_TIMEOUT_SECONDS,
    )
    # The client connects lazily, so the URL's query options first reach the
    # connection class on the first rate-limited request, whose errors quote
    # them: an unknown option name (TypeError), a certificate-requirements
    # flag (RedisError) or a port (ValueError). Build one unconnected
    # connection from the client's own pool now, where these are reported by
    # class only. No socket is opened and the client is used unchanged.
    pool = client.connection_pool
    connection_class: Callable[..., object] = pool.connection_class
    connection_options: dict[str, object] = dict(pool.connection_kwargs)
    without_setting_text(
        lambda: connection_class(**connection_options),
        source="PODEX_RATE_LIMIT__REDIS_URL",
        failures=(redis.RedisError, TypeError, ValueError),
    )
    return RedisSlidingWindowRateLimiter(
        client,
        max_requests=settings.rate_limit.max_requests,
        window_seconds=settings.rate_limit.window_seconds,
        key_prefix=settings.rate_limit.redis_prefix,
    )

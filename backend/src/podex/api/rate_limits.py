"""Endpoint-level rate limiting for API routes."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from time import monotonic

from fastapi import Request

from podex.config import Settings

RATE_LIMIT_WINDOW_SECONDS = 60


@dataclass(frozen=True, slots=True)
class EndpointRateLimitRule:
    """Route-family rate-limit rule.

    Args:
        name: Stable bucket name used in response headers and in-memory keys.
        path_prefixes: URL path prefixes covered by this rule.
        requests_per_minute: Function that reads the active limit from settings.
        methods: Optional HTTP methods covered by this rule.
    """

    name: str
    path_prefixes: tuple[str, ...]
    requests_per_minute: Callable[[Settings], int]
    methods: frozenset[str] | None = None

    def matches(self, *, request: Request) -> bool:
        """Return whether this rule applies to the request.

        Args:
            request: Incoming HTTP request.

        Returns:
            True when the request path and method match the rule.
        """
        if self.methods is not None and request.method.upper() not in self.methods:
            return False
        return any(
            request.url.path == prefix or request.url.path.startswith(f"{prefix}/")
            for prefix in self.path_prefixes
        )


@dataclass(frozen=True, slots=True)
class EndpointRateLimitDecision:
    """Result of evaluating a request against a rate-limit bucket.

    Args:
        allowed: Whether the request is allowed to continue.
        rule_name: Name of the matched rule.
        limit: Maximum requests allowed in the active window.
        remaining: Remaining requests after this request.
        reset_seconds: Seconds until the bucket begins to reset.
    """

    allowed: bool
    rule_name: str
    limit: int
    remaining: int
    reset_seconds: int


class InMemoryEndpointRateLimiter:
    """Small process-local sliding-window limiter for endpoint buckets.

    The limiter starts with no buckets and stores request timestamps in memory.
    """

    def __init__(self) -> None:
        self._buckets: dict[tuple[str, str], deque[float]] = {}
        self._lock = Lock()

    def clear(self) -> None:
        """Clear all in-memory rate-limit buckets."""
        with self._lock:
            self._buckets.clear()

    def check(
        self,
        *,
        request: Request,
        settings: Settings,
    ) -> EndpointRateLimitDecision | None:
        """Evaluate the request against configured endpoint limits.

        Args:
            request: Incoming HTTP request.
            settings: Active application settings.

        Returns:
            A decision for matched endpoint rules, or None when no rule applies.
        """
        if not settings.rate_limit_enabled:
            return None

        rule = _match_endpoint_rate_limit_rule(request=request)
        if rule is None:
            return None

        limit = rule.requests_per_minute(settings)
        if limit <= 0:
            return None

        client_host = request.client.host if request.client else "unknown"
        key = (rule.name, client_host)
        now = monotonic()
        cutoff = now - RATE_LIMIT_WINDOW_SECONDS

        with self._lock:
            bucket = self._buckets.setdefault(key, deque())
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()

            if len(bucket) >= limit:
                oldest = bucket[0] if bucket else now
                reset_seconds = max(1, int(oldest + RATE_LIMIT_WINDOW_SECONDS - now))
                return EndpointRateLimitDecision(
                    allowed=False,
                    rule_name=rule.name,
                    limit=limit,
                    remaining=0,
                    reset_seconds=reset_seconds,
                )

            bucket.append(now)
            reset_seconds = RATE_LIMIT_WINDOW_SECONDS
            return EndpointRateLimitDecision(
                allowed=True,
                rule_name=rule.name,
                limit=limit,
                remaining=max(0, limit - len(bucket)),
                reset_seconds=reset_seconds,
            )


def _get_public_search_limit(settings: Settings) -> int:
    """Return the public search request limit.

    Args:
        settings: Active application settings.

    Returns:
        Public search requests allowed per minute.
    """
    return settings.public_search_rate_limit_per_minute


def _get_auth_limit(settings: Settings) -> int:
    """Return the auth request limit.

    Args:
        settings: Active application settings.

    Returns:
        Auth requests allowed per minute.
    """
    return settings.auth_rate_limit_per_minute


def _get_ops_limit(settings: Settings) -> int:
    """Return the ops request limit.

    Args:
        settings: Active application settings.

    Returns:
        Ops requests allowed per minute.
    """
    return settings.ops_rate_limit_per_minute


def _get_admin_limit(settings: Settings) -> int:
    """Return the admin request limit.

    Args:
        settings: Active application settings.

    Returns:
        Admin requests allowed per minute.
    """
    return settings.admin_rate_limit_per_minute


ENDPOINT_RATE_LIMIT_RULES: tuple[EndpointRateLimitRule, ...] = (
    EndpointRateLimitRule(
        name="public-search",
        path_prefixes=("/api/v2/search",),
        requests_per_minute=_get_public_search_limit,
        methods=frozenset({"GET"}),
    ),
    EndpointRateLimitRule(
        name="auth",
        path_prefixes=("/api/v2/auth",),
        requests_per_minute=_get_auth_limit,
    ),
    EndpointRateLimitRule(
        name="ops",
        path_prefixes=("/api/v2/ops",),
        requests_per_minute=_get_ops_limit,
    ),
    EndpointRateLimitRule(
        name="admin",
        path_prefixes=("/api/v2/admin",),
        requests_per_minute=_get_admin_limit,
    ),
)

endpoint_rate_limiter = InMemoryEndpointRateLimiter()


def _match_endpoint_rate_limit_rule(
    *,
    request: Request,
) -> EndpointRateLimitRule | None:
    """Find the first endpoint rate-limit rule matching a request.

    Args:
        request: Incoming HTTP request.

    Returns:
        Matching endpoint rule, if one applies.
    """
    for rule in ENDPOINT_RATE_LIMIT_RULES:
        if rule.matches(request=request):
            return rule
    return None

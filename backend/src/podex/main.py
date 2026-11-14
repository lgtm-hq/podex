"""FastAPI application entry point."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from podex.api.v2.auth_rejection import (
    RejectedSignInCodeError,
    render_rejected_sign_in_code,
)
from podex.api.v2.errors import install_exception_handlers
from podex.api.v2.router import api_v2_router
from podex.config import Settings, get_settings
from podex.logging_config import configure_logging
from podex.metrics import MetricsMiddleware, build_metrics, get_prometheus_metrics
from podex.middleware import RateLimitMiddleware, RequestContextMiddleware
from podex.observability import init_sentry
from podex.services.cache import TTLCache
from podex.services.limiter_factory import build_rate_limiter


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and configure the Podex FastAPI application."""
    resolved = settings or get_settings()
    configure_logging()
    init_sentry(resolved)
    application = FastAPI(title=resolved.app_name, debug=resolved.debug)
    # Stash the resolved settings on ``application.state`` so route dependencies see
    # the same instance ``create_app`` used to configure middleware — tests
    # (and callers passing an explicit ``settings=``) rely on this to override
    # e.g. ``settings.stats_cache.ttl_seconds`` without touching global state.
    application.state.settings = resolved
    # Process-wide read-through cache for aggregate/stats endpoints. Shared
    # across requests (and workers within this process) so the aggregation
    # queries only run once per TTL window.
    application.state.cache = TTLCache()
    # Fresh registry per application instance so repeated create_app calls (tests)
    # never trip duplicate-registration errors or share samples.
    metrics = build_metrics()
    application.state.metrics = metrics

    install_exception_handlers(application)
    # A rejected sign-in code renders as the generic 502 but stays a
    # dedicated type, so it is never reported as a server error.
    application.add_exception_handler(
        RejectedSignInCodeError,
        render_rejected_sign_in_code,
    )

    # Middleware is applied in reverse registration order (last added runs
    # outermost). Register the rate limiter first so CORS wraps it and still
    # annotates 429 responses.
    if resolved.rate_limit.enabled:
        limiter = build_rate_limiter(resolved)
        application.state.rate_limiter = limiter
        application.add_middleware(
            RateLimitMiddleware,
            limiter=limiter,
            exempt_paths=tuple(resolved.rate_limit.exempt_paths),
        )
    # Registered after the limiter so it observes every request that reaches
    # the application — including rate-limited 429s (which are labelled
    # ``unmatched`` because they short-circuit before routing).
    application.add_middleware(MetricsMiddleware, metrics=metrics)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        # Non-safelisted response headers are invisible to cross-origin
        # browser code unless exposed explicitly.
        expose_headers=[
            "X-Request-ID",
            "X-RateLimit-Limit",
            "X-RateLimit-Remaining",
            "X-RateLimit-Reset",
            "Retry-After",
        ],
    )
    # Outermost, so CORS preflight responses are logged too. The container
    # runs Uvicorn without its access log (which prints query strings,
    # including OAuth codes); this path-only log is the request record.
    application.add_middleware(RequestContextMiddleware)
    application.include_router(api_v2_router, prefix=resolved.api_v2_prefix)

    def health() -> dict[str, str]:
        """Liveness probe."""
        return {"status": "ok"}

    application.add_api_route("/health", health, methods=["GET"])
    # Internal-only Prometheus exposition (X-Ops-Key gated). Deliberately on
    # the application root — not the public read-only /api/v2 surface — and excluded
    # from the OpenAPI schema so the published API contract is unchanged.
    application.add_api_route(
        "/metrics",
        get_prometheus_metrics,
        methods=["GET"],
        include_in_schema=False,
    )

    return application


app = create_app()

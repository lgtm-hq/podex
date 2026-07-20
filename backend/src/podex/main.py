"""FastAPI application entry point."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
    app = FastAPI(title=resolved.app_name, debug=resolved.debug)
    # Stash the resolved settings on ``app.state`` so route dependencies see
    # the same instance ``create_app`` used to configure middleware — tests
    # (and callers passing an explicit ``settings=``) rely on this to override
    # e.g. ``stats_cache_ttl_seconds`` without touching global state.
    app.state.settings = resolved
    # Process-wide read-through cache for aggregate/stats endpoints. Shared
    # across requests (and workers within this process) so the aggregation
    # queries only run once per TTL window.
    app.state.cache = TTLCache()
    # Fresh registry per app instance so repeated create_app calls (tests)
    # never trip duplicate-registration errors or share samples.
    metrics = build_metrics()
    app.state.metrics = metrics

    install_exception_handlers(app)

    # Middleware is applied in reverse registration order (last added runs
    # outermost). Register the rate limiter and request-context logger first so
    # CORS ends up outermost and still annotates 429 responses.
    if resolved.rate_limit_enabled:
        limiter = build_rate_limiter(resolved)
        app.state.rate_limiter = limiter
        app.add_middleware(
            RateLimitMiddleware,
            limiter=limiter,
            exempt_paths=tuple(resolved.rate_limit_exempt_paths),
        )
    app.add_middleware(RequestContextMiddleware)
    # Registered after the limiter/context middleware so it observes every
    # request that reaches the app — including rate-limited 429s (which are
    # labelled ``unmatched`` because they short-circuit before routing).
    app.add_middleware(MetricsMiddleware, metrics=metrics)
    app.add_middleware(
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
    app.include_router(api_v2_router, prefix=resolved.api_v2_prefix)

    def health() -> dict[str, str]:
        """Liveness probe."""
        return {"status": "ok"}

    app.add_api_route("/health", health, methods=["GET"])
    # Internal-only Prometheus exposition (X-Ops-Key gated). Deliberately on
    # the app root — not the public read-only /api/v2 surface — and excluded
    # from the OpenAPI schema so the published API contract is unchanged.
    app.add_api_route(
        "/metrics",
        get_prometheus_metrics,
        methods=["GET"],
        include_in_schema=False,
    )

    return app


app = create_app()

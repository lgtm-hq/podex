"""FastAPI application entry point."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from podex.api.v2.router import api_v2_router
from podex.config import Settings, get_settings
from podex.logging_config import configure_logging
from podex.middleware import RateLimitMiddleware, RequestContextMiddleware
from podex.services.limiter import SlidingWindowRateLimiter


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and configure the Podex FastAPI application."""
    resolved = settings or get_settings()
    configure_logging()
    app = FastAPI(title=resolved.app_name, debug=resolved.debug)

    # Middleware is applied in reverse registration order (last added runs
    # outermost). Register the rate limiter and request-context logger first so
    # CORS ends up outermost and still annotates 429 responses.
    if resolved.rate_limit_enabled:
        limiter = SlidingWindowRateLimiter(
            max_requests=resolved.rate_limit_max_requests,
            window_seconds=resolved.rate_limit_window_seconds,
        )
        app.state.rate_limiter = limiter
        app.add_middleware(
            RateLimitMiddleware,
            limiter=limiter,
            exempt_paths=tuple(resolved.rate_limit_exempt_paths),
        )
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_v2_router, prefix=resolved.api_v2_prefix)

    def health() -> dict[str, str]:
        """Liveness probe."""
        return {"status": "ok"}

    app.add_api_route("/health", health, methods=["GET"])

    return app


app = create_app()

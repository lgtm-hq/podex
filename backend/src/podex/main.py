"""FastAPI application entry point."""

from collections.abc import Awaitable, Callable
from typing import cast

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy import text

from podex.api.router import api_router
from podex.api.v2.router import api_v2_router
from podex.config import get_settings
from podex.database import engine
from podex.logging_config import bind_request_context, configure_logging, get_logger

# Configure structured logging on startup
configure_logging()
logger = get_logger(__name__)

settings = get_settings()

# Rate limiter
limiter = Limiter(key_func=get_remote_address)

app = FastAPI(
    title=settings.app_name,
    description="Podcast Media Index - Track media mentioned in podcasts",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# Add rate limiter to app state
app.state.limiter = limiter
app.add_exception_handler(
    RateLimitExceeded,
    cast(Callable[[Request, Exception], Response], _rate_limit_exceeded_handler),
)


# Request logging middleware
@app.middleware("http")
async def logging_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Bind request context and log requests."""
    # Skip logging for CORS preflight requests
    if request.method == "OPTIONS":
        return await call_next(request)

    await bind_request_context(request)
    logger.info("request_started")

    response = await call_next(request)

    logger.info("request_completed", status_code=response.status_code)
    return response


# API Key authentication middleware
@app.middleware("http")
async def api_key_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Check API key for protected endpoints."""
    # Skip auth for CORS preflight requests
    if request.method == "OPTIONS":
        return await call_next(request)

    # Skip auth for health check and docs
    if request.url.path in ["/health", "/docs", "/redoc", "/openapi.json"]:
        return await call_next(request)

    # Skip if API key auth is disabled
    if not settings.api_key_enabled:
        return await call_next(request)

    # Check API key
    api_key = request.headers.get("X-API-Key")
    if api_key != settings.api_key:
        logger.warning("auth_failed", reason="invalid_api_key")
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid or missing API key"},
        )

    return await call_next(request)


# CORS configuration - allow all origins in debug mode
cors_origins = settings.cors_origins
if settings.debug:
    # nosemgrep: python.fastapi.security.wildcard-cors.wildcard-cors
    cors_origins = ["*"]  # Safe: only in debug mode for local development

app.add_middleware(
    CORSMiddleware,
    # nosemgrep: python.fastapi.security.wildcard-cors.wildcard-cors
    allow_origins=cors_origins,
    allow_credentials=not settings.debug,  # Can't use credentials with wildcard origins
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.api_v1_prefix)
app.include_router(api_v2_router, prefix=settings.api_v2_prefix)


@app.get("/health")
def health_check() -> dict[str, str | bool]:
    """Health check endpoint with database status."""
    db_connected = False
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            db_connected = True
    except Exception as e:
        logger.warning("health_check_db_error", error=str(e))

    return {
        "status": "healthy" if db_connected else "degraded",
        "service": settings.app_name,
        "version": "0.1.0",
        "db_connected": db_connected,
    }

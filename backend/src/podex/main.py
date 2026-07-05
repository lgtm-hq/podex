"""FastAPI application entry point."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from podex.api.v2.router import api_v2_router
from podex.config import Settings, get_settings
from podex.logging_config import configure_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and configure the Podex FastAPI application."""
    resolved = settings or get_settings()
    configure_logging()
    app = FastAPI(title=resolved.app_name, debug=resolved.debug)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_v2_router, prefix=resolved.api_v2_prefix)

    @app.get("/health")
    def health() -> dict[str, str]:
        """Liveness probe."""
        return {"status": "ok"}

    return app


app = create_app()

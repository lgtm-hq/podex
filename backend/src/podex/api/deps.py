"""Shared FastAPI dependencies for the API layer."""

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from podex.config import Settings
from podex.database import get_db
from podex.services.cache import Cache

DbSession = Annotated[Session, Depends(get_db)]
"""A request-scoped database session injected into route handlers."""


def get_app_cache(request: Request) -> Cache:
    """Return the process-wide cache attached to the FastAPI application.

    The cache is created once in :func:`podex.main.create_app` and stashed on
    ``app.state``; routes retrieve it through this dependency so tests can
    override it via ``app.dependency_overrides`` without touching state.
    """
    cache: Cache = request.app.state.cache
    return cache


AppCache = Annotated[Cache, Depends(get_app_cache)]
"""The process-wide cache backend injected into route handlers."""


def get_app_settings(request: Request) -> Settings:
    """Return the :class:`Settings` instance configured on this app.

    :func:`podex.main.create_app` stores the ``settings=`` argument (or the
    cached global fallback) on ``app.state.settings`` so route dependencies
    resolve to the same instance used to build the middleware stack. Reading
    it through the request keeps overrides passed to
    ``create_app(settings=...)`` — most commonly in tests — in effect for
    handlers instead of silently falling back to the global settings.
    """
    settings: Settings = request.app.state.settings
    return settings


AppSettings = Annotated[Settings, Depends(get_app_settings)]
"""Resolved application settings injected into route handlers."""

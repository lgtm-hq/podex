"""Shared FastAPI dependencies for the API layer.

Route handlers pull their collaborators from this module rather than
constructing them ad-hoc, so wiring choices (session lifetime, settings
resolution, pagination parsing) stay in one place. Every declared alias
returns a fully-typed :class:`typing.Annotated` value so route signatures
remain concise while ``mypy --strict`` can still infer the injected type.
"""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from podex.api.v2.schemas import PaginationParams, pagination_params
from podex.config import Settings, get_settings
from podex.database import get_db
from podex.services.cache import Cache

DbSession = Annotated[Session, Depends(get_db)]
"""A request-scoped database session injected into route handlers."""


<<<<<<< HEAD
def get_app_settings(request: Request) -> Settings:
    """Return the :class:`Settings` stored on ``app.state`` by ``create_app``.

    Falling back to :func:`podex.config.get_settings` keeps ad-hoc test apps
    working when they construct a FastAPI instance without going through
=======
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
    """Return the :class:`Settings` stored on ``app.state`` by ``create_app``.

    :func:`podex.main.create_app` stores the ``settings=`` argument (or the
    cached global fallback) on ``app.state.settings`` so route dependencies
    resolve to the same instance used to build the middleware stack. Falling
    back to :func:`podex.config.get_settings` keeps ad-hoc test apps working
    when they construct a FastAPI instance without going through
>>>>>>> origin/main
    ``create_app``.

    Args:
        request: The active request, used to reach ``request.app.state``.

    Returns:
        The application's resolved :class:`Settings` instance.
    """
    state_settings = getattr(request.app.state, "settings", None)
    if isinstance(state_settings, Settings):
        return state_settings
    return cast("Settings", get_settings())


<<<<<<< HEAD
SettingsDep = Annotated[Settings, Depends(get_app_settings)]
"""The application settings resolved once per request from ``app.state``."""
=======
AppSettings = Annotated[Settings, Depends(get_app_settings)]
"""Resolved application settings injected into route handlers."""

SettingsDep = AppSettings
"""Backwards-compatible alias for :data:`AppSettings`."""
>>>>>>> origin/main

Pagination = Annotated[PaginationParams, Depends(pagination_params)]
"""Shared ``limit``/``offset`` query parameter parsing for list endpoints."""

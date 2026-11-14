"""Shared FastAPI dependencies for the API layer.

Route handlers pull their collaborators from this module rather than
constructing them ad-hoc, so wiring choices (session lifetime, settings
resolution, pagination parsing) stay in one place. Every declared alias
returns a fully-typed :class:`typing.Annotated` value so route signatures
remain concise while ``mypy --strict`` can still infer the injected type.
"""

from __future__ import annotations

import secrets
from typing import Annotated, cast

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from podex.api.v2.schemas import PaginationParams, pagination_params
from podex.config import Settings, get_settings
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
    """Return the :class:`Settings` stored on ``app.state`` by ``create_app``.

    :func:`podex.main.create_app` stores the ``settings=`` argument (or the
    cached global fallback) on ``app.state.settings`` so route dependencies
    resolve to the same instance used to build the middleware stack. Falling
    back to :func:`podex.config.get_settings` keeps ad-hoc test apps working
    when they construct a FastAPI instance without going through
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


AppSettings = Annotated[Settings, Depends(get_app_settings)]
"""Resolved application settings injected into route handlers."""


def require_ops_access(
    *,
    request: Request,
    settings: Settings,
    unconfigured_detail: str,
) -> None:
    """Verify the shared ops credential without ordinary secret equality.

    Args:
        request: The request carrying the ``X-Ops-Key`` header.
        settings: Runtime settings providing the expected key.
        unconfigured_detail: The surface-specific message for disabled access.

    Raises:
        HTTPException: 503 when unconfigured, 401 on a missing or invalid key.
    """
    if not settings.ops_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=unconfigured_detail,
        )
    supplied_key = request.headers.get("X-Ops-Key", "").encode("utf-8")
    expected_key = settings.ops_api_key.encode("utf-8")
    if not secrets.compare_digest(supplied_key, expected_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ops authentication required",
        )


SettingsDep = AppSettings
"""Backwards-compatible alias for :data:`AppSettings`."""

Pagination = Annotated[PaginationParams, Depends(pagination_params)]
"""Shared ``limit``/``offset`` query parameter parsing for list endpoints."""

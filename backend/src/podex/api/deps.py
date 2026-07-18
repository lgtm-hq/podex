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

DbSession = Annotated[Session, Depends(get_db)]
"""A request-scoped database session injected into route handlers."""


def get_app_settings(request: Request) -> Settings:
    """Return the :class:`Settings` stored on ``app.state`` by ``create_app``.

    Falling back to :func:`podex.config.get_settings` keeps ad-hoc test apps
    working when they construct a FastAPI instance without going through
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


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
"""The application settings resolved once per request from ``app.state``."""

Pagination = Annotated[PaginationParams, Depends(pagination_params)]
"""Shared ``limit``/``offset`` query parameter parsing for list endpoints."""

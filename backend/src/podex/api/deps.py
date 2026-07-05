"""Shared FastAPI dependencies for the API layer."""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from podex.database import get_db

DbSession = Annotated[Session, Depends(get_db)]
"""A request-scoped database session injected into route handlers."""

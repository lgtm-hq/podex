"""SQLAlchemy base model."""

from sqlalchemy.orm import DeclarativeBase


# SQLAlchemy plugin not available in linter Docker environment
class Base(DeclarativeBase):  # type: ignore[misc]
    """Base class for all SQLAlchemy models."""

    pass

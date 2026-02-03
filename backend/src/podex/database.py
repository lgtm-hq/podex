"""Database connection and session management."""

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from podex.config import get_settings

settings = get_settings()


def _create_engine() -> "Engine":
    """Create database engine with appropriate settings."""
    from typing import Any

    is_sqlite = "sqlite" in settings.database_url

    # Base args - typed as Any to accommodate different value types
    engine_args: dict[str, Any] = {
        "echo": settings.debug,
    }

    if is_sqlite:
        # SQLite-specific settings
        engine_args["connect_args"] = {"check_same_thread": False}
    else:
        # PostgreSQL connection pooling settings for production
        engine_args["pool_size"] = 5
        engine_args["max_overflow"] = 10
        engine_args["pool_timeout"] = 30
        engine_args["pool_pre_ping"] = True  # Check connections are alive

    return create_engine(settings.database_url, **engine_args)


engine = _create_engine()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    """Dependency for getting database sessions."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """Context manager for database sessions."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

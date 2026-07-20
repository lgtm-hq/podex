"""Database engine and session management."""

from collections.abc import Iterator
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from podex.config import Settings, get_settings


def build_engine_kwargs(settings: Settings) -> dict[str, Any]:
    """Return ``create_engine`` keyword arguments for the configured database.

    SQLite keeps the historical behavior (``check_same_thread=False`` so the
    FastAPI thread pool can share connections, stock pooling). Server-backed
    databases (e.g. Neon Postgres behind Railway) get ``pool_pre_ping`` plus
    settings-driven pool sizing so suspended or rotated backends are detected
    and idle connections are recycled.

    Args:
        settings: Application settings providing ``settings.database`` URL
            and pool tuning values.

    Returns:
        Keyword arguments to splat into :func:`sqlalchemy.create_engine`.
    """
    if settings.database.url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False}}
    return {
        "pool_pre_ping": True,
        "pool_size": settings.database.pool_size,
        "max_overflow": settings.database.max_overflow,
        "pool_recycle": settings.database.pool_recycle_seconds,
    }


def enable_sqlite_foreign_keys(target_engine: Engine) -> None:
    """Enforce foreign keys on SQLite, which disables them by default."""
    if target_engine.dialect.name != "sqlite":
        return

    def _set_pragma(dbapi_connection: Any, _record: Any) -> None:
        """Enable the foreign_keys pragma on each new SQLite connection.

        Args:
            dbapi_connection: The raw DBAPI connection being checked out.
            _record: The connection pool record (unused).
        """
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    event.listen(target_engine, "connect", _set_pragma)


_settings = get_settings()

engine = create_engine(_settings.database.url, **build_engine_kwargs(_settings))
enable_sqlite_foreign_keys(engine)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Iterator[Session]:
    """Yield a request-scoped database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

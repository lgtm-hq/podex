"""Database engine and session management."""

from collections.abc import Iterator
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from podex.config import get_settings


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

_connect_args = (
    {"check_same_thread": False} if _settings.database_url.startswith("sqlite") else {}
)
engine = create_engine(_settings.database_url, connect_args=_connect_args)
enable_sqlite_foreign_keys(engine)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Iterator[Session]:
    """Yield a request-scoped database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

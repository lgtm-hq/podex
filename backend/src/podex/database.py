"""Database engine and session management."""

from collections.abc import Iterator
from typing import Any, TypeVar

from sqlalchemy import Engine, Select, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from podex.config import Settings, get_settings
from podex.unusable_settings import checked_database_url


def build_engine_kwargs(settings: Settings) -> dict[str, Any]:
    """Return ``create_engine`` keyword arguments for the configured database.

    SQLite keeps the historical behavior (``check_same_thread=False`` so the
    FastAPI thread pool can share connections, stock pooling). Server-backed
    databases (e.g. Neon Postgres behind Railway) get ``pool_pre_ping`` plus
    settings-driven pool sizing so suspended or rotated backends are detected
    and idle connections are recycled.

    Every engine hides statement parameters from its error messages and logs
    (``hide_parameters``): bound values can be credentials or personal data,
    and database errors are logged and reported.

    Args:
        settings: Application settings providing ``settings.database`` URL
            and pool tuning values.

    Returns:
        Keyword arguments to splat into :func:`sqlalchemy.create_engine`.
    """
    if settings.database.url.get_secret_value().startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False}, "hide_parameters": True}
    return {
        "hide_parameters": True,
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


def build_engine(settings: Settings) -> Engine:
    """Build the application engine from the configured database URL.

    The URL is parsed, and its dialect and driver loaded, on their own first:
    an unusable URL fails there, reported by setting and error class only
    (:class:`~podex.unusable_settings.UnusableSettingError`). The engine is
    then built from the parsed URL, so a fault while building it surfaces
    unchanged.

    Args:
        settings: Application settings providing the URL and pool tuning.

    Returns:
        Engine: The engine, with SQLite foreign keys enforced.
    """
    url = checked_database_url(
        settings.database.url.get_secret_value(),
        source="PODEX_DATABASE__URL",
        load_driver=True,
    )
    built: Engine = create_engine(url, **build_engine_kwargs(settings))
    enable_sqlite_foreign_keys(built)
    return built


engine = build_engine(get_settings())
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Iterator[Session]:
    """Yield a request-scoped database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def delete_if_present(*, db: Session, row: object | None) -> bool:
    """Delete an optional ORM row and flush, returning whether it existed."""
    if row is None:
        return False
    db.delete(row)
    db.flush()
    return True


RowT = TypeVar("RowT")


def fetch_page(
    *, db: Session, statement: Select[RowT], limit: int | None, offset: int
) -> list[RowT]:
    """Execute a scalar-row query with consistent optional pagination."""
    statement = statement.offset(offset)
    if limit is not None:
        statement = statement.limit(limit)
    return list(db.scalars(statement).all())

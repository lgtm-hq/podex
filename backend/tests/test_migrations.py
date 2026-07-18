"""Migration replay and schema-drift guard."""

from pathlib import Path
from typing import cast

from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from assertpy import assert_that
from sqlalchemy import Engine, create_engine, inspect, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.engine.reflection import Inspector
from sqlalchemy.sql.elements import ClauseElement

from alembic import command  # type: ignore[attr-defined]
from podex.models import Base, Episode, Media, MediaType, Mention

_ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def _upgraded_engine(db_url: str) -> Engine:
    """Run Alembic migrations to head and return the resulting engine.

    Args:
        db_url: SQLAlchemy-compatible database URL for the target database.

    Returns:
        Engine connected to the fully migrated database.
    """
    config = Config(str(_ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", db_url)
    command.upgrade(config, "head")
    return create_engine(db_url)


def _index_column_names(
    inspector: Inspector,
    table: str,
    index_name: str,
) -> tuple[str, ...]:
    """Return the ordered column names for a table index."""
    for index in inspector.get_indexes(table):
        if index["name"] == index_name:
            return tuple(cast(list[str], index["column_names"]))
    msg = f"index {index_name!r} not found on table {table!r}"
    raise AssertionError(msg)


def _explain_query_plan(connection: Connection, statement: ClauseElement) -> str:
    """Return SQLite EXPLAIN QUERY PLAN output for a SQLAlchemy statement."""
    compiled = statement.compile(
        dialect=connection.dialect,
        compile_kwargs={"literal_binds": True},
    )
    rows = connection.execute(text(f"EXPLAIN QUERY PLAN {compiled}")).fetchall()
    return " ".join(str(row) for row in rows)


def test_migrations_create_model_tables(tmp_path: Path) -> None:
    """``alembic upgrade head`` creates every table defined by the models."""
    engine = _upgraded_engine(f"sqlite:///{tmp_path / 'replay.db'}")

    tables = set(inspect(engine).get_table_names())

    for table in Base.metadata.tables:
        assert_that(tables).contains(table)


def test_no_schema_drift(tmp_path: Path) -> None:
    """The migrations match the models with no autogenerate drift."""
    engine = _upgraded_engine(f"sqlite:///{tmp_path / 'drift.db'}")

    with engine.connect() as connection:
        migration_context = MigrationContext.configure(connection)
        diffs = compare_metadata(migration_context, Base.metadata)

    assert_that(diffs).is_empty()


def test_hot_query_indexes_exist(tmp_path: Path) -> None:
    """Revision 0002 adds indexes aligned with common list endpoints."""
    engine = _upgraded_engine(f"sqlite:///{tmp_path / 'indexes.db'}")
    inspector = inspect(engine)

    assert_that(_index_column_names(inspector, "episodes", "ix_episodes_published_at")).is_equal_to(
        ("published_at",),
    )
    assert_that(_index_column_names(inspector, "media", "ix_media_type_title")).is_equal_to(
        ("type", "title"),
    )
    assert_that(
        _index_column_names(inspector, "mentions", "ix_mentions_media_id_episode_id"),
    ).is_equal_to(("media_id", "episode_id"))
    assert_that(
        _index_column_names(
            inspector,
            "mentions",
            "ix_mentions_episode_id_timestamp_seconds",
        ),
    ).is_equal_to(("episode_id", "timestamp_seconds"))

    with engine.connect() as connection:
        global_episodes_plan = _explain_query_plan(
            connection,
            select(Episode).order_by(Episode.published_at.desc()),
        )
        media_by_type_plan = _explain_query_plan(
            connection,
            select(Media)
            .where(Media.type == MediaType.BOOK)
            .order_by(Media.title),
        )
        episode_mentions_plan = _explain_query_plan(
            connection,
            select(Mention)
            .where(Mention.episode_id == 1)
            .order_by(Mention.timestamp_seconds),
        )
        media_mentions_plan = _explain_query_plan(
            connection,
            select(Mention).where(Mention.media_id == 1).order_by(Mention.episode_id),
        )

    assert_that(global_episodes_plan).contains("ix_episodes_published_at")
    assert_that(media_by_type_plan).contains("ix_media_type_title")
    assert_that(episode_mentions_plan).contains(
        "ix_mentions_episode_id_timestamp_seconds",
    )
    assert_that(media_mentions_plan).contains("ix_mentions_media_id_episode_id")

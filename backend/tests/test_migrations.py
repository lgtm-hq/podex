"""Migration replay and schema-drift guard."""

from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from assertpy import assert_that
from sqlalchemy import Engine, create_engine, inspect

from alembic import command
from podex.models import Base

_ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def _upgraded_engine(db_url: str) -> Engine:
    config = Config(str(_ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", db_url)
    command.upgrade(config, "head")
    return create_engine(db_url)


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

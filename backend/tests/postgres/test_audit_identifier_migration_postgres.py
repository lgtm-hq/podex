"""Revision 0024 widens and narrows the audit identifier on real PostgreSQL.

PostgreSQL refuses to narrow a ``varchar`` column that holds longer values, so
the downgrade must cut identifiers over 120 characters first. SQLite ignores
column lengths and cannot show either. This test runs in the PostgreSQL CI
lane and is skipped elsewhere.
"""

import pytest
from alembic.command import downgrade, upgrade
from alembic.config import Config
from assertpy import assert_that
from sqlalchemy import TextClause, text
from sqlalchemy.exc import DataError

from tests.audit_identifier_cases import (
    DOWNGRADED_ENTRIES,
    NARROW_ENTRIES,
    NARROW_LENGTH,
    WIDE_ENTRIES,
    WIDE_LENGTH,
    multibyte,
)
from tests.postgres.conftest import ALEMBIC_INI, PostgresDatabase

pytestmark = pytest.mark.postgres

_INSERT_SQL = text(
    "INSERT INTO audit_logs (id, action, resource_type, resource_identifier,"
    " summary, created_at) VALUES (:id, 'update_podcast', 'podcast',"
    " :identifier, 'Updated podcast', now())",
)
_IDENTIFIERS_SQL = text("SELECT id, resource_identifier FROM audit_logs ORDER BY id")
_LENGTH_SQL = text(
    "SELECT character_maximum_length FROM information_schema.columns"
    " WHERE table_name = 'audit_logs' AND column_name = 'resource_identifier'",
)


def _alembic_config(postgres: PostgresDatabase) -> Config:
    """Return an Alembic configuration bound to the test's database."""
    config = Config(str(ALEMBIC_INI))
    rendered = postgres.url.render_as_string(hide_password=False)
    # ConfigParser interpolation treats ``%`` specially; escape it.
    config.set_main_option("sqlalchemy.url", rendered.replace("%", "%%"))
    return config


def _rows(
    postgres: PostgresDatabase, statement: TextClause
) -> list[tuple[object, ...]]:
    """Return every row of ``statement`` as tuples."""
    with postgres.engine.connect() as connection:
        return [tuple(row) for row in connection.execute(statement)]


def _insert(postgres: PostgresDatabase, identifiers: dict[int, str | None]) -> None:
    """Insert one audit entry per id with the given resource identifier."""
    with postgres.engine.begin() as connection:
        for entry_id, identifier in identifiers.items():
            connection.execute(_INSERT_SQL, {"id": entry_id, "identifier": identifier})


def test_revision_0024_round_trips_long_identifiers_on_postgres(
    postgres: PostgresDatabase,
) -> None:
    """Head stores 255 characters; downgrade cuts them to 120 and succeeds."""
    _insert(postgres, {**NARROW_ENTRIES, **WIDE_ENTRIES})
    assert_that(_rows(postgres, _LENGTH_SQL)).is_equal_to([(WIDE_LENGTH,)])

    config = _alembic_config(postgres)
    downgrade(config, "0023")

    assert_that(_rows(postgres, _LENGTH_SQL)).is_equal_to([(NARROW_LENGTH,)])
    assert_that(_rows(postgres, _IDENTIFIERS_SQL)).is_equal_to(DOWNGRADED_ENTRIES)
    # Control: the narrowed column rejects a longer identifier.
    with pytest.raises(DataError):
        _insert(postgres, {6: "y" * (NARROW_LENGTH + 1)})

    upgrade(config, "head")

    assert_that(_rows(postgres, _LENGTH_SQL)).is_equal_to([(WIDE_LENGTH,)])
    _insert(postgres, {6: multibyte(WIDE_LENGTH)})
    assert_that(_rows(postgres, _IDENTIFIERS_SQL)[-1]).is_equal_to(
        (6, multibyte(WIDE_LENGTH)),
    )

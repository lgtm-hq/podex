"""The audit log resource identifier holds every slug the ops routes accept.

SQLite ignores column lengths, so only the PostgreSQL lane shows a long slug
failing the audit insert. Here the declared lengths are compared, and
revision 0024 is replayed up and down on SQLite.
"""

from pathlib import Path
from typing import Any

import pytest
from alembic.command import downgrade, upgrade
from alembic.config import Config
from assertpy import assert_that
from pydantic import BaseModel
from sqlalchemy import String, create_engine, inspect, text

from podex.models import AuditLog, Podcast
from podex.schemas.ops import OpsPodcastCreateRequest, OpsPodcastUpdateRequest
from tests.audit_identifier_cases import (
    DOWNGRADED_ENTRIES,
    NARROW_ENTRIES,
    NARROW_LENGTH,
    WIDE_ENTRIES,
    WIDE_LENGTH,
)

_ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def _column_length(model: Any, column: str) -> int:
    """Return the declared length of a model's string column."""
    return int(model.__table__.columns[column].type.length)


def _slug_max_length(schema: type[BaseModel]) -> int:
    """Return the ``maxLength`` a request schema declares for ``slug``."""
    slug: dict[str, Any] = schema.model_json_schema()["properties"]["slug"]
    options: list[dict[str, Any]] = slug.get("anyOf", [slug])
    lengths = [int(option["maxLength"]) for option in options if "maxLength" in option]
    assert_that(lengths).is_length(1)
    return lengths[0]


@pytest.mark.parametrize(
    "schema",
    [OpsPodcastCreateRequest, OpsPodcastUpdateRequest],
    ids=["create", "update"],
)
def test_audit_identifier_holds_the_longest_accepted_slug(
    schema: type[BaseModel],
) -> None:
    """The identifier column is at least as long as any slug a route accepts."""
    identifier_length = _column_length(AuditLog, "resource_identifier")

    assert_that(identifier_length).is_greater_than_or_equal_to(
        _column_length(Podcast, "slug"),
    )
    assert_that(identifier_length).is_greater_than_or_equal_to(
        _slug_max_length(schema),
    )


class _AuditDatabase:
    """A SQLite file database migrated with Alembic for one test."""

    def __init__(self, db_url: str) -> None:
        self.config = Config(str(_ALEMBIC_INI))
        self.config.set_main_option("sqlalchemy.url", db_url)
        self.engine = create_engine(db_url)

    def identifier_length(self) -> int:
        """Return the reflected length of ``audit_logs.resource_identifier``."""
        columns = inspect(self.engine).get_columns("audit_logs")
        by_name = {column["name"]: column["type"] for column in columns}
        column_type = by_name["resource_identifier"]
        if not isinstance(column_type, String) or column_type.length is None:
            msg = f"expected a bounded string column, got {column_type!r}"
            raise AssertionError(msg)
        return int(column_type.length)

    def identifiers(self) -> list[tuple[int, str | None]]:
        """Return every audit entry's id and resource identifier."""
        with self.engine.connect() as connection:
            rows = connection.execute(
                text("SELECT id, resource_identifier FROM audit_logs ORDER BY id"),
            ).all()
        return [(row[0], row[1]) for row in rows]

    def insert(self, identifiers: dict[int, str | None]) -> None:
        """Insert one audit entry per id with the given resource identifier."""
        with self.engine.begin() as connection:
            for entry_id, identifier in identifiers.items():
                connection.execute(
                    text(
                        "INSERT INTO audit_logs (id, action, resource_type,"
                        " resource_identifier, summary, created_at) VALUES"
                        " (:id, 'update_podcast', 'podcast', :identifier,"
                        " 'Updated podcast', '2026-11-17')",
                    ),
                    {"id": entry_id, "identifier": identifier},
                )


def test_revision_0024_widens_and_narrows_the_identifier(tmp_path: Path) -> None:
    """Upgrade keeps every identifier; downgrade cuts only those over 120."""
    database = _AuditDatabase(f"sqlite:///{tmp_path / 'audit.db'}")
    upgrade(database.config, "0023")
    database.insert(NARROW_ENTRIES)
    assert_that(database.identifier_length()).is_equal_to(NARROW_LENGTH)

    upgrade(database.config, "0024")
    database.insert(WIDE_ENTRIES)

    assert_that(database.identifier_length()).is_equal_to(WIDE_LENGTH)
    assert_that(database.identifiers()).is_equal_to(
        [*NARROW_ENTRIES.items(), *WIDE_ENTRIES.items()],
    )

    downgrade(database.config, "0023")

    assert_that(database.identifier_length()).is_equal_to(NARROW_LENGTH)
    assert_that(database.identifiers()).is_equal_to(DOWNGRADED_ENTRIES)

    upgrade(database.config, "head")

    assert_that(database.identifier_length()).is_equal_to(WIDE_LENGTH)
    database.engine.dispose()

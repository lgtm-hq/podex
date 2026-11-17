"""Find statements that PostgreSQL rejects because they compare JSON values.

PostgreSQL has no equality operator for ``json``, so a ``SELECT DISTINCT``
whose selected columns include a JSON column fails there with "could not
identify an equality operator for type json". SQLite accepts the same
statement, so the default test database cannot show the failure. These
helpers capture the statements a call runs, compile each ``SELECT`` in them
for the PostgreSQL dialect and name the JSON columns a ``DISTINCT`` compares.
"""

import re
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import JSON, create_engine, event
from sqlalchemy.orm import ORMExecuteState, Session
from sqlalchemy.sql import ClauseElement, ColumnElement
from sqlalchemy.sql.selectable import Select
from sqlalchemy.sql.visitors import iterate

_DISTINCT = re.compile(r"^SELECT DISTINCT\b")
# Building an engine does not connect; only its dialect is used.
_POSTGRESQL = create_engine("postgresql+psycopg://").dialect


@contextmanager
def captured_statements(session: Session) -> Iterator[list[ClauseElement]]:
    """Record every ORM statement ``session`` executes inside the block.

    Yields:
        The list the statements are appended to, in execution order.
    """
    statements: list[ClauseElement] = []

    def record(state: ORMExecuteState) -> None:
        if isinstance(state.statement, ClauseElement):
            statements.append(state.statement)

    event.listen(session, "do_orm_execute", record)
    try:
        yield statements
    finally:
        event.remove(session, "do_orm_execute", record)


def _json_names(columns: Iterable[ColumnElement[Any]]) -> list[str]:
    """Return the names of the JSON-typed columns in ``columns``."""
    return [str(column) for column in columns if isinstance(column.type, JSON)]


def json_distinct_columns(statement: ClauseElement) -> list[str]:
    """Name each JSON column a ``SELECT DISTINCT`` compares on PostgreSQL.

    Every ``SELECT`` nested in ``statement`` is compiled for the PostgreSQL
    dialect; one that renders as ``SELECT DISTINCT`` compares all of its
    selected columns.

    Args:
        statement: The statement to inspect.

    Returns:
        The JSON columns compared, empty when PostgreSQL accepts the statement.
    """
    found: list[str] = []
    for element in iterate(statement):
        if not isinstance(element, Select):
            continue
        compiled = str(element.compile(dialect=_POSTGRESQL))
        if _DISTINCT.match(compiled):
            found.extend(_json_names(element.selected_columns))
    return found

"""Guarded tables are written through the ORM unit of work only.

The suppression guard sees ORM flushes. Core ``insert``/``update``/``delete``,
``Query.update``/``Query.delete``, raw SQL and the ``bulk_*`` session methods
skip it, so no module may write a guarded table that way. The scan reads the
source with ``ast``; a new bypass fails here until it is removed or added to
``_EXEMPT`` with a reason.
"""

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest
from assertpy import assert_that

from podex.models import GUARDED_MODELS

_BACKEND = Path(__file__).resolve().parent.parent
_SCANNED = ("src", "scripts", "alembic")
_EXEMPT = {
    "src/podex/services/takedown_requests.py": (
        "takedown execution clears and deletes suppressed rows in bulk; it only"
        " removes content"
    ),
    "alembic/versions": "migrations change schema and backfill suppression rows",
}
_GUARDED_NAMES = frozenset(model.__name__ for model in GUARDED_MODELS)
_GUARDED_TABLES = frozenset(model.__table__.name for model in GUARDED_MODELS)
_CORE_WRITES = frozenset({"insert", "update", "delete"})
_BULK_METHODS = frozenset(
    {"bulk_save_objects", "bulk_insert_mappings", "bulk_update_mappings"},
)
_SQL_VERBS = ("insert into ", "update ", "delete from ")


@dataclass(frozen=True, slots=True)
class _Finding:
    """One bypass write found in a module."""

    path: str
    line: int
    reason: str


def _guarded_target(node: ast.expr) -> str | None:
    """Return the guarded model a Core statement or query targets, if any."""
    if isinstance(node, ast.Name) and node.id in _GUARDED_NAMES:
        return node.id
    if (
        isinstance(node, ast.Attribute)
        and node.attr == "__table__"
        and isinstance(node.value, ast.Name)
        and node.value.id in _GUARDED_NAMES
    ):
        return node.value.id
    return None


def _called_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _queried_model(node: ast.expr) -> str | None:
    """Return the guarded model of a ``query(Model)...`` chain, if any."""
    current: ast.expr = node
    while isinstance(current, (ast.Call, ast.Attribute)):
        if isinstance(current, ast.Call):
            if _called_name(current) == "query" and current.args:
                return _guarded_target(current.args[0])
            current = current.func
        else:
            current = current.value
    return None


def _raw_sql_target(call: ast.Call) -> str | None:
    """Return a guarded table a raw SQL string literal writes, if any."""
    for argument in call.args:
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            sql = " ".join(argument.value.lower().split())
            for verb in _SQL_VERBS:
                for table in _GUARDED_TABLES:
                    if f"{verb}{table}" in sql:
                        written: str = table
                        return written
    return None


def find_bypass_writes(source: str, path: str) -> list[_Finding]:
    """Return every guarded-table write in ``source`` that skips the flush."""
    findings: list[_Finding] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        name = _called_name(node)
        reason: str | None = None
        if name in _BULK_METHODS:
            reason = f"session.{name}"
        elif name in _CORE_WRITES and node.args and _guarded_target(node.args[0]):
            reason = f"Core {name}({_guarded_target(node.args[0])})"
        elif (
            name in _CORE_WRITES
            and isinstance(node.func, ast.Attribute)
            and _guarded_target(node.func.value)
        ):
            reason = f"{_guarded_target(node.func.value)}.__table__.{name}()"
        elif (
            name in {"update", "delete"}
            and isinstance(node.func, ast.Attribute)
            and _queried_model(node.func.value)
        ):
            reason = f"Query({_queried_model(node.func.value)}).{name}()"
        elif name in {"text", "exec_driver_sql"} and _raw_sql_target(node):
            reason = f"raw SQL writing {_raw_sql_target(node)}"
        if reason is not None:
            findings.append(_Finding(path=path, line=node.lineno, reason=reason))
    return findings


def _is_exempt(relative: str) -> bool:
    return any(
        relative == exempt or relative.startswith(f"{exempt}/") for exempt in _EXEMPT
    )


def test_no_module_writes_a_guarded_table_around_the_flush() -> None:
    """Every guarded-table write in the backend goes through the ORM flush."""
    findings: list[_Finding] = []
    for directory in _SCANNED:
        for path in sorted((_BACKEND / directory).rglob("*.py")):
            relative = path.relative_to(_BACKEND).as_posix()
            if _is_exempt(relative):
                continue
            findings.extend(
                find_bypass_writes(path.read_text(encoding="utf-8"), relative),
            )

    assert_that(findings).is_empty()


def test_every_exemption_still_exists() -> None:
    """An exemption for a moved or deleted path is removed, not left behind."""
    for exempt in _EXEMPT:
        assert_that((_BACKEND / exempt).exists()).described_as(exempt).is_true()


_BYPASSES = {
    "core-insert": "db.execute(insert(EpisodeSummary).values(summary_text='x'))",
    "core-update": "db.execute(update(Mention).values(context='x'))",
    "table-insert": "conn.execute(SemanticChunk.__table__.insert(), rows)",
    "connection-insert": "db.connection().execute(insert(Transcript), rows)",
    "query-update": "db.query(GraphTriple).filter(x).update({'a': 1})",
    "query-delete": "db.query(MediaRelation).delete()",
    "bulk-save": "db.bulk_save_objects(rows)",
    "bulk-insert": "db.bulk_insert_mappings(EpisodeSummary, rows)",
    "bulk-update": "db.bulk_update_mappings(Mention, rows)",
    "raw-sql": "db.execute(text('INSERT INTO episode_summaries (a) VALUES (1)'))",
    "driver-sql": "conn.exec_driver_sql('update  transcripts set cleaned_text = 1')",
}


@pytest.mark.parametrize("snippet", _BYPASSES.values(), ids=_BYPASSES.keys())
def test_scan_detects_each_bypass_interface(snippet: str) -> None:
    """The scan recognises every interface that skips the flush guard."""
    assert_that(find_bypass_writes(snippet, "snippet.py")).is_length(1)


def test_scan_ignores_orm_writes_and_unguarded_tables() -> None:
    """ORM writes and bulk writes of other tables are not reported."""
    allowed = "\n".join(
        [
            "db.add(EpisodeSummary(summary_text='x'))",
            "db.query(AccountAlertEvent).filter(x).update({'a': 1})",
            "db.execute(insert(AuditLog).values(summary='x'))",
            "db.execute(text('UPDATE magic_link_tokens SET used = 1'))",
            "db.query(Mention).filter(x).all()",
        ],
    )

    assert_that(find_bypass_writes(allowed, "snippet.py")).is_empty()

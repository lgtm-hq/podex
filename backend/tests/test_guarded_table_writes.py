"""Guarded tables are written through the ORM unit of work only.

The suppression guard sees ORM flushes. Core ``insert``/``update``/``delete``
(including the dialect variants), ``Query.update``/``Query.delete``, raw SQL
and the ``bulk_*`` session methods skip it, so no module may write a guarded
table that way. The scan parses each module with ``ast`` and resolves its
imports, so aliases (``insert as sqlite_insert``, ``Mention as M``, ``import
sqlalchemy as sa``, ``from ..models import Mention``) are followed, and a
table named by string
(``table("mentions")``, ``metadata.tables["mentions"]``) counts as the model.

What it cannot see: a write built dynamically (a model or table chosen at
run time, SQL assembled from variables), a write through a helper defined in
another package, and code outside ``backend/src``, ``backend/scripts`` and
``backend/alembic``. A new bypass fails here until it is removed or added to
``_EXEMPT`` with a reason.
"""

import ast
from dataclasses import dataclass, field
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
_TABLE_FACTORIES = frozenset({"table", "Table"})
_SQL_VERBS = ("insert into ", "update ", "delete from ")


@dataclass(frozen=True, slots=True)
class _Finding:
    """One bypass write found in a module."""

    path: str
    line: int
    reason: str


@dataclass(slots=True)
class _Module:
    """Names a module imports, resolved to their dotted origin."""

    aliases: dict[str, str] = field(default_factory=dict)

    def qualified(self, node: ast.expr) -> str | None:
        """Return the dotted origin of a name or attribute chain."""
        if isinstance(node, ast.Name):
            return self.aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            base = self.qualified(node.value)
            return f"{base}.{node.attr}" if base else None
        return None


def _imports(tree: ast.Module) -> _Module:
    module = _Module()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module.aliases[alias.asname or alias.name.split(".")[0]] = (
                    alias.name if alias.asname else alias.name.split(".")[0]
                )
        elif isinstance(node, ast.ImportFrom):
            # A relative import in the scanned tree can only name a module of
            # the ``podex`` package, so it resolves under that prefix.
            package = ".".join(
                part
                for part in ("podex" if node.level else "", node.module or "")
                if part
            )
            for alias in node.names:
                module.aliases[alias.asname or alias.name] = f"{package}.{alias.name}"
    return module


def _is_core_write(module: _Module, func: ast.expr) -> str | None:
    """Return ``insert``/``update``/``delete`` for a SQLAlchemy write constructor."""
    origin = module.qualified(func)
    if origin is None:
        return None
    verb = origin.rsplit(".", 1)[-1]
    if verb in _CORE_WRITES and (origin == verb or origin.startswith("sqlalchemy")):
        return verb
    return None


def _guarded(module: _Module, node: ast.expr) -> str | None:
    """Return the guarded model or table a statement argument names, if any."""
    if isinstance(node, ast.Attribute) and node.attr == "__table__":
        return _guarded(module, node.value)
    origin = module.qualified(node)
    if origin is not None:
        name = origin.rsplit(".", 1)[-1]
        if name in _GUARDED_NAMES and (origin == name or origin.startswith("podex")):
            return name
    if isinstance(node, ast.Call):
        factory = module.qualified(node.func)
        if (
            factory is not None
            and factory.rsplit(".", 1)[-1] in _TABLE_FACTORIES
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value in _GUARDED_TABLES
        ):
            return str(node.args[0].value)
    if (
        isinstance(node, ast.Subscript)
        and isinstance(node.slice, ast.Constant)
        and node.slice.value in _GUARDED_TABLES
    ):
        return str(node.slice.value)
    return None


def _queried(module: _Module, node: ast.expr) -> str | None:
    """Return the guarded model of a ``query(Model)...`` chain, if any."""
    current: ast.expr = node
    while isinstance(current, (ast.Call, ast.Attribute)):
        if isinstance(current, ast.Call):
            if (
                isinstance(current.func, ast.Attribute)
                and current.func.attr == "query"
                and current.args
            ):
                return _guarded(module, current.args[0])
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


def _reason(module: _Module, node: ast.Call) -> str | None:
    """Return why a call bypasses the flush for a guarded table, if it does."""
    method = node.func.attr if isinstance(node.func, ast.Attribute) else None
    verb = _is_core_write(module, node.func)
    if method in _BULK_METHODS:
        return f"session.{method}"
    if verb and node.args and _guarded(module, node.args[0]):
        return f"Core {verb}({_guarded(module, node.args[0])})"
    if (
        method in _CORE_WRITES
        and isinstance(node.func, ast.Attribute)
        and _guarded(module, node.func.value)
    ):
        return f"{_guarded(module, node.func.value)}.{method}()"
    if (
        method in {"update", "delete"}
        and isinstance(node.func, ast.Attribute)
        and _queried(module, node.func.value)
    ):
        return f"Query({_queried(module, node.func.value)}).{method}()"
    if (
        method in {"execute", "exec_driver_sql"}
        or verb is None
        and (module.qualified(node.func) or "").endswith("text")
    ):
        target = _raw_sql_target(node)
        if target is not None:
            return f"raw SQL writing {target}"
    return None


def find_bypass_writes(source: str, path: str) -> list[_Finding]:
    """Return every guarded-table write in ``source`` that skips the flush."""
    tree = ast.parse(source)
    module = _imports(tree)
    return [
        _Finding(path=path, line=node.lineno, reason=reason)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and (reason := _reason(module, node)) is not None
    ]


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


_HEADER = "from sqlalchemy import insert, update, delete, text\n"
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
    "dialect-insert": (
        "from sqlalchemy.dialects.sqlite import insert as sqlite_insert\n"
        "db.execute(sqlite_insert(Mention).values(context='x'))"
    ),
    "model-alias": (
        "from podex.models import Mention as Hidden\n"
        "db.execute(insert(Hidden).values(context='x'))"
    ),
    "module-alias": (
        "import sqlalchemy as sa\nfrom podex import models\n"
        "db.execute(sa.insert(models.SemanticChunk).values(text='x'))"
    ),
    "table-by-name": (
        "import sqlalchemy as sa\n"
        "db.execute(sa.table('mentions', sa.column('id')).insert())"
    ),
    "metadata-table": "db.execute(Base.metadata.tables['transcripts'].update())",
    "relative-model": (
        "from ..models import Mention\n"
        "db.execute(insert(Mention).values(context='x'))"
    ),
    "relative-package": (
        "from .. import models\n"
        "db.execute(insert(models.EpisodeSummary).values(summary_text='x'))"
    ),
}


@pytest.mark.parametrize("snippet", _BYPASSES.values(), ids=_BYPASSES.keys())
def test_scan_detects_each_bypass_interface(snippet: str) -> None:
    """The scan recognises every interface that skips the flush guard."""
    assert_that(find_bypass_writes(_HEADER + snippet, "snippet.py")).is_length(1)


def test_scan_ignores_orm_writes_and_unguarded_tables() -> None:
    """ORM writes and bulk writes of other tables are not reported."""
    allowed = "\n".join(
        [
            _HEADER,
            "db.add(EpisodeSummary(summary_text='x'))",
            "db.query(AccountAlertEvent).filter(x).update({'a': 1})",
            "db.execute(insert(AuditLog).values(summary='x'))",
            "db.execute(text('UPDATE magic_link_tokens SET used = 1'))",
            "db.query(Mention).filter(x).all()",
            "db.execute(sa.table('content_suppressions').insert())",
        ],
    )

    assert_that(find_bypass_writes(allowed, "snippet.py")).is_empty()

"""Guarded tables are written through the ORM unit of work only.

The suppression guard sees ORM flushes. Core ``insert``/``update``/``delete``
(including the dialect variants and their upserts), ``Query.update``/
``Query.delete``, raw SQL and the ``bulk_*`` session methods skip it, so no
module may write a guarded table that way. The scan parses each module with
``ast`` and resolves its imports, so aliases (``insert as sqlite_insert``,
``Mention as M``, ``import sqlalchemy as sa``, ``from ..models import
Mention``) are followed. A statement's target counts as the model whether it
is given positionally or as ``table=``, through ``aliased(...)`` or
``__table__``, or as a table named by string (``table("mentions")``,
``metadata.tables["mentions"]``). Raw SQL targets are read after comments are
removed, and may be quoted or schema-qualified; ``INSERT``, ``INSERT OR ...``,
``REPLACE``, ``UPDATE`` and ``DELETE`` all count.

``Episode`` and ``Podcast`` are scanned the same way (ADR 0017): an update or
an upsert can move an episode to another podcast without carrying its
podcast's suppressions, an insert can bring back a deleted row's id, and a
delete would skip the guard's refusal.

What it cannot see: a write built dynamically (a model or table chosen at
run time, SQL assembled from variables), a write through a helper defined in
another package, and code outside ``backend/src``, ``backend/scripts`` and
``backend/alembic``. A new bypass fails here until it is removed or allowed
in ``_EXEMPT`` with a reason that covers exactly its verb and table.
"""

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that

from podex.models import GUARDED_MODELS, Episode, Podcast

_BACKEND = Path(__file__).resolve().parent.parent
_SCANNED = ("src", "scripts", "alembic")
_SCANNED_MODELS: tuple[type[Any], ...] = (*GUARDED_MODELS, Episode, Podcast)
_TABLE_OF_MODEL: dict[str, str] = {
    model.__name__: str(model.__table__.name) for model in _SCANNED_MODELS
}
_TABLES = frozenset(_TABLE_OF_MODEL.values())
_CORE_WRITES = frozenset({"insert", "update", "delete"})
_BULK_METHODS = frozenset(
    {"bulk_save_objects", "bulk_insert_mappings", "bulk_update_mappings"},
)
_TABLE_FACTORIES = frozenset({"table", "Table"})
_SQL_COMMENTS = re.compile(r"/\*.*?\*/|--[^\n]*", re.DOTALL)
_IDENTIFIER = r"[`\"\[]?\w+[`\"\]]?"
_SQL_WRITE = re.compile(
    r"(?<![\w.])(?P<verb>insert(?:\s+or\s+\w+)?\s+into|replace\s+into"
    r"|update(?:\s+or\s+\w+)?|delete\s+from)\s+"
    rf"(?P<target>(?:{_IDENTIFIER}\s*\.\s*)?{_IDENTIFIER})",
)


@dataclass(frozen=True, slots=True)
class _Exemption:
    """Writes one module may make around the flush, and why."""

    reason: str
    writes: frozenset[tuple[str, str]]


_EXEMPT = {
    "src/podex/services/takedown_requests.py": _Exemption(
        reason=(
            "takedown execution deletes an episode's derived rows and clears its"
            " derived transcript text in bulk; it only removes content"
        ),
        writes=frozenset(
            {
                ("delete", "derivative_generation_runs"),
                ("delete", "semantic_chunks"),
                ("delete", "episode_summaries"),
                ("update", "transcripts"),
            },
        ),
    ),
}


@dataclass(frozen=True, slots=True)
class _Finding:
    """One bypass write found in a module: its verb, table and form."""

    path: str
    line: int
    verb: str
    table: str
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


def _target(module: _Module, node: ast.expr) -> str | None:
    """Return the scanned table a statement target names, if any."""
    if isinstance(node, ast.Attribute) and node.attr == "__table__":
        return _target(module, node.value)
    origin = module.qualified(node)
    if origin is not None:
        name = origin.rsplit(".", 1)[-1]
        if name in _TABLE_OF_MODEL and (origin == name or origin.startswith("podex")):
            return _TABLE_OF_MODEL[name]
    if isinstance(node, ast.Call) and node.args:
        factory = (module.qualified(node.func) or "").rsplit(".", 1)[-1]
        first = node.args[0]
        if factory == "aliased":
            return _target(module, first)
        if (
            factory in _TABLE_FACTORIES
            and isinstance(first, ast.Constant)
            and first.value in _TABLES
        ):
            return str(first.value)
    if (
        isinstance(node, ast.Subscript)
        and isinstance(node.slice, ast.Constant)
        and node.slice.value in _TABLES
    ):
        return str(node.slice.value)
    return None


def _statement_target(module: _Module, call: ast.Call) -> str | None:
    """Return the table of ``insert(...)`` and friends, positional or ``table=``."""
    candidates = [*call.args[:1]]
    candidates.extend(
        keyword.value for keyword in call.keywords if keyword.arg == "table"
    )
    for candidate in candidates:
        if (table := _target(module, candidate)) is not None:
            return table
    return None


def _queried(module: _Module, node: ast.expr) -> str | None:
    """Return the scanned table of a ``query(Model)...`` chain, if any."""
    current: ast.expr = node
    while isinstance(current, (ast.Call, ast.Attribute)):
        if isinstance(current, ast.Call):
            if (
                isinstance(current.func, ast.Attribute)
                and current.func.attr == "query"
                and current.args
            ):
                return _target(module, current.args[0])
            current = current.func
        else:
            current = current.value
    return None


def sql_writes(sql: str) -> list[tuple[str, str]]:
    """Return each ``(verb, table)`` a SQL string writes to a scanned table.

    Comments are removed first; a target may be quoted (``"t"``, `` `t` ``,
    ``[t]``) or schema-qualified. ``INSERT OR ...`` and ``REPLACE`` are inserts.
    """
    cleaned = " ".join(_SQL_COMMENTS.sub(" ", sql).lower().split())
    writes: list[tuple[str, str]] = []
    for match in _SQL_WRITE.finditer(cleaned):
        table = re.sub(r"[`\"\[\]\s]", "", match["target"]).rsplit(".", 1)[-1]
        verb = match["verb"].split()[0]
        if table in _TABLES:
            writes.append(("insert" if verb == "replace" else verb, table))
    return writes


def _raw_sql_write(call: ast.Call) -> tuple[str, str] | None:
    """Return the first scanned ``(verb, table)`` a SQL string literal writes."""
    for argument in call.args:
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            writes = sql_writes(argument.value)
            if writes:
                return writes[0]
    return None


def _write(module: _Module, node: ast.Call) -> tuple[str, str, str] | None:
    """Return ``(verb, table, form)`` for a call that bypasses the flush."""
    method = node.func.attr if isinstance(node.func, ast.Attribute) else None
    verb = _is_core_write(module, node.func)
    if method in _BULK_METHODS:
        bulk = (_target(module, node.args[0]) if node.args else None) or "?"
        return "bulk", bulk, f"session.{method}"
    if verb and (constructed := _statement_target(module, node)):
        return verb, constructed, f"Core {verb}({constructed})"
    if (
        method in _CORE_WRITES
        and isinstance(node.func, ast.Attribute)
        and (own := _target(module, node.func.value))
    ):
        return method, own, f"{own}.{method}()"
    if (
        method in {"update", "delete"}
        and isinstance(node.func, ast.Attribute)
        and (queried := _queried(module, node.func.value))
    ):
        return method, queried, f"Query({queried}).{method}()"
    if (
        method in {"execute", "exec_driver_sql"}
        or verb is None
        and (module.qualified(node.func) or "").endswith("text")
    ):
        written = _raw_sql_write(node)
        if written is not None:
            return *written, f"raw SQL {written[0]} {written[1]}"
    return None


def find_bypass_writes(source: str, path: str) -> list[_Finding]:
    """Return every scanned-table write in ``source`` that skips the flush."""
    tree = ast.parse(source)
    module = _imports(tree)
    findings: list[_Finding] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and (write := _write(module, node)):
            verb, table, reason = write
            findings.append(
                _Finding(
                    path=path, line=node.lineno, verb=verb, table=table, reason=reason
                ),
            )
    return findings


def _exemption(relative: str) -> _Exemption | None:
    for path, exemption in _EXEMPT.items():
        if relative == path or relative.startswith(f"{path}/"):
            return exemption
    return None


def unexempted(findings: list[_Finding]) -> list[_Finding]:
    """Drop the findings an exemption covers by both verb and table."""
    kept: list[_Finding] = []
    for finding in findings:
        exemption = _exemption(finding.path)
        if exemption is None or (finding.verb, finding.table) not in exemption.writes:
            kept.append(finding)
    return kept


def _scan(relative: str) -> list[_Finding]:
    return find_bypass_writes(
        (_BACKEND / relative).read_text(encoding="utf-8"),
        relative,
    )


def test_no_module_writes_a_guarded_table_around_the_flush() -> None:
    """Every scanned-table write in the backend goes through the ORM flush."""
    findings: list[_Finding] = []
    for directory in _SCANNED:
        for path in sorted((_BACKEND / directory).rglob("*.py")):
            findings.extend(_scan(path.relative_to(_BACKEND).as_posix()))

    assert_that(unexempted(findings)).is_empty()


def test_every_exemption_is_still_used_exactly() -> None:
    """Each exempt module exists and still makes every write it is allowed."""
    for path, exemption in _EXEMPT.items():
        assert_that((_BACKEND / path).exists()).described_as(path).is_true()
        made = {(finding.verb, finding.table) for finding in _scan(path)}
        assert_that(made).described_as(path).is_equal_to(set(exemption.writes))


@pytest.mark.parametrize(
    "snippet",
    [
        "db.execute(update(Episode).values(podcast_id=2))",
        "db.execute(delete(Episode).where(x))",
        "db.execute(delete(Podcast).where(x))",
        "db.query(Mention).filter(x).delete()",
        "db.execute(text('UPDATE episodes SET podcast_id = 2'))",
    ],
    ids=["episode-move", "episode-delete", "podcast-delete", "mention-delete", "raw"],
)
def test_an_exemption_covers_only_its_own_writes(snippet: str) -> None:
    """A write the exemption's reason does not name is reported in that module."""
    path = "src/podex/services/takedown_requests.py"
    findings = find_bypass_writes(_HEADER + snippet, path)

    assert_that(unexempted(findings)).is_length(1)


def test_an_exempt_write_is_not_reported() -> None:
    """The writes an exemption names stay allowed in its module."""
    path = "src/podex/services/takedown_requests.py"
    snippet = "db.query(SemanticChunk).filter(x).delete()"

    assert_that(unexempted(find_bypass_writes(_HEADER + snippet, path))).is_empty()


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
    "keyword-target": "db.execute(update(table=Mention).values(context='x'))",
    "aliased-target": (
        "from sqlalchemy.orm import aliased\n"
        "db.execute(update(aliased(Mention)).values(context='x'))"
    ),
    "quoted-sql": "db.execute(text('UPDATE \"mentions\" SET context = 1'))",
    "schema-sql": "db.execute(text('DELETE FROM main.semantic_chunks'))",
    "commented-sql": (
        "db.execute(text('INSERT /* x */ INTO transcripts (a) VALUES (1)'))"
    ),
    "replace-sql": "db.execute(text('REPLACE INTO episode_summaries (a) VALUES (1)'))",
    "episode-move": "db.execute(update(Episode).values(podcast_id=2))",
    "episode-table-update": "conn.execute(Episode.__table__.update(), rows)",
    "episode-query-update": "db.query(Episode).filter(x).update({'podcast_id': 2})",
    "episode-delete": "db.execute(delete(Episode).where(x))",
    "podcast-delete": "db.execute(delete(Podcast).where(x))",
    "podcast-query-delete": "db.query(Podcast).filter(x).delete()",
    "podcast-update": "db.execute(update(Podcast).values(status='paused'))",
    "episode-insert": "db.execute(insert(Episode).values(podcast_id=1))",
    "podcast-insert": "db.execute(insert(Podcast).values(name='x'))",
    "episode-raw-update": "db.execute(text('UPDATE episodes SET podcast_id = 2'))",
    "podcast-raw-delete": "db.execute(text('DELETE FROM podcasts WHERE id = 1'))",
    "podcast-table-by-name": (
        "import sqlalchemy as sa\n"
        "db.execute(sa.table('podcasts', sa.column('id')).delete())"
    ),
    "episode-keyword-target": (
        "db.execute(update(table=Episode).values(podcast_id=destination))"
    ),
    "episode-aliased-target": (
        "from sqlalchemy.orm import aliased\n"
        "db.execute(update(aliased(Episode)).values(podcast_id=destination))"
    ),
    "episode-quoted-sql": "db.execute(text('UPDATE \"episodes\" SET podcast_id=:id'))",
    "episode-schema-sql": "db.execute(text('UPDATE main.episodes SET podcast_id=:id'))",
    "episode-commented-sql": (
        "db.execute(text('UPDATE /* move */ episodes SET podcast_id=:id'))"
    ),
    "episode-core-upsert": (
        "from sqlalchemy.dialects.sqlite import insert as sqlite_insert\n"
        "db.execute(sqlite_insert(Episode).values(id=1).on_conflict_do_update("
        "index_elements=['id'], set_={'podcast_id': other}))"
    ),
    "episode-raw-upsert": (
        "db.execute(text('INSERT INTO episodes (id, podcast_id) VALUES (:id, :p)"
        " ON CONFLICT (id) DO UPDATE SET podcast_id = excluded.podcast_id'))"
    ),
    "episode-insert-or-replace": (
        "db.execute(text('INSERT OR REPLACE INTO episodes (id) VALUES (1)'))"
    ),
    "episode-replace": "db.execute(text('REPLACE INTO episodes (id) VALUES (1)'))",
    "episode-quoted-delete": (
        "db.execute(text('DELETE FROM \"episodes\" WHERE id=:id'))"
    ),
}


@pytest.mark.parametrize("snippet", _BYPASSES.values(), ids=_BYPASSES.keys())
def test_scan_detects_each_bypass_interface(snippet: str) -> None:
    """The scan recognises every interface that skips the flush guard."""
    assert_that(find_bypass_writes(_HEADER + snippet, "snippet.py")).is_length(1)


def test_scan_ignores_orm_writes_reads_and_unguarded_tables() -> None:
    """ORM writes, reads and writes of other tables are not reported."""
    allowed = "\n".join(
        [
            _HEADER,
            "from sqlalchemy.orm import aliased",
            "db.add(EpisodeSummary(summary_text='x'))",
            "db.add(Episode(podcast_id=1, title='x'))",
            "episode.podcast_id = 2",
            "db.delete(episode)",
            "db.query(AccountAlertEvent).filter(x).update({'a': 1})",
            "db.execute(insert(AuditLog).values(summary='x'))",
            "db.execute(update(table=AuditLog).values(summary='x'))",
            "db.execute(update(aliased(AuditLog)).values(summary='x'))",
            "db.execute(text('UPDATE magic_link_tokens SET used = 1'))",
            "db.execute(text('UPDATE content_suppressions SET id = id WHERE 0 = 1'))",
            "db.execute(text('INSERT INTO audit_logs (a) VALUES (1)"
            " ON CONFLICT (a) DO UPDATE SET a = 2'))",
            "db.execute(text('SELECT id FROM episodes -- update episodes'))",
            "db.execute(text('/* delete from podcasts */ SELECT 1'))",
            "db.execute(text('UPDATE episodes_archive SET x = 1'))",
            "db.query(Mention).filter(x).all()",
            "db.execute(select(Episode).where(Episode.podcast_id == 1))",
            "db.execute(sa.table('content_suppressions').insert())",
        ],
    )

    assert_that(find_bypass_writes(allowed, "snippet.py")).is_empty()


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ('UPDATE "episodes" SET podcast_id=:id', [("update", "episodes")]),
        ("UPDATE main.episodes SET podcast_id=:id", [("update", "episodes")]),
        ("update `podcasts` set name = 1", [("update", "podcasts")]),
        ("DELETE FROM [mentions] WHERE 1", [("delete", "mentions")]),
        ("INSERT OR IGNORE INTO podcasts (a) VALUES (1)", [("insert", "podcasts")]),
        ("UPDATE OR REPLACE episodes SET title = 1", [("update", "episodes")]),
        ("SELECT * FROM episodes", []),
    ],
)
def test_sql_targets_are_read_through_quotes_schemas_and_variants(
    sql: str,
    expected: list[tuple[str, str]],
) -> None:
    """Raw SQL targets resolve to the table however they are spelled."""
    assert_that(sql_writes(sql)).is_equal_to(expected)

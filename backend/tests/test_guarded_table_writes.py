"""Guarded tables are written through the ORM unit of work only.

The suppression guard sees ORM flushes. Core ``insert``/``update``/``delete``
(including the dialect variants, their upserts and the ``Insert``,
``Update`` and ``Delete`` classes), ``Query.update``/``Query.delete``, raw
SQL and the ``bulk_*`` session methods skip it, so no module may write a
guarded table that way. The scan parses each module with ``ast`` and
resolves its imports, so aliases (``insert as sqlite_insert``, ``Mention as
M``, ``import sqlalchemy as sa``, ``from ..models import Mention``) are
followed. A statement's target counts as the model whether it is given
positionally or as ``table=``, through ``aliased(...)`` or ``__table__``, or
as a table named by string (``table("mentions")``,
``metadata.tables["mentions"]``).

Raw SQL passed to ``execute``, ``exec_driver_sql``, ``executemany``,
``executescript`` or ``text`` (positionally or as ``text=``) is read from
string and bytes literals and from the literal parts of f-strings, of ``%``,
``.format``, ``+`` and ``sep.join([...])`` expressions, and of literals
passed through a text-returning string method (``.strip()``,
``.replace(...)``, ...) or ``dedent``. String literals and comments are
tokenised, so a comment marker inside a literal hides nothing. Targets may
be quoted, parenthesised, qualified by up to two names or follow ``ONLY``.
``INSERT``, ``INSERT OR ...``, ``REPLACE``, ``MERGE``, ``UPDATE``,
``DELETE``, ``TRUNCATE`` (every table it lists) and ``COPY ... FROM`` all
count, and every write in a string is reported before exemptions apply.

``Episode`` and ``Podcast`` are scanned the same way (ADR 0017): an update or
an upsert can move an episode to another podcast past the guard's refusal,
an insert can bring back a deleted row's id, and a delete would skip the
guard's refusal.

An exemption grants exact writes: verb, table, the columns an update sets,
and how many times. Any other write in the exempt module is reported.

What it cannot see:

- a write built dynamically: a model or table chosen at run time, SQL or a
  table object held in a variable, SQL assembled in a loop or from values a
  replacement inserts (``"UPDATE x".replace("x", name)``);
- a write through a helper defined in another package, or through a call it
  does not know to return text;
- code outside ``backend/src``, ``backend/scripts`` and ``backend/alembic``.

``podex.suppression_backfill`` writes through a table object held in a module
variable, so the scan does not see its writes; they go to
``content_suppressions`` and the change counter, which the scan does not
cover. An ORM bulk statement run through the session
(``session.execute(update(Episode)...)``) skips the flush guard at run time;
only this scan refuses it. A new bypass fails here until it is removed or
granted in ``_EXEMPT`` with a reason that covers exactly that write.
"""

import ast
import re
from collections import Counter
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
_DML_CLASSES = {"Insert": "insert", "Update": "update", "Delete": "delete"}
_RAW_SQL_METHODS = frozenset(
    {"execute", "exec_driver_sql", "executemany", "executescript"},
)
_TEXT_METHODS = frozenset(
    {
        "casefold",
        "expandtabs",
        "format",
        "lower",
        "lstrip",
        "removeprefix",
        "removesuffix",
        "replace",
        "rstrip",
        "strip",
        "upper",
    },
)
_BULK_METHODS = frozenset(
    {"bulk_save_objects", "bulk_insert_mappings", "bulk_update_mappings"},
)
_TABLE_FACTORIES = frozenset({"table", "Table"})
_IDENTIFIER = r"(?:\"[^\"]+\"|`[^`]+`|\[[^\]]+\]|\w+)"
_NAME = rf"{_IDENTIFIER}(?:\s*\.\s*{_IDENTIFIER}){{0,2}}"
_TARGET = rf"\(?\s*{_NAME}\s*\)?"
_SQL_WRITE = re.compile(
    r"(?<![\w.])(?P<verb>insert(?:\s+or\s+\w+)?\s+into|replace\s+into"
    r"|merge\s+into|update(?:\s+or\s+\w+)?(?:\s+only)?"
    r"|delete\s+from(?:\s+only)?)\s*"
    rf"(?P<target>{_TARGET})",
)
_SQL_TRUNCATE = re.compile(
    r"(?<![\w.])truncate(?:\s+table)?\s+"
    rf"(?P<targets>(?:only\s+)?{_TARGET}(?:\s*,\s*(?:only\s+)?{_TARGET})*)",
)
_SQL_COPY_FROM = re.compile(
    rf"(?<![\w.])copy\s+(?P<target>{_NAME})(?:\s*\([^)]*\))?\s+from\b",
)
_SQL_SET = re.compile(r"\s*set\s+(?P<sets>.*?)(?:\s+(?:where|from|returning)\b|;|$)")
_PLACEHOLDER = " x "
"""Stands in for an interpolated value when literal SQL parts are joined."""

_Write = tuple[str, str, tuple[str, ...]]
"""A write's verb, table, and the columns an update sets (empty otherwise)."""


@dataclass(frozen=True, slots=True)
class _Exemption:
    """Writes one module may make around the flush, how often, and why."""

    reason: str
    writes: dict[_Write, int]


_TRANSCRIPT_TEXT = ("cleaned_at", "cleaned_text", "digest_created_at", "digest_text")
_EXEMPT = {
    "src/podex/services/takedown_requests.py": _Exemption(
        reason=(
            "takedown execution deletes an episode's derived rows and clears its"
            " derived transcript text in bulk; it only removes content"
        ),
        writes={
            ("delete", "derivative_generation_runs", ()): 1,
            ("delete", "semantic_chunks", ()): 1,
            ("delete", "episode_summaries", ()): 1,
            ("update", "transcripts", _TRANSCRIPT_TEXT): 1,
        },
    ),
}


@dataclass(frozen=True, slots=True)
class _Finding:
    """One bypass write found in a module: its verb, table, columns and form."""

    path: str
    line: int
    verb: str
    table: str
    reason: str
    columns: tuple[str, ...] = ()

    @property
    def write(self) -> _Write:
        """The write as an exemption names it."""
        return (self.verb, self.table, self.columns)


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
    """Return the verb of a SQLAlchemy write constructor or DML class, if any."""
    origin = module.qualified(func)
    if origin is None:
        return None
    name = origin.rsplit(".", 1)[-1]
    if name in _CORE_WRITES and (origin == name or origin.startswith("sqlalchemy")):
        return name
    if name in _DML_CLASSES and origin.startswith("sqlalchemy"):
        return _DML_CLASSES[name]
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


def _table_name(target: str) -> str:
    """Return a target's table name without quotes, brackets, schema or ``ONLY``."""
    bare = re.sub(r"^\s*only\s+", "", target)
    return re.sub(r"[`\"\[\]()\s]", "", bare).rsplit(".", 1)[-1]


def _normalise_sql(sql: str) -> str:
    """Lower-case SQL with string literals emptied and comments removed.

    Literals are read before comments, so a comment marker inside a literal
    (``'--'``, ``'/*'``) neither hides nor starts anything. Double-quoted
    identifiers are kept.
    """
    parts: list[str] = []
    index = 0
    while index < len(sql):
        if sql[index] in "'\"":
            quote = sql[index]
            end = index + 1
            while end < len(sql) and not (
                sql[end] == quote and sql[end + 1 : end + 2] != quote
            ):
                end += 2 if sql[end] == quote else 1
            parts.append(" '' " if quote == "'" else sql[index : end + 1])
            index = end + 1
        elif sql.startswith(("--", "/*"), index):
            closer = "\n" if sql.startswith("--", index) else "*/"
            end = sql.find(closer, index + 2)
            parts.append(" ")
            index = len(sql) if end < 0 else end + len(closer)
        else:
            parts.append(sql[index])
            index += 1
    return " ".join("".join(parts).lower().split())


def _set_columns(rest: str) -> tuple[str, ...]:
    """Return the sorted columns an ``UPDATE``'s ``SET`` clause assigns."""
    match = _SQL_SET.match(rest)
    if match is None:
        return ()
    names = (
        _table_name(assignment.split("=", 1)[0])
        for assignment in match["sets"].split(",")
        if "=" in assignment
    )
    return tuple(sorted(name for name in names if name))


def _sql_writes(sql: str) -> list[_Write]:
    """Return each write a SQL string makes to a scanned table, with columns."""
    cleaned = _normalise_sql(sql)
    writes: list[_Write] = []
    for match in _SQL_WRITE.finditer(cleaned):
        verb = match["verb"].split()[0]
        verb = {"replace": "insert", "merge": "update"}.get(verb, verb)
        columns = _set_columns(cleaned[match.end() :]) if verb == "update" else ()
        writes.append((verb, _table_name(match["target"]), columns))
    for match in _SQL_TRUNCATE.finditer(cleaned):
        writes.extend(
            ("delete", _table_name(target), ())
            for target in match["targets"].split(",")
        )
    for match in _SQL_COPY_FROM.finditer(cleaned):
        writes.append(("insert", _table_name(match["target"]), ()))
    return [write for write in writes if write[1] in _TABLES]


def sql_writes(sql: str) -> list[tuple[str, str]]:
    """Return each ``(verb, table)`` a SQL string writes to a scanned table.

    String literals and comments are tokenised first; a target may be quoted
    (``"t"``, `` `t` ``, ``[t]``), parenthesised, qualified by up to two
    names, or follow ``ONLY``. ``INSERT OR ...``, ``REPLACE`` and
    ``COPY ... FROM`` are inserts, ``MERGE`` an update and ``TRUNCATE`` a
    delete of every table it lists.
    """
    return [(verb, table) for verb, table, _columns in _sql_writes(sql)]


def _wrapped(node: ast.expr) -> ast.expr | None:
    """Return the literal inside a text-returning wrapper, if ``node`` is one.

    The template of ``%`` and ``.format``, the receiver of a string method
    that returns text (``.strip()``, ``.replace(...)``, ...), and the
    argument of ``dedent(...)``.
    """
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        return node.left
    if not isinstance(node, ast.Call):
        return None
    if isinstance(node.func, ast.Attribute) and node.func.attr in _TEXT_METHODS:
        return node.func.value
    name = node.func.attr if isinstance(node.func, ast.Attribute) else ""
    name = node.func.id if isinstance(node.func, ast.Name) else name
    if name == "dedent" and node.args:
        return node.args[0]
    return None


def _composed(node: ast.expr) -> str | None:
    """Return the literal text of an f-string, ``+`` or ``sep.join([...])``."""
    if isinstance(node, ast.JoinedStr):
        return "".join(
            (
                part.value
                if isinstance(part, ast.Constant) and isinstance(part.value, str)
                else _PLACEHOLDER
            )
            for part in node.values
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        pieces: list[ast.expr] = [node.left, node.right]
        separator = ""
    elif (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "join"
        and len(node.args) == 1
        and isinstance(node.args[0], (ast.List, ast.Tuple))
    ):
        pieces = list(node.args[0].elts)
        separator = literal_sql(node.func.value) or " "
    else:
        return None
    parts = [literal_sql(piece) for piece in pieces]
    if all(part is None for part in parts):
        return None
    return separator.join(part if part is not None else _PLACEHOLDER for part in parts)


def literal_sql(node: ast.expr) -> str | None:
    """Return the SQL text a statically written expression spells, if any.

    String and bytes literals count, and so do the literal parts of an
    f-string and of ``%``, ``.format``, ``+`` and ``sep.join([...])``
    expressions, and literals passed through a text-returning string method
    or ``dedent``; interpolated values become a placeholder. A string or
    table held in a variable is not followed.
    """
    if (inner := _wrapped(node)) is not None:
        return literal_sql(inner)
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
        value = node.value
        return value if isinstance(value, str) else value.decode("utf-8", "replace")
    return _composed(node)


def _raw_sql_writes(call: ast.Call) -> list[_Write]:
    """Return every scanned write the call's SQL literals make."""
    arguments = [*call.args, *(keyword.value for keyword in call.keywords)]
    writes: list[_Write] = []
    for argument in arguments:
        sql = literal_sql(argument)
        if sql is not None:
            writes.extend(_sql_writes(sql))
    return writes


def _dict_columns(call: ast.Call) -> tuple[str, ...]:
    """Return the sorted columns a ``Query.update({...})`` sets."""
    if not call.args or not isinstance(call.args[0], ast.Dict):
        return ()
    names: list[str] = []
    for key in call.args[0].keys:
        if isinstance(key, ast.Attribute):
            names.append(key.attr)
        elif isinstance(key, ast.Constant) and isinstance(key.value, str):
            names.append(key.value)
    return tuple(sorted(names))


def _writes(module: _Module, node: ast.Call) -> list[tuple[_Write, str]]:
    """Return each write a call makes around the flush, with its form."""
    method = node.func.attr if isinstance(node.func, ast.Attribute) else None
    verb = _is_core_write(module, node.func)
    if method in _BULK_METHODS:
        bulk = (_target(module, node.args[0]) if node.args else None) or "?"
        return [(("bulk", bulk, ()), f"session.{method}")]
    if verb and (constructed := _statement_target(module, node)):
        return [((verb, constructed, ()), f"Core {verb}({constructed})")]
    if (
        method in _CORE_WRITES
        and isinstance(node.func, ast.Attribute)
        and (own := _target(module, node.func.value))
    ):
        return [((method, own, ()), f"{own}.{method}()")]
    if (
        method in {"update", "delete"}
        and isinstance(node.func, ast.Attribute)
        and (queried := _queried(module, node.func.value))
    ):
        columns = _dict_columns(node) if method == "update" else ()
        return [((method, queried, columns), f"Query({queried}).{method}()")]
    if (
        method in _RAW_SQL_METHODS
        or verb is None
        and (module.qualified(node.func) or "").endswith("text")
    ):
        return [
            (write, f"raw SQL {write[0]} {write[1]}") for write in _raw_sql_writes(node)
        ]
    return []


def find_bypass_writes(source: str, path: str) -> list[_Finding]:
    """Return every scanned-table write in ``source`` that skips the flush."""
    tree = ast.parse(source)
    module = _imports(tree)
    findings: list[_Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for (verb, table, columns), reason in _writes(module, node):
            findings.append(
                _Finding(
                    path=path,
                    line=node.lineno,
                    verb=verb,
                    table=table,
                    reason=reason,
                    columns=columns,
                ),
            )
    return sorted(findings, key=lambda finding: finding.line)


def _exemption(relative: str) -> _Exemption | None:
    for path, exemption in _EXEMPT.items():
        if relative == path or relative.startswith(f"{path}/"):
            return exemption
    return None


def unexempted(findings: list[_Finding]) -> list[_Finding]:
    """Drop the findings an exemption grants, by write and number of times.

    A write matches by verb, table and the columns an update sets; each grant
    covers as many occurrences as it names, in source order.
    """
    remaining: dict[str, dict[_Write, int]] = {}
    kept: list[_Finding] = []
    for finding in findings:
        exemption = _exemption(finding.path)
        if exemption is not None:
            left = remaining.setdefault(finding.path, dict(exemption.writes))
            if left.get(finding.write, 0) > 0:
                left[finding.write] -= 1
                continue
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
        made = Counter(finding.write for finding in _scan(path))
        assert_that(dict(made)).described_as(path).is_equal_to(exemption.writes)


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


def test_every_write_in_a_string_is_reported_before_exemptions() -> None:
    """An exempt write cannot hide a second write in the same SQL string."""
    path = "src/podex/services/takedown_requests.py"
    snippet = (
        "db.execute(text('UPDATE transcripts SET cleaned_text = NULL,"
        " cleaned_at = NULL, digest_text = NULL, digest_created_at = NULL;"
        " UPDATE episodes SET podcast_id = 2'))"
    )

    findings = find_bypass_writes(_HEADER + snippet, path)

    assert_that([(item.verb, item.table) for item in findings]).is_equal_to(
        [("update", "transcripts"), ("update", "episodes")],
    )
    assert_that(
        [(item.verb, item.table) for item in unexempted(findings)],
    ).is_equal_to([("update", "episodes")])


@pytest.mark.parametrize(
    "snippet",
    [
        "db.execute(text('UPDATE transcripts SET raw_text = NULL'))",
        "db.query(Transcript).filter(x).update({Transcript.raw_text: None})",
        "db.query(SemanticChunk).filter(x).delete()\n"
        "db.query(SemanticChunk).filter(y).delete()",
    ],
    ids=["other-columns-raw", "other-columns-query", "second-delete"],
)
def test_an_exemption_matches_only_the_exact_write_it_grants(snippet: str) -> None:
    """Other columns, or one more occurrence, of an exempt table are reported."""
    path = "src/podex/services/takedown_requests.py"

    assert_that(unexempted(find_bypass_writes(_HEADER + snippet, path))).is_length(1)


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
    "episode-f-string": 'db.execute(text(f"UPDATE episodes SET podcast_id={x}"))',
    "episode-percent": (
        "db.execute(text('UPDATE episodes SET podcast_id=%s' % destination))"
    ),
    "episode-format": (
        "db.execute(text('UPDATE episodes SET podcast_id={}'.format(destination)))"
    ),
    "episode-concatenation": (
        "db.execute(text('UPDATE episodes SET podcast_id=' + str(destination)))"
    ),
    "episode-text-keyword": (
        "db.execute(text(text='UPDATE episodes SET podcast_id=:id'))"
    ),
    "episode-update-only": "db.execute(text('UPDATE ONLY episodes SET title = 1'))",
    "episode-delete-only": "db.execute(text('DELETE FROM ONLY episodes'))",
    "episode-merge": (
        "db.execute(text('MERGE INTO episodes USING staged ON (episodes.id ="
        " staged.id) WHEN MATCHED THEN UPDATE SET podcast_id = staged.podcast_id'))"
    ),
    "episode-truncate": "db.execute(text('TRUNCATE episodes'))",
    "episode-copy-from": (
        "conn.exec_driver_sql('COPY episodes (id, podcast_id) FROM STDIN')"
    ),
    "mention-f-string": 'db.execute(text(f"DELETE FROM mentions WHERE id = {x}"))',
    "literal-quote-hides-no-comment": (
        "db.execute(text(\"SELECT '--'; UPDATE episodes SET podcast_id = 2\"))"
    ),
    "update-only-parenthesised": (
        "db.execute(text('UPDATE ONLY (episodes) SET podcast_id = 2'))"
    ),
    "delete-only-parenthesised": "db.execute(text('DELETE FROM ONLY (episodes)'))",
    "three-part-name": (
        "db.execute(text('UPDATE podex.public.episodes SET podcast_id = 2'))"
    ),
    "strip-call": (
        "db.execute(text('  UPDATE episodes SET podcast_id = 2  '.strip()))"
    ),
    "replace-call": (
        "db.execute(text('UPDATE episodes SET podcast_id = X'.replace('X', '2')))"
    ),
    "dedent-call": (
        "import textwrap\n"
        "db.execute(text(textwrap.dedent('UPDATE episodes SET podcast_id = 2')))"
    ),
    "join-call": (
        "db.execute(text(' '.join(['UPDATE episodes', 'SET podcast_id = 2'])))"
    ),
    "executemany": "cursor.executemany('UPDATE episodes SET podcast_id = ?', rows)",
    "executescript": "conn.executescript('DELETE FROM podcasts WHERE id = 1')",
    "dml-update-class": (
        "from sqlalchemy.sql.dml import Update\n"
        "db.execute(Update(Episode).values(podcast_id=2))"
    ),
    "dml-delete-class": (
        "from sqlalchemy.sql.dml import Delete\ndb.execute(Delete(Podcast))"
    ),
    "dml-insert-class": (
        "from sqlalchemy.sql.dml import Insert\n"
        "db.execute(Insert(Episode).values(id=1))"
    ),
    "bytes-literal": "conn.exec_driver_sql(b'UPDATE episodes SET podcast_id = 2')",
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
            "db.execute(text('COPY episodes TO STDOUT'))",
            "db.execute(text(f'SELECT * FROM episodes WHERE id = {x}'))",
            "db.execute(text('SELECT %s FROM podcasts' % column))",
            "db.execute(statement)",
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
        (
            "TRUNCATE TABLE ONLY episodes, podcasts",
            [
                ("delete", "episodes"),
                ("delete", "podcasts"),
            ],
        ),
        ("COPY public.episodes FROM '/tmp/x'", [("insert", "episodes")]),
        ("MERGE INTO mentions USING s ON true", [("update", "mentions")]),
        (
            "UPDATE transcripts SET cleaned_text = NULL WHERE '--' = '--';"
            " UPDATE episodes SET podcast_id = 2",
            [("update", "transcripts"), ("update", "episodes")],
        ),
        (
            "SELECT '/*'; DELETE FROM podcasts; SELECT '*/'",
            [("delete", "podcasts")],
        ),
        (
            "TRUNCATE TABLE transcripts, ONLY episodes",
            [("delete", "transcripts"), ("delete", "episodes")],
        ),
        ("UPDATE x SET note = 'UPDATE episodes SET a = 1'", []),
    ],
)
def test_sql_targets_are_read_through_quotes_schemas_and_variants(
    sql: str,
    expected: list[tuple[str, str]],
) -> None:
    """Raw SQL targets resolve to the table however they are spelled."""
    assert_that(sql_writes(sql)).is_equal_to(expected)

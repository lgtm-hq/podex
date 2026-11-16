"""Record suppressions for executed takedowns that have none.

Each approved takedown that already ran (``metadata_json.executed_at`` set)
should have one ``content_suppressions`` row per suppression scope it
requested: ``unpublish_mentions`` maps to ``mentions`` and
``suppress_derivatives`` to ``derivatives``. Migration ``0022`` runs this once.
It is also an operational command, run again after a rollout: an instance
still on the previous code can approve a takedown against the new schema
without recording its suppression.

    python -m podex.suppression_backfill --dry-run   # report only
    python -m podex.suppression_backfill             # write what is missing

It only adds missing rows, so re-running it when nothing is missing writes
nothing. Podcast and episode subjects come from the case. A mention subject's
``(episode, media)`` pair comes only from the execution result its decision
audit row recorded, and only when that names exactly one episode and one
media item that still exist. Anything else is reported, by case id, as
unresolved and never guessed. It uses Core statements on locally declared
tables, so it does not depend on the ORM models of a later release.
"""

import argparse
import logging
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa

from podex.config import get_settings
from podex.database import build_engine

logger = logging.getLogger(__name__)

_SCOPES_BY_ACTION = {
    "unpublish_mentions": "mentions",
    "suppress_derivatives": "derivatives",
}

_metadata = sa.MetaData()
_takedowns = sa.Table(
    "takedown_requests",
    _metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("subject_type", sa.String),
    sa.Column("subject_id", sa.Integer),
    sa.Column("status", sa.String),
    sa.Column("requested_actions_json", sa.JSON),
    sa.Column("metadata_json", sa.JSON),
)
_audit_logs = sa.Table(
    "audit_logs",
    _metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("action", sa.String),
    sa.Column("resource_type", sa.String),
    sa.Column("resource_id", sa.Integer),
    sa.Column("metadata_json", sa.JSON),
)
_suppressions = sa.Table(
    "content_suppressions",
    _metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("suppression_key", sa.String),
    sa.Column("takedown_request_id", sa.Integer),
    sa.Column("subject_type", sa.String),
    sa.Column("scope", sa.String),
    sa.Column("podcast_id", sa.Integer),
    sa.Column("episode_id", sa.Integer),
    sa.Column("media_id", sa.Integer),
    sa.Column("created_at", sa.DateTime(timezone=True)),
)
_versions = sa.Table(
    "content_suppression_versions",
    _metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("version", sa.Integer),
)
_referenced = {
    name: sa.Table(name, _metadata, sa.Column("id", sa.Integer, primary_key=True))
    for name in ("podcasts", "episodes", "media")
}

_Subject = tuple[str, tuple[int, ...]]


class _Unresolved(Exception):
    """A case whose suppression cannot be derived from stored evidence."""


@dataclass(slots=True)
class BackfillReport:
    """What a backfill wrote, or would write, and what it could not resolve.

    Attributes:
        written: Suppression keys written (or, in a dry run, to be written).
        unresolved: Case ids whose subject cannot be derived; record by hand.
        subject_gone: Podcast or episode cases whose subject no longer exists.
        removed_nothing: Mention cases whose execution removed nothing.
    """

    written: list[str] = field(default_factory=list)
    unresolved: list[int] = field(default_factory=list)
    subject_gone: list[int] = field(default_factory=list)
    removed_nothing: list[int] = field(default_factory=list)


def _exists(connection: sa.Connection, table: str, row_id: int) -> bool:
    referenced = _referenced[table]
    return (
        connection.execute(
            sa.select(referenced.c.id).where(referenced.c.id == row_id),
        ).first()
        is not None
    )


def _execution_result(connection: sa.Connection, case_id: int) -> dict[str, Any]:
    """Return the execution result the case's decision audit row recorded."""
    row = connection.execute(
        sa.select(_audit_logs.c.metadata_json)
        .where(
            _audit_logs.c.action == "decide_takedown_request",
            _audit_logs.c.resource_type == "takedown_request",
            _audit_logs.c.resource_id == case_id,
        )
        .order_by(_audit_logs.c.id.desc())
        .limit(1),
    ).first()
    metadata = row[0] if row is not None else None
    if not isinstance(metadata, dict):
        raise _Unresolved
    return metadata


def _ids(value: object) -> list[int]:
    """Read a recorded id list; anything malformed is unresolved."""
    if not isinstance(value, list) or not all(
        isinstance(item, int) and not isinstance(item, bool) for item in value
    ):
        raise _Unresolved
    return list(value)


def _mention_subjects(
    connection: sa.Connection,
    case_id: int,
    scopes: list[str],
) -> dict[str, _Subject]:
    """Resolve a mention case from its recorded execution result.

    Returns:
        Subjects per scope; empty when the takedown removed nothing.

    Raises:
        _Unresolved: If the evidence is missing, malformed, ambiguous, or
            names rows that no longer exist.
    """
    result = _execution_result(connection, case_id)
    episode_ids = _ids(result.get("episode_ids"))
    media_ids = _ids(result.get("media_ids"))
    if not episode_ids:
        return {}
    if len(episode_ids) != 1:
        raise _Unresolved
    (episode_id,) = episode_ids
    if not _exists(connection, "episodes", episode_id):
        raise _Unresolved
    subjects: dict[str, _Subject] = {}
    if "derivatives" in scopes:
        subjects["derivatives"] = ("episode", (episode_id,))
    if "mentions" in scopes and result.get("mentions_unpublished") != 0:
        if len(media_ids) != 1 or not _exists(connection, "media", media_ids[0]):
            raise _Unresolved
        subjects["mentions"] = ("mention_pair", (episode_id, media_ids[0]))
    return subjects


def _key(scope: str, subject: _Subject) -> str:
    subject_type, ids = subject
    return f"{subject_type}:{':'.join(str(item) for item in ids)}:{scope}"


def _insert(
    connection: sa.Connection,
    *,
    case_id: int,
    scope: str,
    subject: _Subject,
) -> None:
    subject_type, ids = subject
    columns: dict[str, int | None] = {
        "podcast_id": None,
        "episode_id": None,
        "media_id": None,
    }
    if subject_type == "podcast":
        columns["podcast_id"] = ids[0]
    elif subject_type == "episode":
        columns["episode_id"] = ids[0]
    else:
        columns["episode_id"], columns["media_id"] = ids
    connection.execute(
        _suppressions.insert().values(
            suppression_key=_key(scope, subject),
            takedown_request_id=case_id,
            subject_type=subject_type,
            scope=scope,
            created_at=datetime.now(UTC),
            **columns,
        ),
    )


def _bump_version(connection: sa.Connection) -> None:
    """Increase the suppression change counter once for this backfill.

    Raises:
        LookupError: If the counter row is missing (revision ``0022`` seeds it).
    """
    updated = connection.execute(
        _versions.update()
        .where(_versions.c.id == 1)
        .values(version=_versions.c.version + 1),
    )
    if not updated.rowcount:
        message = "content_suppression_versions has no row; apply the migrations"
        raise LookupError(message)


def _case_subjects(
    connection: sa.Connection,
    case: sa.Row[Any],
    scopes: list[str],
    report: BackfillReport,
) -> dict[str, _Subject]:
    """Resolve one case's subjects, filing it in the report when it has none."""
    if case.subject_type in {"podcast", "episode"}:
        table = "podcasts" if case.subject_type == "podcast" else "episodes"
        if not isinstance(case.subject_id, int):
            report.unresolved.append(case.id)
            return {}
        if not _exists(connection, table, case.subject_id):
            report.subject_gone.append(case.id)
            return {}
        return dict.fromkeys(scopes, (case.subject_type, (case.subject_id,)))
    try:
        subjects = _mention_subjects(connection, case.id, scopes)
    except _Unresolved:
        report.unresolved.append(case.id)
        return {}
    if not subjects:
        report.removed_nothing.append(case.id)
    return subjects


def backfill_suppressions(
    connection: sa.Connection,
    *,
    dry_run: bool = False,
) -> BackfillReport:
    """Write the suppressions executed takedowns should have and do not.

    Args:
        connection: Connection whose transaction the caller commits.
        dry_run: Report what would be written without writing it.

    Returns:
        What was written (or would be) and every case left unresolved.
    """
    report = BackfillReport()
    existing = {
        key for (key,) in connection.execute(sa.select(_suppressions.c.suppression_key))
    }
    cases = connection.execute(
        sa.select(_takedowns)
        .where(_takedowns.c.status == "approved")
        .order_by(_takedowns.c.id),
    ).all()
    for case in cases:
        metadata = case.metadata_json if isinstance(case.metadata_json, dict) else {}
        actions = case.requested_actions_json
        if not isinstance(actions, list):
            actions = []
        scopes = [
            scope for action, scope in _SCOPES_BY_ACTION.items() if action in actions
        ]
        if not metadata.get("executed_at") or not scopes:
            continue
        for scope, subject in _case_subjects(connection, case, scopes, report).items():
            key = _key(scope, subject)
            if key in existing:
                continue
            existing.add(key)
            report.written.append(key)
            if not dry_run:
                _insert(connection, case_id=case.id, scope=scope, subject=subject)
    if report.written and not dry_run:
        _bump_version(connection)
    if report.unresolved:
        logger.warning(
            "content suppression backfill could not resolve takedown cases %s;"
            " record their suppressions before enabling any rebuild",
            report.unresolved,
        )
    if report.subject_gone or report.removed_nothing:
        logger.warning(
            "content suppression backfill skipped takedown cases %s whose subject"
            " no longer exists and %s whose takedown removed nothing",
            report.subject_gone,
            report.removed_nothing,
        )
    return report


def main(argv: list[str] | None = None) -> int:
    """Run the backfill against the configured database.

    Returns:
        ``0`` when every case resolved, ``1`` when some need a manual record.
    """
    parser = argparse.ArgumentParser(
        prog="python -m podex.suppression_backfill",
        description="Record suppressions for executed takedowns that have none.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be written without writing it",
    )
    arguments = parser.parse_args(argv)
    engine = build_engine(get_settings())
    with engine.begin() as connection:
        report = backfill_suppressions(connection, dry_run=arguments.dry_run)
    verb = "would write" if arguments.dry_run else "wrote"
    sys.stdout.write(f"{verb} {len(report.written)} suppressions\n")
    for key in report.written:
        sys.stdout.write(f"  {key}\n")
    sys.stdout.write(f"unresolved cases: {report.unresolved}\n")
    sys.stdout.write(f"subject gone: {report.subject_gone}\n")
    sys.stdout.write(f"removed nothing: {report.removed_nothing}\n")
    return 1 if report.unresolved else 0


if __name__ == "__main__":
    raise SystemExit(main())

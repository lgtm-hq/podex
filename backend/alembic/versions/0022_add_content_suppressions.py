"""Add durable content suppressions and backfill them from executed takedowns.

Each approved takedown that already ran (``metadata_json.executed_at`` set)
gets one row per suppression scope it requested: ``unpublish_mentions`` maps to
``mentions`` and ``suppress_derivatives`` to ``derivatives``. Podcast and
episode subjects come from the case itself. A mention subject's ``Mention`` row
was deleted by the takedown, so its ``(episode, media)`` pair is taken from the
execution result that the decision's audit row recorded in the same
transaction, and only when that result names exactly one episode and one media
item.

Offline (``--sql``) output creates the table only; the backfill reads rows and
runs online. Cases that cannot be resolved are not guessed. Their ids are logged
at WARNING so an owner can supply the mapping before any rebuild path is
enabled; cases whose takedown removed nothing are logged separately. Downgrade
drops the table and leaves the takedown case records unchanged.

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-16 12:00:00.000000
"""

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from alembic import op  # type: ignore[attr-defined]

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

_SCOPES_BY_ACTION = {
    "unpublish_mentions": "mentions",
    "suppress_derivatives": "derivatives",
}

_takedowns = sa.table(
    "takedown_requests",
    sa.column("id", sa.Integer()),
    sa.column("subject_type", sa.String()),
    sa.column("subject_id", sa.Integer()),
    sa.column("status", sa.String()),
    sa.column("requested_actions_json", sa.JSON()),
    sa.column("metadata_json", sa.JSON()),
)
_audit_logs = sa.table(
    "audit_logs",
    sa.column("id", sa.Integer()),
    sa.column("action", sa.String()),
    sa.column("resource_type", sa.String()),
    sa.column("resource_id", sa.Integer()),
    sa.column("metadata_json", sa.JSON()),
)
_suppressions = sa.table(
    "content_suppressions",
    sa.column("suppression_key", sa.String()),
    sa.column("takedown_request_id", sa.Integer()),
    sa.column("subject_type", sa.String()),
    sa.column("scope", sa.String()),
    sa.column("podcast_id", sa.Integer()),
    sa.column("episode_id", sa.Integer()),
    sa.column("media_id", sa.Integer()),
    sa.column("created_at", sa.DateTime(timezone=True)),
)
_referenced = {
    name: sa.table(name, sa.column("id", sa.Integer()))
    for name in ("podcasts", "episodes", "media")
}

_Subject = tuple[str, tuple[int, ...]]

# Which id columns each subject type fills; the check constraint enforces it.
_SUBJECT_ID_SHAPES = (
    ("podcast", "podcast_id IS NOT NULL AND episode_id IS NULL AND media_id IS NULL"),
    ("episode", "podcast_id IS NULL AND episode_id IS NOT NULL AND media_id IS NULL"),
    (
        "mention_pair",
        "podcast_id IS NULL AND episode_id IS NOT NULL AND media_id IS NOT NULL",
    ),
)


def upgrade() -> None:
    """Create the suppression table, then backfill executed takedowns."""
    op.create_table(
        "content_suppressions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("suppression_key", sa.String(length=120), nullable=False),
        sa.Column("takedown_request_id", sa.Integer(), nullable=False),
        sa.Column("subject_type", sa.String(length=32), nullable=False),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("podcast_id", sa.Integer(), nullable=True),
        sa.Column("episode_id", sa.Integer(), nullable=True),
        sa.Column("media_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            " OR ".join(
                f"(subject_type = '{subject_type}' AND {ids})"
                for subject_type, ids in _SUBJECT_ID_SHAPES
            ),
            name="ck_content_suppressions_subject_ids",
        ),
        sa.ForeignKeyConstraint(["episode_id"], ["episodes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["media_id"], ["media.id"]),
        sa.ForeignKeyConstraint(["podcast_id"], ["podcasts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["takedown_request_id"], ["takedown_requests.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_content_suppressions_suppression_key",
        "content_suppressions",
        ["suppression_key"],
        unique=True,
    )
    op.create_index(
        "ix_content_suppressions_takedown_request_id",
        "content_suppressions",
        ["takedown_request_id"],
        unique=False,
    )
    op.create_index(
        "ix_content_suppressions_media_id",
        "content_suppressions",
        ["media_id"],
        unique=False,
    )
    op.create_index(
        "ix_content_suppressions_scope_episode_id",
        "content_suppressions",
        ["scope", "episode_id"],
        unique=False,
    )
    op.create_index(
        "ix_content_suppressions_scope_podcast_id",
        "content_suppressions",
        ["scope", "podcast_id"],
        unique=False,
    )
    if op.get_context().as_sql:
        logger.warning(
            "content suppression backfill reads existing takedowns and is not"
            " part of offline SQL; run this revision online to backfill",
        )
        return
    _backfill(op.get_bind())


def downgrade() -> None:
    """Drop the suppression table; takedown case records are untouched."""
    for index_name in (
        "ix_content_suppressions_scope_podcast_id",
        "ix_content_suppressions_scope_episode_id",
        "ix_content_suppressions_media_id",
        "ix_content_suppressions_takedown_request_id",
        "ix_content_suppressions_suppression_key",
    ):
        op.drop_index(index_name, table_name="content_suppressions")
    op.drop_table("content_suppressions")


def _exists(connection: sa.Connection, table: str, row_id: int) -> bool:
    """Return whether a referenced podcast, episode or media row is present."""
    referenced = _referenced[table]
    found = connection.execute(
        sa.select(referenced.c.id).where(referenced.c.id == row_id),
    ).first()
    return found is not None


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
    return metadata if isinstance(metadata, dict) else {}


def _mention_subjects(
    connection: sa.Connection,
    case_id: int,
    scopes: list[str],
) -> dict[str, _Subject] | None:
    """Resolve a mention case's subjects from its recorded execution result.

    Returns:
        Subjects per scope, ``{}`` when the takedown removed nothing, or
        ``None`` when the evidence is missing or ambiguous.
    """
    result = _execution_result(connection, case_id)
    episode_ids = result.get("episode_ids")
    media_ids = result.get("media_ids")
    if not isinstance(episode_ids, list) or not isinstance(media_ids, list):
        return None
    if not episode_ids:
        return {}
    if len(episode_ids) != 1:
        return None
    episode_id = int(episode_ids[0])
    subjects: dict[str, _Subject] = {}
    if "derivatives" in scopes:
        subjects["derivatives"] = ("episode", (episode_id,))
    if "mentions" in scopes and result.get("mentions_unpublished") != 0:
        if len(media_ids) != 1:
            return None
        subjects["mentions"] = ("mention_pair", (episode_id, int(media_ids[0])))
    return subjects


def _case_subjects(
    connection: sa.Connection,
    case: sa.Row[Any],
    scopes: list[str],
) -> dict[str, _Subject] | None:
    """Resolve the suppressed subject for each scope of one executed case."""
    if case.subject_type == "podcast":
        return dict.fromkeys(scopes, ("podcast", (case.subject_id,)))
    if case.subject_type == "episode":
        return dict.fromkeys(scopes, ("episode", (case.subject_id,)))
    return _mention_subjects(connection, case.id, scopes)


def _subject_rows_exist(connection: sa.Connection, subject: _Subject) -> bool:
    """Return whether every row a subject references still exists."""
    subject_type, ids = subject
    if subject_type == "podcast":
        return _exists(connection, "podcasts", ids[0])
    if not _exists(connection, "episodes", ids[0]):
        return False
    return subject_type == "episode" or _exists(connection, "media", ids[1])


def _insert(
    connection: sa.Connection,
    *,
    case_id: int,
    scope: str,
    subject: _Subject,
    seen: set[str],
) -> None:
    """Insert one suppression unless its key was already written."""
    subject_type, ids = subject
    key = f"{subject_type}:{':'.join(str(item) for item in ids)}:{scope}"
    if key in seen:
        return
    seen.add(key)
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
            suppression_key=key,
            takedown_request_id=case_id,
            subject_type=subject_type,
            scope=scope,
            created_at=datetime.now(UTC),
            **columns,
        ),
    )


def _backfill(connection: sa.Connection) -> None:
    """Write suppressions for executed takedowns; log what cannot be resolved."""
    cases = connection.execute(
        sa.select(_takedowns)
        .where(_takedowns.c.status == "approved")
        .order_by(_takedowns.c.id),
    ).all()
    seen: set[str] = set()
    unresolved: list[int] = []
    removed_nothing: set[int] = set()
    for case in cases:
        metadata = case.metadata_json if isinstance(case.metadata_json, dict) else {}
        actions = case.requested_actions_json or []
        scopes = [
            scope for action, scope in _SCOPES_BY_ACTION.items() if action in actions
        ]
        if not metadata.get("executed_at") or not scopes:
            continue
        subjects = _case_subjects(connection, case, scopes)
        if subjects is None:
            unresolved.append(case.id)
            continue
        if not subjects:
            removed_nothing.add(case.id)
        for scope, subject in subjects.items():
            if not _subject_rows_exist(connection, subject):
                removed_nothing.add(case.id)
                continue
            _insert(
                connection,
                case_id=case.id,
                scope=scope,
                subject=subject,
                seen=seen,
            )
    if unresolved:
        logger.warning(
            "content suppression backfill could not resolve takedown cases %s;"
            " record their suppressions before enabling any rebuild",
            unresolved,
        )
    if removed_nothing:
        logger.warning(
            "content suppression backfill skipped takedown cases %s: the subject"
            " no longer exists or the takedown removed nothing",
            sorted(removed_nothing),
        )

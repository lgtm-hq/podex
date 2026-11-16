"""Add durable content suppressions and backfill them from executed takedowns.

Creates ``content_suppressions`` and the ``content_suppression_versions``
change counter, then runs ``podex.suppression_backfill``: each approved
takedown that already ran gets one row per suppression scope it requested.
The backfill only adds missing rows and reports unresolved cases by id; it is
also an operational command (``python -m podex.suppression_backfill``) to
re-run after a rolling deploy.

Tables and indexes are created only when missing, so a run that failed part
way can be retried. Offline (``--sql``) output creates the tables only; the
backfill reads rows and must then be run as the command. Downgrade drops both
tables and leaves the takedown case records unchanged.

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-16 12:00:00.000000
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op  # type: ignore[attr-defined]

from podex.suppression_backfill import backfill_suppressions

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

# Which id columns each subject type fills; the check constraint enforces it.
_SUBJECT_ID_SHAPES = (
    ("podcast", "podcast_id IS NOT NULL AND episode_id IS NULL AND media_id IS NULL"),
    ("episode", "podcast_id IS NULL AND episode_id IS NOT NULL AND media_id IS NULL"),
    (
        "mention_pair",
        "podcast_id IS NULL AND episode_id IS NOT NULL AND media_id IS NOT NULL",
    ),
)
_INDEXES = (
    ("ix_content_suppressions_suppression_key", ["suppression_key"], True),
    ("ix_content_suppressions_takedown_request_id", ["takedown_request_id"], False),
    ("ix_content_suppressions_media_id", ["media_id"], False),
    ("ix_content_suppressions_scope_episode_id", ["scope", "episode_id"], False),
    ("ix_content_suppressions_scope_podcast_id", ["scope", "podcast_id"], False),
)


def upgrade() -> None:
    """Create the suppression tables, then backfill executed takedowns."""
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
        if_not_exists=True,
    )
    for name, columns, unique in _INDEXES:
        op.create_index(
            name,
            "content_suppressions",
            columns,
            unique=unique,
            if_not_exists=True,
        )
    op.create_table(
        "content_suppression_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        if_not_exists=True,
    )
    if op.get_context().as_sql:
        logger.warning(
            "content suppression backfill reads existing takedowns and is not"
            " part of offline SQL; run python -m podex.suppression_backfill",
        )
        return
    backfill_suppressions(op.get_bind())


def downgrade() -> None:
    """Drop the suppression tables; takedown case records are untouched."""
    op.drop_table("content_suppression_versions")
    for name, _columns, _unique in reversed(_INDEXES):
        op.drop_index(name, table_name="content_suppressions")
    op.drop_table("content_suppressions")

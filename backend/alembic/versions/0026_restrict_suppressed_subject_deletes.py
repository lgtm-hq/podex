"""Refuse to delete a podcast or episode that a content suppression covers.

``content_suppressions.podcast_id`` and ``episode_id`` were ``ON DELETE
CASCADE``, so deleting a suppressed podcast or episode also deleted its
suppression, and re-ingested content was then writable again. Both foreign
keys become ``ON DELETE RESTRICT`` (ADR 0017). Existing suppression rows are
kept as they are.

On PostgreSQL the constraints keep their default names and are dropped and
added again; adding one checks the existing rows. SQLite cannot alter a
constraint, so the table is copied with its rows; the naming convention names
the reflected, unnamed constraints the way PostgreSQL does. Downgrade restores
``ON DELETE CASCADE`` and keeps every row.

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-17 12:00:00.000000
"""

from collections.abc import Sequence

from alembic import op  # type: ignore[attr-defined]

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "content_suppressions"
_FOREIGN_KEYS = (("podcast_id", "podcasts"), ("episode_id", "episodes"))
# PostgreSQL's default foreign key name, so both backends use the same names.
_NAMING_CONVENTION = {"fk": "%(table_name)s_%(column_0_name)s_fkey"}


def _set_delete_rule(ondelete: str) -> None:
    """Recreate both subject foreign keys with ``ondelete``."""
    with op.batch_alter_table(
        _TABLE,
        naming_convention=_NAMING_CONVENTION,
    ) as batch:
        for column, referred in _FOREIGN_KEYS:
            name = f"{_TABLE}_{column}_fkey"
            batch.drop_constraint(name, type_="foreignkey")
            batch.create_foreign_key(
                name,
                referred,
                [column],
                ["id"],
                ondelete=ondelete,
            )


def upgrade() -> None:
    """Make deleting a suppressed podcast or episode fail instead of cascading."""
    _set_delete_rule("RESTRICT")


def downgrade() -> None:
    """Restore ``ON DELETE CASCADE``; every suppression row is kept."""
    _set_delete_rule("CASCADE")

"""Store the season that qualifies a feed's per-season episode number.

The column starts empty for every episode. A row without a season keeps
matching any record with its number, as before, so existing rows behave as
they did; only rows stored from now on carry the season their feed reported.

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-17 12:00:00.000000
"""

import importlib
from collections.abc import Sequence
from types import ModuleType

import sqlalchemy as sa

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _op() -> ModuleType:
    """Return Alembic's operations proxy for the running migration.

    Imported by name: the CI type checker resolves ``alembic`` to the local
    migration directory, where the runtime proxy does not exist.
    """
    return importlib.import_module("alembic.op")


def upgrade() -> None:
    """Add the nullable season number to episodes."""
    _op().add_column(
        "episodes",
        sa.Column("season_number", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    """Drop the season number."""
    _op().drop_column("episodes", "season_number")

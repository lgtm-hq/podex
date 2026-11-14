"""Mark ingestion runs stuck on the invalid ``in_progress`` status as failed.

Recurring discovery once wrote ``in_progress``, which is not an
``IngestionRunStatus`` member, so such rows cannot be loaded. A row still
holding it belongs to a run whose scheduler tick never committed.

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-14 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op  # type: ignore[attr-defined]

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.execute(
        sa.text(
            "UPDATE ingestion_runs"
            " SET status = 'FAILED', error_summary = :summary"
            " WHERE status = 'in_progress'",
        ).bindparams(summary="interrupted before completion"),
    )


def downgrade() -> None:
    """Keep repaired rows; the original invalid value cannot be loaded."""

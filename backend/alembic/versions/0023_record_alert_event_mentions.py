"""Record which mentions each media alert event reports.

Digest delivery counts how many of an event's own mentions are still
published, so a takedown of other mentions does not change or drop it.
Existing events keep a null list and are delivered with their stored counts.

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-16 21:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op  # type: ignore[attr-defined]

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the nullable mention id list."""
    with op.batch_alter_table("account_alert_events") as batch:
        batch.add_column(sa.Column("mention_ids_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Drop the mention id list; events keep their counts."""
    with op.batch_alter_table("account_alert_events") as batch:
        batch.drop_column("mention_ids_json")

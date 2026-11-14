"""Add durable keyset checkpoints for recurring batched sweeps.

The table starts empty: a sweep without a row begins a new cycle from the
lowest ID, so no backfill is needed.

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-14 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op  # type: ignore[attr-defined]

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the sweep checkpoint table."""
    op.create_table(
        "sweep_checkpoints",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sweep_key", sa.String(length=160), nullable=False),
        sa.Column("last_id", sa.Integer(), nullable=True),
        sa.Column("cycle_upper_id", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_sweep_checkpoints_sweep_key",
        "sweep_checkpoints",
        ["sweep_key"],
        unique=True,
    )


def downgrade() -> None:
    """Drop the sweep checkpoint table."""
    op.drop_index("ix_sweep_checkpoints_sweep_key", table_name="sweep_checkpoints")
    op.drop_table("sweep_checkpoints")

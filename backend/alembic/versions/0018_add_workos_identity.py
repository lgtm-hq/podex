"""Add WorkOS identity columns to account users.

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-20 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op  # type: ignore[attr-defined]

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    with op.batch_alter_table("account_users") as batch:
        batch.add_column(sa.Column("workos_id", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("first_name", sa.String(length=100), nullable=True))
        batch.add_column(sa.Column("last_name", sa.String(length=100), nullable=True))
    op.create_index(
        op.f("ix_account_users_workos_id"),
        "account_users",
        ["workos_id"],
        unique=True,
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_index(op.f("ix_account_users_workos_id"), table_name="account_users")
    with op.batch_alter_table("account_users") as batch:
        batch.drop_column("last_name")
        batch.drop_column("first_name")
        batch.drop_column("workos_id")

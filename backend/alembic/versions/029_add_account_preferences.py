"""Add persisted account notification preferences.

Revision ID: 029_add_account_preferences
Revises: 028_add_account_digests
Create Date: 2026-05-24

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "029_add_account_preferences"
down_revision: str | None = "028_add_account_digests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create user-owned notification preference records."""
    op.create_table(
        "account_preferences",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("digest_enabled", sa.Boolean(), nullable=False),
        sa.Column("digest_frequency", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["account_users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_account_preferences_user_id",
        "account_preferences",
        ["user_id"],
        unique=True,
    )


def downgrade() -> None:
    """Drop account notification preference records."""
    op.drop_index("ix_account_preferences_user_id", table_name="account_preferences")
    op.drop_table("account_preferences")

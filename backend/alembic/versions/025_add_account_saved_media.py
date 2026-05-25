"""Add account saved public media records.

Revision ID: 025_add_account_saved_media
Revises: 024_add_account_auth_sessions
Create Date: 2026-05-24

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "025_add_account_saved_media"
down_revision: str | None = "024_add_account_auth_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create saved associations between accounts and public media."""
    op.create_table(
        "account_saved_media",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("media_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["media_id"], ["media.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["account_users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "media_id",
            name="uq_account_saved_media_user_media",
        ),
    )
    op.create_index(
        "ix_account_saved_media_user_id",
        "account_saved_media",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_account_saved_media_media_id",
        "account_saved_media",
        ["media_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop saved account media associations."""
    op.drop_index(
        "ix_account_saved_media_media_id",
        table_name="account_saved_media",
    )
    op.drop_index(
        "ix_account_saved_media_user_id",
        table_name="account_saved_media",
    )
    op.drop_table("account_saved_media")

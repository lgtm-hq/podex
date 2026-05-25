"""Add account followed public podcast records.

Revision ID: 026_add_account_followed_podcasts
Revises: 025_add_account_saved_media
Create Date: 2026-05-24

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "026_add_account_followed_podcasts"
down_revision: str | None = "025_add_account_saved_media"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create followed associations between accounts and public podcasts."""
    op.create_table(
        "account_followed_podcasts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("podcast_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["podcast_id"], ["podcasts.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["account_users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "podcast_id",
            name="uq_account_followed_podcasts_user_podcast",
        ),
    )
    op.create_index(
        "ix_account_followed_podcasts_user_id",
        "account_followed_podcasts",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_account_followed_podcasts_podcast_id",
        "account_followed_podcasts",
        ["podcast_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop followed account podcast associations."""
    op.drop_index(
        "ix_account_followed_podcasts_podcast_id",
        table_name="account_followed_podcasts",
    )
    op.drop_index(
        "ix_account_followed_podcasts_user_id",
        table_name="account_followed_podcasts",
    )
    op.drop_table("account_followed_podcasts")

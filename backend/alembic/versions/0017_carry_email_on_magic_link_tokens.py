"""Carry email on magic link tokens.

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-19 08:00:27.986762
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op  # type: ignore[attr-defined]

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    with op.batch_alter_table("magic_link_tokens") as batch:
        batch.add_column(
            sa.Column(
                "email",
                sa.String(length=320),
                nullable=False,
                server_default="",
            ),
        )
        batch.alter_column("user_id", existing_type=sa.INTEGER(), nullable=True)
    op.create_index(
        op.f("ix_magic_link_tokens_email"),
        "magic_link_tokens",
        ["email"],
        unique=False,
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_index(op.f("ix_magic_link_tokens_email"), table_name="magic_link_tokens")
    with op.batch_alter_table("magic_link_tokens") as batch:
        batch.alter_column("user_id", existing_type=sa.INTEGER(), nullable=False)
        batch.drop_column("email")

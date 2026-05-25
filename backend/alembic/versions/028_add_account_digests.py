"""Add email digests for generated account alert events.

Revision ID: 028_add_account_digests
Revises: 027_add_account_alert_rules
Create Date: 2026-05-24

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "028_add_account_digests"
down_revision: str | None = "027_add_account_alert_rules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create digest delivery records and link generated events to deliveries."""
    op.create_table(
        "account_digests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("event_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("delivered_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["account_users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_account_digests_user_id",
        "account_digests",
        ["user_id"],
        unique=False,
    )
    with op.batch_alter_table("account_alert_events") as batch_op:
        batch_op.add_column(sa.Column("digest_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_account_alert_events_digest_id",
            "account_digests",
            ["digest_id"],
            ["id"],
        )
        batch_op.create_index("ix_account_alert_events_digest_id", ["digest_id"])


def downgrade() -> None:
    """Drop digest event links and delivered digest records."""
    with op.batch_alter_table("account_alert_events") as batch_op:
        batch_op.drop_index("ix_account_alert_events_digest_id")
        batch_op.drop_constraint(
            "fk_account_alert_events_digest_id",
            type_="foreignkey",
        )
        batch_op.drop_column("digest_id")
    op.drop_index("ix_account_digests_user_id", table_name="account_digests")
    op.drop_table("account_digests")

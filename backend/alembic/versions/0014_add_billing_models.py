"""Add account subscription and quota models.

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-19 00:51:00.325288
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op  # type: ignore[attr-defined]

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "account_quota_usage",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("feature", sa.String(length=40), nullable=False),
        sa.Column("units", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["account_users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "period",
            "feature",
            name="uq_account_quota_usage_user_period_feature",
        ),
    )
    op.create_index(
        op.f("ix_account_quota_usage_period"),
        "account_quota_usage",
        ["period"],
        unique=False,
    )
    op.create_index(
        op.f("ix_account_quota_usage_user_id"),
        "account_quota_usage",
        ["user_id"],
        unique=False,
    )
    op.create_table(
        "account_subscriptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("tier", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("billing_provider", sa.String(length=40), nullable=True),
        sa.Column("provider_customer_id", sa.String(length=255), nullable=True),
        sa.Column("provider_subscription_id", sa.String(length=255), nullable=True),
        sa.Column("current_period_ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["account_users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_account_subscriptions_user_id"),
        "account_subscriptions",
        ["user_id"],
        unique=True,
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_index(
        op.f("ix_account_subscriptions_user_id"), table_name="account_subscriptions"
    )
    op.drop_table("account_subscriptions")
    op.drop_index(
        op.f("ix_account_quota_usage_user_id"), table_name="account_quota_usage"
    )
    op.drop_index(
        op.f("ix_account_quota_usage_period"), table_name="account_quota_usage"
    )
    op.drop_table("account_quota_usage")

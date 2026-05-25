"""Add account subscription entitlements and metered quota use.

Revision ID: 030_add_account_subscriptions_and_quotas
Revises: 029_add_account_preferences
Create Date: 2026-05-24

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "030_add_account_subscriptions_and_quotas"
down_revision: str | None = "029_add_account_preferences"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create hosted subscription state and monthly quota accounting."""
    op.create_table(
        "account_subscriptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("tier", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("billing_provider", sa.String(length=40), nullable=True),
        sa.Column("provider_customer_id", sa.String(length=255), nullable=True),
        sa.Column("provider_subscription_id", sa.String(length=255), nullable=True),
        sa.Column("current_period_ends_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["account_users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_account_subscriptions_user_id",
        "account_subscriptions",
        ["user_id"],
        unique=True,
    )
    op.create_table(
        "account_quota_usage",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("feature", sa.String(length=40), nullable=False),
        sa.Column("units", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["account_users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "period",
            "feature",
            name="uq_account_quota_usage_user_period_feature",
        ),
    )
    op.create_index(
        "ix_account_quota_usage_user_id",
        "account_quota_usage",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_account_quota_usage_period",
        "account_quota_usage",
        ["period"],
        unique=False,
    )


def downgrade() -> None:
    """Drop hosted subscription and monthly quota state."""
    op.drop_index("ix_account_quota_usage_period", table_name="account_quota_usage")
    op.drop_index("ix_account_quota_usage_user_id", table_name="account_quota_usage")
    op.drop_table("account_quota_usage")
    op.drop_index(
        "ix_account_subscriptions_user_id",
        table_name="account_subscriptions",
    )
    op.drop_table("account_subscriptions")

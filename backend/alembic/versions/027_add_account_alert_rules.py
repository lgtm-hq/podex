"""Add account alert rules and generated events.

Revision ID: 027_add_account_alert_rules
Revises: 026_add_account_followed_podcasts
Create Date: 2026-05-24

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "027_add_account_alert_rules"
down_revision: str | None = "026_add_account_followed_podcasts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create account alert rules and their generated events."""
    op.create_table(
        "account_alert_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("target_type", sa.String(length=20), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=30), nullable=False),
        sa.Column("baseline_count", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("last_evaluated_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["account_users.id"]),
        sa.UniqueConstraint(
            "user_id",
            "target_type",
            "target_id",
            "event_type",
            name="uq_account_alert_rules_user_target_event",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_account_alert_rules_user_id",
        "account_alert_rules",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_account_alert_rules_target_type",
        "account_alert_rules",
        ["target_type"],
        unique=False,
    )
    op.create_index(
        "ix_account_alert_rules_target_id",
        "account_alert_rules",
        ["target_id"],
        unique=False,
    )
    op.create_table(
        "account_alert_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rule_id", sa.Integer(), nullable=False),
        sa.Column("previous_count", sa.Integer(), nullable=False),
        sa.Column("observed_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["rule_id"], ["account_alert_rules.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_account_alert_events_rule_id",
        "account_alert_events",
        ["rule_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop account alert events and rules."""
    op.drop_index("ix_account_alert_events_rule_id", table_name="account_alert_events")
    op.drop_table("account_alert_events")
    for column_name in ("target_id", "target_type", "user_id"):
        op.drop_index(
            f"ix_account_alert_rules_{column_name}",
            table_name="account_alert_rules",
        )
    op.drop_table("account_alert_rules")

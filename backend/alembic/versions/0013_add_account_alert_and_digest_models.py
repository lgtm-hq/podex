"""Add account alert and digest models.

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-19 00:39:45.832737
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op  # type: ignore[attr-defined]

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
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
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["account_users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "target_type",
            "target_id",
            "event_type",
            name="uq_account_alert_rules_user_target_event",
        ),
    )
    op.create_index(
        op.f("ix_account_alert_rules_target_id"),
        "account_alert_rules",
        ["target_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_account_alert_rules_target_type"),
        "account_alert_rules",
        ["target_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_account_alert_rules_user_id"),
        "account_alert_rules",
        ["user_id"],
        unique=False,
    )
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
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["account_users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_account_digests_user_id"), "account_digests", ["user_id"], unique=False
    )
    op.create_table(
        "account_alert_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rule_id", sa.Integer(), nullable=False),
        sa.Column("previous_count", sa.Integer(), nullable=False),
        sa.Column("observed_count", sa.Integer(), nullable=False),
        sa.Column("digest_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["digest_id"],
            ["account_digests.id"],
        ),
        sa.ForeignKeyConstraint(
            ["rule_id"],
            ["account_alert_rules.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_account_alert_events_digest_id"),
        "account_alert_events",
        ["digest_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_account_alert_events_rule_id"),
        "account_alert_events",
        ["rule_id"],
        unique=False,
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_index(
        op.f("ix_account_alert_events_rule_id"), table_name="account_alert_events"
    )
    op.drop_index(
        op.f("ix_account_alert_events_digest_id"), table_name="account_alert_events"
    )
    op.drop_table("account_alert_events")
    op.drop_index(op.f("ix_account_digests_user_id"), table_name="account_digests")
    op.drop_table("account_digests")
    op.drop_index(
        op.f("ix_account_alert_rules_user_id"), table_name="account_alert_rules"
    )
    op.drop_index(
        op.f("ix_account_alert_rules_target_type"), table_name="account_alert_rules"
    )
    op.drop_index(
        op.f("ix_account_alert_rules_target_id"), table_name="account_alert_rules"
    )
    op.drop_table("account_alert_rules")

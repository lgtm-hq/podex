"""Add the billing webhook event ledger for idempotent processing.

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-20 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op  # type: ignore[attr-defined]

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "billing_webhook_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("event_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("processed_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider",
            "event_id",
            name="uq_billing_webhook_events_provider_event",
        ),
    )
    op.create_index(
        op.f("ix_billing_webhook_events_event_id"),
        "billing_webhook_events",
        ["event_id"],
        unique=False,
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_index(
        op.f("ix_billing_webhook_events_event_id"),
        table_name="billing_webhook_events",
    )
    op.drop_table("billing_webhook_events")

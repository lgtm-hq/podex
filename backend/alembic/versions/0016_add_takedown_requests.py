"""Add takedown requests.

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-19 07:54:32.356300
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op  # type: ignore[attr-defined]

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "takedown_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("subject_type", sa.String(length=32), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("requester_type", sa.String(length=32), nullable=False),
        sa.Column("requester_name", sa.String(length=255), nullable=False),
        sa.Column("requester_email", sa.String(length=320), nullable=False),
        sa.Column("basis", sa.Text(), nullable=False),
        sa.Column("requested_actions_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("decided_by", sa.String(length=100), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_takedown_requests_requester_type"),
        "takedown_requests",
        ["requester_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_takedown_requests_status"),
        "takedown_requests",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_takedown_requests_subject_id"),
        "takedown_requests",
        ["subject_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_takedown_requests_subject_type"),
        "takedown_requests",
        ["subject_type"],
        unique=False,
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_index(
        op.f("ix_takedown_requests_subject_type"), table_name="takedown_requests"
    )
    op.drop_index(
        op.f("ix_takedown_requests_subject_id"), table_name="takedown_requests"
    )
    op.drop_index(op.f("ix_takedown_requests_status"), table_name="takedown_requests")
    op.drop_index(
        op.f("ix_takedown_requests_requester_type"), table_name="takedown_requests"
    )
    op.drop_table("takedown_requests")

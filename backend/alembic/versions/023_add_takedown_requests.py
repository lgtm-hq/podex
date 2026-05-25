"""Add public takedown intake case records.

Revision ID: 023_add_takedown_requests
Revises: 022_add_transcript_source_retention_policies
Create Date: 2026-05-24

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "023_add_takedown_requests"
down_revision: str | None = "022_add_transcript_source_retention_policies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create privileged takedown case records."""
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
    for column_name in ("subject_type", "subject_id", "requester_type", "status"):
        op.create_index(
            f"ix_takedown_requests_{column_name}",
            "takedown_requests",
            [column_name],
            unique=False,
        )


def downgrade() -> None:
    """Drop privileged takedown case records."""
    for column_name in ("status", "requester_type", "subject_id", "subject_type"):
        op.drop_index(
            f"ix_takedown_requests_{column_name}",
            table_name="takedown_requests",
            if_exists=True,
        )
    op.drop_table("takedown_requests")

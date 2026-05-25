"""Add persisted transcript sampling assignment details.

Revision ID: 019_add_retention_sampling_assignment
Revises: 018_add_derivative_generation_runs
Create Date: 2026-05-24

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "019_add_retention_sampling_assignment"
down_revision: str | None = "018_add_derivative_generation_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Store the applied rate, deterministic score, and sample strata."""
    op.add_column(
        "transcripts",
        sa.Column("retention_sample_rate", sa.Float(), nullable=True),
    )
    op.add_column(
        "transcripts",
        sa.Column("retention_sample_score", sa.Float(), nullable=True),
    )
    op.add_column(
        "transcripts",
        sa.Column("retention_sample_strata_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    """Remove persisted sample assignment details."""
    op.drop_column("transcripts", "retention_sample_strata_json")
    op.drop_column("transcripts", "retention_sample_score")
    op.drop_column("transcripts", "retention_sample_rate")

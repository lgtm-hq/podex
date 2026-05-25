"""Add persisted source-scoped transcript retention policies.

Revision ID: 022_add_transcript_source_retention_policies
Revises: 021_add_transcript_artifacts
Create Date: 2026-05-24

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "022_add_transcript_source_retention_policies"
down_revision: str | None = "021_add_transcript_artifacts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create source-scoped retention policy configuration records."""
    op.create_table(
        "transcript_source_retention_policies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("podcast_id", sa.Integer(), nullable=False),
        sa.Column("source_key", sa.String(length=80), nullable=False),
        sa.Column("policy_version", sa.String(length=80), nullable=False),
        sa.Column("hot_days", sa.Integer(), nullable=False),
        sa.Column("warm_days", sa.Integer(), nullable=False),
        sa.Column("min_purge_confidence", sa.Float(), nullable=False),
        sa.Column(
            "source_retention_opt_out",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["podcast_id"], ["podcasts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "podcast_id",
            "source_key",
            name="uq_transcript_source_retention_policies_source",
        ),
    )
    op.create_index(
        "ix_transcript_source_retention_policies_podcast_id",
        "transcript_source_retention_policies",
        ["podcast_id"],
        unique=False,
    )
    op.create_index(
        "ix_transcript_source_retention_policies_source_key",
        "transcript_source_retention_policies",
        ["source_key"],
        unique=False,
    )


def downgrade() -> None:
    """Drop source-scoped retention policy configuration records."""
    op.drop_index(
        "ix_transcript_source_retention_policies_source_key",
        table_name="transcript_source_retention_policies",
        if_exists=True,
    )
    op.drop_index(
        "ix_transcript_source_retention_policies_podcast_id",
        table_name="transcript_source_retention_policies",
        if_exists=True,
    )
    op.drop_table("transcript_source_retention_policies")

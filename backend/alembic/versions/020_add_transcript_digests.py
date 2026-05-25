"""Add durable transcript purge digest records.

Revision ID: 020_add_transcript_digests
Revises: 019_add_retention_sampling_assignment
Create Date: 2026-05-24

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "020_add_transcript_digests"
down_revision: str | None = "019_add_retention_sampling_assignment"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create proof-of-processing records preserved after raw transcript purge."""
    op.create_table(
        "transcript_digests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("transcript_id", sa.Integer(), nullable=False),
        sa.Column("episode_id", sa.Integer(), nullable=False),
        sa.Column("digest_key", sa.String(length=160), nullable=False),
        sa.Column("source_text_hash", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("policy_version", sa.String(length=80), nullable=True),
        sa.Column("summary_text", sa.Text(), nullable=False),
        sa.Column("sampling_strata_json", sa.JSON(), nullable=True),
        sa.Column("extraction_versions_json", sa.JSON(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["episode_id"], ["episodes.id"]),
        sa.ForeignKeyConstraint(["transcript_id"], ["transcripts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("digest_key", name="uq_transcript_digests_digest_key"),
    )
    for column_name, unique in (
        ("transcript_id", False),
        ("episode_id", False),
        ("digest_key", True),
        ("source_text_hash", False),
    ):
        op.create_index(
            f"ix_transcript_digests_{column_name}",
            "transcript_digests",
            [column_name],
            unique=unique,
        )


def downgrade() -> None:
    """Drop proof-of-processing records."""
    for column_name in (
        "source_text_hash",
        "digest_key",
        "episode_id",
        "transcript_id",
    ):
        op.drop_index(
            f"ix_transcript_digests_{column_name}",
            table_name="transcript_digests",
            if_exists=True,
        )
    op.drop_table("transcript_digests")

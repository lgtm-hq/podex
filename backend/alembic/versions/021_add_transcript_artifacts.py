"""Add encrypted raw transcript artifact metadata.

Revision ID: 021_add_transcript_artifacts
Revises: 020_add_transcript_digests
Create Date: 2026-05-24

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "021_add_transcript_artifacts"
down_revision: str | None = "020_add_transcript_digests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create metadata records for encrypted raw transcript objects."""
    op.create_table(
        "transcript_artifacts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("transcript_id", sa.Integer(), nullable=False),
        sa.Column("episode_id", sa.Integer(), nullable=False),
        sa.Column("reacquired_from_digest_id", sa.Integer(), nullable=True),
        sa.Column("storage_key", sa.String(length=500), nullable=False),
        sa.Column("storage_backend", sa.String(length=32), nullable=False),
        sa.Column("encryption_key_id", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("source_text_hash", sa.String(length=64), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("provenance_json", sa.JSON(), nullable=True),
        sa.Column("stored_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["episode_id"], ["episodes.id"]),
        sa.ForeignKeyConstraint(
            ["reacquired_from_digest_id"],
            ["transcript_digests.id"],
        ),
        sa.ForeignKeyConstraint(["transcript_id"], ["transcripts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key", name="uq_transcript_artifacts_storage_key"),
    )
    for column_name, unique in (
        ("transcript_id", False),
        ("episode_id", False),
        ("reacquired_from_digest_id", False),
        ("storage_key", True),
        ("source_text_hash", False),
    ):
        op.create_index(
            f"ix_transcript_artifacts_{column_name}",
            "transcript_artifacts",
            [column_name],
            unique=unique,
        )


def downgrade() -> None:
    """Drop encrypted raw transcript artifact metadata."""
    for column_name in (
        "source_text_hash",
        "storage_key",
        "reacquired_from_digest_id",
        "episode_id",
        "transcript_id",
    ):
        op.drop_index(
            f"ix_transcript_artifacts_{column_name}",
            table_name="transcript_artifacts",
            if_exists=True,
        )
    op.drop_table("transcript_artifacts")

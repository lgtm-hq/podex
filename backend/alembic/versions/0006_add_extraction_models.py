"""Add transcripts, mention candidates, provenance, and review items.

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-18 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op  # type: ignore[attr-defined]

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the extraction-workflow tables."""
    op.create_table(
        "transcripts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("episode_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("segments_json", sa.JSON(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(), nullable=True),
        sa.Column("cleaned_text", sa.Text(), nullable=True),
        sa.Column("cleaned_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["episode_id"], ["episodes.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_transcripts_episode_id", "transcripts", ["episode_id"])

    op.create_table(
        "mention_candidates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("episode_id", sa.Integer(), nullable=False),
        sa.Column("media_id", sa.Integer(), nullable=True),
        sa.Column("mention_id", sa.Integer(), nullable=True),
        sa.Column("media_type", sa.String(length=20), nullable=False),
        sa.Column("raw_title", sa.String(length=500), nullable=False),
        sa.Column("normalized_title", sa.String(length=500), nullable=True),
        sa.Column("suggested_author", sa.String(length=255), nullable=True),
        sa.Column("timestamp_seconds", sa.Integer(), nullable=True),
        sa.Column("context", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("extraction_source", sa.String(length=50), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["episode_id"], ["episodes.id"]),
        sa.ForeignKeyConstraint(["media_id"], ["media.id"]),
        sa.ForeignKeyConstraint(["mention_id"], ["mentions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_mention_candidates_episode_id",
        "mention_candidates",
        ["episode_id"],
    )
    op.create_index(
        "ix_mention_candidates_media_id",
        "mention_candidates",
        ["media_id"],
    )
    op.create_index(
        "ix_mention_candidates_mention_id",
        "mention_candidates",
        ["mention_id"],
        unique=True,
    )
    op.create_index(
        "ix_mention_candidates_media_type",
        "mention_candidates",
        ["media_type"],
    )
    op.create_index("ix_mention_candidates_state", "mention_candidates", ["state"])

    op.create_table(
        "mention_candidate_provenance",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("mention_candidate_id", sa.Integer(), nullable=False),
        sa.Column("media_id", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("change_summary", sa.Text(), nullable=True),
        sa.Column("raw_title", sa.String(length=500), nullable=False),
        sa.Column("normalized_title", sa.String(length=500), nullable=True),
        sa.Column("suggested_author", sa.String(length=255), nullable=True),
        sa.Column("timestamp_seconds", sa.Integer(), nullable=True),
        sa.Column("context", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("extraction_source", sa.String(length=50), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["mention_candidate_id"],
            ["mention_candidates.id"],
        ),
        sa.ForeignKeyConstraint(["media_id"], ["media.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_mention_candidate_provenance_mention_candidate_id",
        "mention_candidate_provenance",
        ["mention_candidate_id"],
    )
    op.create_index(
        "ix_mention_candidate_provenance_media_id",
        "mention_candidate_provenance",
        ["media_id"],
    )
    op.create_index(
        "ix_mention_candidate_provenance_event_type",
        "mention_candidate_provenance",
        ["event_type"],
    )

    op.create_table(
        "review_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("mention_candidate_id", sa.Integer(), nullable=False),
        sa.Column("target_media_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.String(length=16), nullable=False),
        sa.Column("assigned_to", sa.String(length=100), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["mention_candidate_id"],
            ["mention_candidates.id"],
        ),
        sa.ForeignKeyConstraint(["target_media_id"], ["media.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_review_items_mention_candidate_id",
        "review_items",
        ["mention_candidate_id"],
        unique=True,
    )
    op.create_index(
        "ix_review_items_target_media_id",
        "review_items",
        ["target_media_id"],
    )
    op.create_index("ix_review_items_status", "review_items", ["status"])
    op.create_index("ix_review_items_priority", "review_items", ["priority"])


def downgrade() -> None:
    """Drop the extraction-workflow tables."""
    op.drop_table("review_items")
    op.drop_table("mention_candidate_provenance")
    op.drop_table("mention_candidates")
    op.drop_table("transcripts")

"""Add the media_relations table.

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-18 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op  # type: ignore[attr-defined]

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create media_relations for typed media-to-media links."""
    op.create_table(
        "media_relations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("relation_key", sa.String(length=96), nullable=False),
        sa.Column("subject_media_id", sa.Integer(), nullable=False),
        sa.Column("object_media_id", sa.Integer(), nullable=False),
        sa.Column("relation_type", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence_text", sa.Text(), nullable=True),
        sa.Column("provenance_episode_id", sa.Integer(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["subject_media_id"], ["media.id"]),
        sa.ForeignKeyConstraint(["object_media_id"], ["media.id"]),
        sa.ForeignKeyConstraint(["provenance_episode_id"], ["episodes.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "relation_key",
            name="uq_media_relations_relation_key",
        ),
    )
    op.create_index(
        "ix_media_relations_relation_key",
        "media_relations",
        ["relation_key"],
        unique=True,
    )
    op.create_index(
        "ix_media_relations_subject_media_id",
        "media_relations",
        ["subject_media_id"],
    )
    op.create_index(
        "ix_media_relations_object_media_id",
        "media_relations",
        ["object_media_id"],
    )
    op.create_index(
        "ix_media_relations_relation_type",
        "media_relations",
        ["relation_type"],
    )
    op.create_index(
        "ix_media_relations_source",
        "media_relations",
        ["source"],
    )
    op.create_index(
        "ix_media_relations_provenance_episode_id",
        "media_relations",
        ["provenance_episode_id"],
    )


def downgrade() -> None:
    """Drop media_relations."""
    op.drop_table("media_relations")

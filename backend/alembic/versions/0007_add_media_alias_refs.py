"""Add media alias and external-reference tables.

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-18 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op  # type: ignore[attr-defined]

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create media_aliases and media_external_refs."""
    op.create_table(
        "media_aliases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("media_id", sa.Integer(), nullable=False),
        sa.Column("alias", sa.String(length=500), nullable=False),
        sa.Column("normalized_alias", sa.String(length=500), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["media_id"], ["media.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "media_id",
            "normalized_alias",
            name="uq_media_aliases_media_normalized_alias",
        ),
    )
    op.create_index("ix_media_aliases_media_id", "media_aliases", ["media_id"])
    op.create_index(
        "ix_media_aliases_normalized_alias",
        "media_aliases",
        ["normalized_alias"],
    )
    op.create_index("ix_media_aliases_source", "media_aliases", ["source"])

    op.create_table(
        "media_external_refs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("media_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=True),
        sa.Column("label", sa.String(length=255), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["media_id"], ["media.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "media_id",
            "source",
            "external_id",
            name="uq_media_external_refs_media_source_external_id",
        ),
    )
    op.create_index(
        "ix_media_external_refs_media_id",
        "media_external_refs",
        ["media_id"],
    )
    op.create_index(
        "ix_media_external_refs_source",
        "media_external_refs",
        ["source"],
    )
    op.create_index(
        "ix_media_external_refs_external_id",
        "media_external_refs",
        ["external_id"],
    )


def downgrade() -> None:
    """Drop media alias and external-reference tables."""
    op.drop_table("media_external_refs")
    op.drop_table("media_aliases")

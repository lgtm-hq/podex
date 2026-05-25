"""Add public editorial collections.

Revision ID: 031_add_editorial_collections
Revises: 030_add_account_subscriptions_and_quotas
Create Date: 2026-05-24

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "031_add_editorial_collections"
down_revision: str | None = "030_add_account_subscriptions_and_quotas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create curated collection records and their ordered media items."""
    op.create_table(
        "editorial_collections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("curator_name", sa.String(length=120), nullable=True),
        sa.Column("published", sa.Boolean(), nullable=False),
        sa.Column("featured", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_editorial_collections_slug",
        "editorial_collections",
        ["slug"],
        unique=True,
    )
    op.create_index(
        "ix_editorial_collections_published",
        "editorial_collections",
        ["published"],
        unique=False,
    )
    op.create_table(
        "editorial_collection_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("collection_id", sa.Integer(), nullable=False),
        sa.Column("media_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["collection_id"], ["editorial_collections.id"]),
        sa.ForeignKeyConstraint(["media_id"], ["media.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "collection_id",
            "media_id",
            name="uq_editorial_collection_items_collection_media",
        ),
    )
    op.create_index(
        "ix_editorial_collection_items_collection_id",
        "editorial_collection_items",
        ["collection_id"],
        unique=False,
    )
    op.create_index(
        "ix_editorial_collection_items_media_id",
        "editorial_collection_items",
        ["media_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop curated collection storage."""
    op.drop_index(
        "ix_editorial_collection_items_media_id",
        table_name="editorial_collection_items",
    )
    op.drop_index(
        "ix_editorial_collection_items_collection_id",
        table_name="editorial_collection_items",
    )
    op.drop_table("editorial_collection_items")
    op.drop_index(
        "ix_editorial_collections_published",
        table_name="editorial_collections",
    )
    op.drop_index("ix_editorial_collections_slug", table_name="editorial_collections")
    op.drop_table("editorial_collections")

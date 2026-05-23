"""Add graph triples and media entity relations.

Revision ID: 016_add_graph_triples
Revises: 015_add_semantic_chunks
Create Date: 2026-05-22

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "016_add_graph_triples"
down_revision: str | None = "015_add_semantic_chunks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create graph triple, relation, and external reference tables."""
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
        "ix_media_external_refs_external_id",
        "media_external_refs",
        ["external_id"],
        unique=False,
    )
    op.create_index(
        "ix_media_external_refs_media_id",
        "media_external_refs",
        ["media_id"],
        unique=False,
    )
    op.create_index(
        "ix_media_external_refs_source",
        "media_external_refs",
        ["source"],
        unique=False,
    )

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
        sa.ForeignKeyConstraint(["object_media_id"], ["media.id"]),
        sa.ForeignKeyConstraint(["provenance_episode_id"], ["episodes.id"]),
        sa.ForeignKeyConstraint(["subject_media_id"], ["media.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("relation_key", name="uq_media_relations_relation_key"),
    )
    op.create_index(
        "ix_media_relations_object_media_id",
        "media_relations",
        ["object_media_id"],
        unique=False,
    )
    op.create_index(
        "ix_media_relations_provenance_episode_id",
        "media_relations",
        ["provenance_episode_id"],
        unique=False,
    )
    op.create_index(
        "ix_media_relations_relation_key",
        "media_relations",
        ["relation_key"],
        unique=True,
    )
    op.create_index(
        "ix_media_relations_relation_type",
        "media_relations",
        ["relation_type"],
        unique=False,
    )
    op.create_index(
        "ix_media_relations_source",
        "media_relations",
        ["source"],
        unique=False,
    )
    op.create_index(
        "ix_media_relations_subject_media_id",
        "media_relations",
        ["subject_media_id"],
        unique=False,
    )

    op.create_table(
        "graph_triples",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("triple_key", sa.String(length=96), nullable=False),
        sa.Column("subject_media_id", sa.Integer(), nullable=False),
        sa.Column("predicate", sa.String(length=80), nullable=False),
        sa.Column("object_kind", sa.String(length=32), nullable=False),
        sa.Column("object_media_id", sa.Integer(), nullable=True),
        sa.Column("object_value", sa.String(length=500), nullable=True),
        sa.Column("media_relation_id", sa.Integer(), nullable=True),
        sa.Column("provenance_episode_id", sa.Integer(), nullable=True),
        sa.Column("provenance_mention_id", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence_text", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["media_relation_id"], ["media_relations.id"]),
        sa.ForeignKeyConstraint(["object_media_id"], ["media.id"]),
        sa.ForeignKeyConstraint(["provenance_episode_id"], ["episodes.id"]),
        sa.ForeignKeyConstraint(["provenance_mention_id"], ["mentions.id"]),
        sa.ForeignKeyConstraint(["subject_media_id"], ["media.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("triple_key", name="uq_graph_triples_triple_key"),
    )
    op.create_index(
        "ix_graph_triples_media_relation_id",
        "graph_triples",
        ["media_relation_id"],
        unique=False,
    )
    op.create_index(
        "ix_graph_triples_object_kind",
        "graph_triples",
        ["object_kind"],
        unique=False,
    )
    op.create_index(
        "ix_graph_triples_object_media_id",
        "graph_triples",
        ["object_media_id"],
        unique=False,
    )
    op.create_index(
        "ix_graph_triples_object_value",
        "graph_triples",
        ["object_value"],
        unique=False,
    )
    op.create_index(
        "ix_graph_triples_predicate",
        "graph_triples",
        ["predicate"],
        unique=False,
    )
    op.create_index(
        "ix_graph_triples_provenance_episode_id",
        "graph_triples",
        ["provenance_episode_id"],
        unique=False,
    )
    op.create_index(
        "ix_graph_triples_provenance_mention_id",
        "graph_triples",
        ["provenance_mention_id"],
        unique=False,
    )
    op.create_index(
        "ix_graph_triples_source",
        "graph_triples",
        ["source"],
        unique=False,
    )
    op.create_index(
        "ix_graph_triples_subject_media_id",
        "graph_triples",
        ["subject_media_id"],
        unique=False,
    )
    op.create_index(
        "ix_graph_triples_triple_key",
        "graph_triples",
        ["triple_key"],
        unique=True,
    )


def downgrade() -> None:
    """Drop graph triple, relation, and external reference tables."""
    op.drop_index(
        "ix_graph_triples_triple_key",
        table_name="graph_triples",
        if_exists=True,
    )
    op.drop_index(
        "ix_graph_triples_subject_media_id",
        table_name="graph_triples",
        if_exists=True,
    )
    op.drop_index("ix_graph_triples_source", table_name="graph_triples", if_exists=True)
    op.drop_index(
        "ix_graph_triples_provenance_mention_id",
        table_name="graph_triples",
        if_exists=True,
    )
    op.drop_index(
        "ix_graph_triples_provenance_episode_id",
        table_name="graph_triples",
        if_exists=True,
    )
    op.drop_index(
        "ix_graph_triples_predicate",
        table_name="graph_triples",
        if_exists=True,
    )
    op.drop_index(
        "ix_graph_triples_object_value",
        table_name="graph_triples",
        if_exists=True,
    )
    op.drop_index(
        "ix_graph_triples_object_media_id",
        table_name="graph_triples",
        if_exists=True,
    )
    op.drop_index(
        "ix_graph_triples_object_kind",
        table_name="graph_triples",
        if_exists=True,
    )
    op.drop_index(
        "ix_graph_triples_media_relation_id",
        table_name="graph_triples",
        if_exists=True,
    )
    op.drop_table("graph_triples")

    op.drop_index(
        "ix_media_relations_subject_media_id",
        table_name="media_relations",
        if_exists=True,
    )
    op.drop_index(
        "ix_media_relations_source", table_name="media_relations", if_exists=True
    )
    op.drop_index(
        "ix_media_relations_relation_type",
        table_name="media_relations",
        if_exists=True,
    )
    op.drop_index(
        "ix_media_relations_relation_key",
        table_name="media_relations",
        if_exists=True,
    )
    op.drop_index(
        "ix_media_relations_provenance_episode_id",
        table_name="media_relations",
        if_exists=True,
    )
    op.drop_index(
        "ix_media_relations_object_media_id",
        table_name="media_relations",
        if_exists=True,
    )
    op.drop_table("media_relations")

    op.drop_index(
        "ix_media_external_refs_source",
        table_name="media_external_refs",
        if_exists=True,
    )
    op.drop_index(
        "ix_media_external_refs_media_id",
        table_name="media_external_refs",
        if_exists=True,
    )
    op.drop_index(
        "ix_media_external_refs_external_id",
        table_name="media_external_refs",
        if_exists=True,
    )
    op.drop_table("media_external_refs")

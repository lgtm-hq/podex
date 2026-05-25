"""Add semantic transcript chunks.

Revision ID: 015_add_semantic_chunks
Revises: 014_add_scheduler_retention_aliases
Create Date: 2026-05-22

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.types import UserDefinedType

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "015_add_semantic_chunks"
down_revision: str | None = "014_add_scheduler_retention_aliases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


class PgVectorMigrationType(UserDefinedType[str]):  # type: ignore[misc]
    """Migration-local fixed-dimension pgvector type with SQLite fallback."""

    cache_ok = True

    def __init__(
        self,
        dimensions: int,
    ) -> None:
        self.dimensions = dimensions

    def get_col_spec(
        self,
        **kwargs: object,
    ) -> str:
        """Return a generic column type for non-Postgres dialects."""
        return "TEXT"


@compiles(PgVectorMigrationType, "postgresql")  # type: ignore[untyped-decorator]
def _compile_pgvector_migration_type(
    type_: PgVectorMigrationType,
    compiler: object,
    **kwargs: object,
) -> str:
    """Compile semantic embeddings to pgvector on Postgres."""
    return f"vector({type_.dimensions})"


@compiles(PgVectorMigrationType)  # type: ignore[untyped-decorator]
def _compile_default_migration_type(
    type_: PgVectorMigrationType,
    compiler: object,
    **kwargs: object,
) -> str:
    """Compile semantic embeddings to text outside Postgres."""
    return "TEXT"


def upgrade() -> None:
    """Create semantic chunk storage."""
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "semantic_chunks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("episode_id", sa.Integer(), nullable=False),
        sa.Column("transcript_id", sa.Integer(), nullable=False),
        sa.Column("chunk_key", sa.String(length=180), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("pipeline_version", sa.String(length=80), nullable=False),
        sa.Column("source_text_hash", sa.String(length=64), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("context_snippet", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("start_seconds", sa.Integer(), nullable=True),
        sa.Column("end_seconds", sa.Integer(), nullable=True),
        sa.Column("embedding_status", sa.String(length=32), nullable=False),
        sa.Column("embedding_model", sa.String(length=120), nullable=True),
        sa.Column("embedding_dimensions", sa.Integer(), nullable=True),
        sa.Column("embedding_vector", PgVectorMigrationType(1536), nullable=True),
        sa.Column("embedding_error", sa.Text(), nullable=True),
        sa.Column("embedded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["episode_id"], ["episodes.id"]),
        sa.ForeignKeyConstraint(["transcript_id"], ["transcripts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chunk_key", name="uq_semantic_chunks_chunk_key"),
    )
    op.create_index(
        "ix_semantic_chunks_chunk_key",
        "semantic_chunks",
        ["chunk_key"],
        unique=True,
    )
    op.create_index(
        "ix_semantic_chunks_embedding_model",
        "semantic_chunks",
        ["embedding_model"],
        unique=False,
    )
    op.create_index(
        "ix_semantic_chunks_embedding_status",
        "semantic_chunks",
        ["embedding_status"],
        unique=False,
    )
    op.create_index(
        "ix_semantic_chunks_episode_id",
        "semantic_chunks",
        ["episode_id"],
        unique=False,
    )
    op.create_index(
        "ix_semantic_chunks_pipeline_version",
        "semantic_chunks",
        ["pipeline_version"],
        unique=False,
    )
    op.create_index(
        "ix_semantic_chunks_source_text_hash",
        "semantic_chunks",
        ["source_text_hash"],
        unique=False,
    )
    op.create_index(
        "ix_semantic_chunks_transcript_id",
        "semantic_chunks",
        ["transcript_id"],
        unique=False,
    )
    if bind.dialect.name == "postgresql":
        op.execute(
            "CREATE INDEX ix_semantic_chunks_embedding_vector_ivfflat "
            "ON semantic_chunks USING ivfflat "
            "(embedding_vector vector_cosine_ops) WITH (lists = 100) "
            "WHERE embedding_vector IS NOT NULL",
        )


def downgrade() -> None:
    """Drop semantic chunk storage."""
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "DROP INDEX IF EXISTS ix_semantic_chunks_embedding_vector_ivfflat",
        )
    op.drop_index(
        "ix_semantic_chunks_transcript_id",
        table_name="semantic_chunks",
        if_exists=True,
    )
    op.drop_index(
        "ix_semantic_chunks_source_text_hash",
        table_name="semantic_chunks",
        if_exists=True,
    )
    op.drop_index(
        "ix_semantic_chunks_pipeline_version",
        table_name="semantic_chunks",
        if_exists=True,
    )
    op.drop_index(
        "ix_semantic_chunks_episode_id",
        table_name="semantic_chunks",
        if_exists=True,
    )
    op.drop_index(
        "ix_semantic_chunks_embedding_status",
        table_name="semantic_chunks",
        if_exists=True,
    )
    op.drop_index(
        "ix_semantic_chunks_embedding_model",
        table_name="semantic_chunks",
        if_exists=True,
    )
    op.drop_index(
        "ix_semantic_chunks_chunk_key",
        table_name="semantic_chunks",
        if_exists=True,
    )
    op.drop_table("semantic_chunks")

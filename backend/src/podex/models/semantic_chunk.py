"""Semantic transcript chunk model."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum, auto
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from podex.models.base import Base

if TYPE_CHECKING:
    from sqlalchemy.engine.interfaces import Dialect

    from podex.models.episode import Episode
    from podex.models.transcript import Transcript


class PgVectorType(TypeDecorator[list[float] | None]):
    """Store fixed-dimension embeddings as pgvector or text JSON."""

    impl = Text
    cache_ok = True

    def __init__(
        self,
        dimensions: int,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.dimensions = dimensions

    def process_bind_param(
        self,
        value: list[float] | None,
        dialect: Dialect,
    ) -> str | None:
        """Serialize vector values for database storage.

        Args:
            value: Embedding vector or ``None``.
            dialect: SQLAlchemy dialect receiving the value.

        Returns:
            Serialized vector value accepted by pgvector and SQLite text columns.
        """
        if value is None:
            return None
        return json.dumps([float(item) for item in value], separators=(",", ":"))

    def process_result_value(
        self,
        value: object,
        dialect: Dialect,
    ) -> list[float] | None:
        """Deserialize vector values from database storage.

        Args:
            value: Raw database value.
            dialect: SQLAlchemy dialect that returned the value.

        Returns:
            Embedding vector as floats, or ``None``.
        """
        if value is None:
            return None
        if isinstance(value, list):
            return [float(item) for item in value]
        if isinstance(value, str):
            return [float(item) for item in json.loads(value)]
        return None


@compiles(PgVectorType, "postgresql")
def _compile_pgvector_type(
    type_: PgVectorType,
    compiler: object,
    **kwargs: object,
) -> str:
    """Compile semantic embeddings to a pgvector column on Postgres."""
    return f"vector({type_.dimensions})"


@compiles(PgVectorType)
def _compile_default_vector_type(
    type_: PgVectorType,
    compiler: object,
    **kwargs: object,
) -> str:
    """Compile semantic embeddings to text for local test databases."""
    return "TEXT"


class SemanticChunkEmbeddingStatus(StrEnum):
    """Lifecycle states for semantic chunk embeddings."""

    PENDING = auto()
    EMBEDDED = auto()
    FAILED = auto()


class SemanticChunk(Base):
    """Transcript window persisted as a durable semantic derivative."""

    __tablename__ = "semantic_chunks"
    __table_args__ = (
        UniqueConstraint("chunk_key", name="uq_semantic_chunks_chunk_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id"), index=True)
    transcript_id: Mapped[int] = mapped_column(
        ForeignKey("transcripts.id"),
        index=True,
    )
    chunk_key: Mapped[str] = mapped_column(String(180), unique=True, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    pipeline_version: Mapped[str] = mapped_column(String(80), index=True)
    source_text_hash: Mapped[str] = mapped_column(String(64), index=True)
    text: Mapped[str] = mapped_column(Text)
    context_snippet: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer)
    start_seconds: Mapped[int | None] = mapped_column(Integer)
    end_seconds: Mapped[int | None] = mapped_column(Integer)
    embedding_status: Mapped[str] = mapped_column(
        String(32),
        default=SemanticChunkEmbeddingStatus.PENDING.value,
        index=True,
    )
    embedding_model: Mapped[str | None] = mapped_column(String(120), index=True)
    embedding_dimensions: Mapped[int | None] = mapped_column(Integer)
    embedding_vector: Mapped[list[float] | None] = mapped_column(PgVectorType(1536))
    embedding_error: Mapped[str | None] = mapped_column(Text)
    embedded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    episode: Mapped[Episode] = relationship(back_populates="semantic_chunks")
    transcript: Mapped[Transcript] = relationship(back_populates="semantic_chunks")

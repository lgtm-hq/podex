"""Semantic transcript chunk model."""

from __future__ import annotations

import json
import struct
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum, auto
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator, TypeEngine, UserDefinedType

from podex.models.base import Base

if TYPE_CHECKING:
    from sqlalchemy.engine.interfaces import Dialect

    from podex.models.episode import Episode
    from podex.models.transcript import Transcript

EMBEDDING_DIMENSIONS = 1536
"""Dimension of the ``semantic_chunks.embedding_vector`` column (migration 0010)."""

FLOAT32_MAX: float = struct.unpack("<f", b"\xff\xff\x7f\x7f")[0]
"""Largest finite float32. pgvector stores each element as a 4-byte float and
refuses NaN, infinity and any value that overflows it."""


def storable_vector(value: object) -> list[float]:
    """Return ``value`` as floats if pgvector would store every element.

    The check applies on every dialect, so SQLite (which keeps the vector as
    text) refuses what PostgreSQL refuses. The length is not checked here; the
    database reports a vector of another dimension.

    Args:
        value: Embedding vector to bind.

    Returns:
        The elements as floats.

    Raises:
        TypeError: If ``value`` is not a list or tuple, or an element is not an
            ``int`` or ``float`` (``bool``, strings and ``None`` included).
        ValueError: If an element is NaN, infinite or outside float32.
    """
    if not isinstance(value, list | tuple):
        raise TypeError(
            f"embedding vector must be a list or tuple, got {type(value).__name__}",
        )
    for index, item in enumerate(value):
        if isinstance(item, bool) or not isinstance(item, int | float):
            raise TypeError(
                f"embedding vector element {index} must be an int or float, "
                f"got {type(item).__name__}",
            )
        # NaN fails both comparisons, so it is refused with the infinities.
        if not -FLOAT32_MAX <= item <= FLOAT32_MAX:
            raise ValueError(
                f"embedding vector element {index} must be finite in float32",
            )
    return [float(item) for item in value]


def _checked_dimensions(dimensions: object) -> int:
    """Return ``dimensions`` if it is a positive integer.

    Args:
        dimensions: Requested vector dimension.

    Returns:
        The validated dimension.

    Raises:
        TypeError: If ``dimensions`` is not an integer (``bool`` included).
        ValueError: If ``dimensions`` is not positive.
    """
    if isinstance(dimensions, bool) or not isinstance(dimensions, int):
        raise TypeError(
            f"vector dimensions must be an integer, got {type(dimensions).__name__}",
        )
    if dimensions <= 0:
        raise ValueError(f"vector dimensions must be positive, got {dimensions}")
    return dimensions


class PostgresVectorBind(UserDefinedType[list[float] | None]):
    """PostgreSQL implementation type of ``PgVectorType``.

    ``render_bind_cast`` makes the psycopg dialect, which renders a cast on
    every bound parameter, write ``%(name)s::vector(N)`` instead of the
    ``::VARCHAR`` it would render for the ``Text`` implementation, which
    PostgreSQL refuses to assign to a ``vector`` column. ``None`` therefore
    binds as ``NULL::vector(N)``. The value itself stays pgvector's text form.
    """

    cache_ok = True
    render_bind_cast = True
    # TypeEngine refuses COLLATE by raising NotImplementedError, which reads as
    # an abstract method; vectors keep that refusal unchanged.
    _with_collation = UserDefinedType._with_collation

    def __init__(self, dimensions: int) -> None:
        self.dimensions = _checked_dimensions(dimensions)

    def get_col_spec(self, **_kwargs: Any) -> str:
        """Return the pgvector type with this column's dimension."""
        return f"vector({self.dimensions})"

    def literal_processor(self, _dialect: Dialect) -> Callable[[str], str]:
        """Render a serialized vector as a quoted literal cast to this type.

        Args:
            _dialect: SQLAlchemy dialect rendering the literal.

        Returns:
            Function turning pgvector text into a ``'...'::vector(N)`` literal.
        """
        spec = self.get_col_spec()

        def process(value: str) -> str:
            escaped = value.replace("'", "''")
            return f"'{escaped}'::{spec}"

        return process


# SQLAlchemy uses process_bind_param for literal fallback when this optional
# process_literal_param hook is absent; TypeDecorator marks it abstract to pylint.
# pylint: disable-next=abstract-method
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
        self.dimensions = _checked_dimensions(dimensions)

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        """Bind as pgvector on PostgreSQL and as text everywhere else.

        Args:
            dialect: SQLAlchemy dialect the type is used with.

        Returns:
            The implementation type for ``dialect``.
        """
        if dialect.name == "postgresql":
            return PostgresVectorBind(self.dimensions)
        return super().load_dialect_impl(dialect)

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

        Raises:
            TypeError: If ``value`` or an element has a type pgvector refuses.
            ValueError: If an element is not finite in float32.
        """
        if value is None:
            return None
        return json.dumps(storable_vector(value), separators=(",", ":"))

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

        Raises:
            TypeError: If the driver returned neither text, a list nor ``None``.
        """
        if value is None:
            return None
        if isinstance(value, list):
            return [float(item) for item in value]
        if isinstance(value, str):
            return [float(item) for item in json.loads(value)]
        raise TypeError(
            f"unexpected embedding value type from the database: "
            f"{type(value).__name__}",
        )


def _compile_pgvector_type(
    type_: PgVectorType,
    _compiler: object,
    **_kwargs: object,
) -> str:
    """Compile semantic embeddings to a pgvector column on Postgres."""
    return f"vector({type_.dimensions})"


def _compile_default_vector_type(
    _type: PgVectorType,
    _compiler: object,
    **_kwargs: object,
) -> str:
    """Compile semantic embeddings to text for local test databases."""
    return "TEXT"


# Registered as plain calls: the @compiles decorator is untyped under the
# dep-less CI mypy image and would mark these functions untyped.
compiles(PgVectorType, "postgresql")(_compile_pgvector_type)
compiles(PgVectorType)(_compile_default_vector_type)


class SemanticChunkEmbeddingStatus(StrEnum):
    """Lifecycle states for semantic chunk embeddings."""

    PENDING = auto()
    EMBEDDED = auto()
    FAILED = auto()


class SemanticChunk(Base):
    """Transcript window persisted as a durable semantic derivative.

    ``embedding_vector`` and ``embedded_at`` are set only while the status is
    ``embedded``, and then ``embedding_model`` names the model that produced
    the vector. A ``failed`` chunk has neither; its model and dimensions are
    those of the failed attempt (dimensions null when not a storable integer).
    """

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
    embedding_vector: Mapped[list[float] | None] = mapped_column(
        PgVectorType(EMBEDDING_DIMENSIONS),
    )
    embedding_error: Mapped[str | None] = mapped_column(Text)
    embedded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    episode: Mapped[Episode] = relationship(back_populates="semantic_chunks")
    transcript: Mapped[Transcript] = relationship(back_populates="semantic_chunks")

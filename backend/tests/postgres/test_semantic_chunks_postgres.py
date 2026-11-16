"""Semantic chunk embeddings on the migrated PostgreSQL schema (pgvector).

The embedding column is ``vector(1536)`` here and ``TEXT`` on SQLite, so these
writes and reads only prove anything on PostgreSQL. pgvector stores each
component as a 4-byte float: values exactly representable in float32 read back
equal, others read back as the nearest float32 (compared at that precision).
"""

import struct
from collections.abc import Sequence

import pytest
from assertpy import assert_that
from sqlalchemy import select, text
from sqlalchemy.exc import DataError
from sqlalchemy.orm import Session

from podex.models import (
    Episode,
    Podcast,
    SemanticChunk,
    SemanticChunkEmbeddingStatus,
    Transcript,
)
from podex.services.semantic_chunks import (
    SemanticChunkingPolicy,
    sync_semantic_chunks_for_transcript,
)
from tests.postgres.conftest import PostgresDatabase

pytestmark = pytest.mark.postgres

DIMENSIONS = 1536


def _float32(values: Sequence[float]) -> list[float]:
    """Round each value to the nearest IEEE 754 single-precision float."""
    return [struct.unpack("f", struct.pack("f", value))[0] for value in values]


def _quarter_vector(seed: int) -> list[float]:
    """Return a full-dimension vector of quarters, exact in float32."""
    return [((seed + 1) * (index % 7) - 3) / 4.0 for index in range(DIMENSIONS)]


def _add_transcript(db: Session, *, slug: str, text_value: str = "") -> Transcript:
    """Add and flush a podcast, episode and transcript without committing."""
    podcast = Podcast(name=f"Show {slug}", slug=slug)
    db.add(podcast)
    db.flush()
    episode = Episode(podcast_id=podcast.id, title=f"Episode {slug}")
    db.add(episode)
    db.flush()
    transcript = Transcript(
        episode_id=episode.id,
        provider="podscripts",
        raw_text=text_value,
        cleaned_text=text_value,
    )
    db.add(transcript)
    db.flush()
    return transcript


def _chunk(
    transcript: Transcript,
    *,
    key: str,
    vector: list[float] | None,
) -> SemanticChunk:
    """Return an unsaved chunk of ``transcript`` with ``vector``."""
    return SemanticChunk(
        episode_id=transcript.episode_id,
        transcript_id=transcript.id,
        chunk_key=key,
        chunk_index=0,
        pipeline_version="semantic-chunk-v1",
        source_text_hash="c" * 64,
        text="alpha beta gamma",
        context_snippet="alpha beta gamma",
        token_count=3,
        embedding_vector=vector,
    )


def _stored_vector(postgres: PostgresDatabase, key: str) -> list[float] | None:
    """Read one chunk's embedding through the ORM in a fresh session."""
    with postgres.sessions() as db:
        vector: list[float] | None = db.execute(
            select(SemanticChunk.embedding_vector).where(
                SemanticChunk.chunk_key == key,
            ),
        ).scalar_one()
    return vector


# This adapter implements one protocol operation; extra methods have no purpose.
# pylint: disable-next=too-few-public-methods
class FullDimensionEmbeddingProvider:
    """Deterministic provider returning vectors of the column's dimension."""

    model_name = "synthetic-embedding-v1"
    dimensions = DIMENSIONS

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Return one float32-exact vector per text, distinct per position."""
        return [_quarter_vector(index) for index, _text in enumerate(texts)]


def test_chunk_with_null_embedding_inserts_and_reads_back_null(
    postgres: PostgresDatabase,
) -> None:
    """A chunk without an embedding inserts and stores SQL NULL."""
    with postgres.sessions() as db:
        transcript = _add_transcript(db, slug="null-embedding")
        db.add(_chunk(transcript, key="null-embedding:0", vector=None))
        db.commit()

    with postgres.engine.connect() as connection:
        is_null = connection.execute(
            text("SELECT embedding_vector IS NULL FROM semantic_chunks"),
        ).scalar_one()

    assert_that(is_null).is_true()
    assert_that(_stored_vector(postgres, "null-embedding:0")).is_none()


def test_full_dimension_embedding_round_trips_equal(
    postgres: PostgresDatabase,
) -> None:
    """A float32-exact embedding of the configured dimension reads back equal."""
    vector = _quarter_vector(2)
    with postgres.sessions() as db:
        transcript = _add_transcript(db, slug="full-embedding")
        db.add(_chunk(transcript, key="full-embedding:0", vector=vector))
        db.commit()

    with postgres.engine.connect() as connection:
        dimensions = connection.execute(
            text("SELECT vector_dims(embedding_vector) FROM semantic_chunks"),
        ).scalar_one()
        raw = connection.execute(
            text("SELECT embedding_vector FROM semantic_chunks"),
        ).scalar_one()
    stored = _stored_vector(postgres, "full-embedding:0")

    assert_that(dimensions).is_equal_to(DIMENSIONS)
    # psycopg has no loader for pgvector, so the driver returns its text form.
    assert_that(raw).is_instance_of(str)
    assert_that(raw).starts_with("[")
    assert_that(stored).is_equal_to(vector)


def test_embedding_rounds_to_float32_precision(postgres: PostgresDatabase) -> None:
    """Values beyond float32 precision read back as the nearest float32."""
    precise = [
        0.123456789012345,
        -2.718281828459045,
        1e-07,
        -3.5e-12,
        2.5e20,
        123456.789,
    ]
    vector = precise + [0.0] * (DIMENSIONS - len(precise))
    with postgres.sessions() as db:
        transcript = _add_transcript(db, slug="precise-embedding")
        db.add(_chunk(transcript, key="precise-embedding:0", vector=vector))
        db.commit()

    stored = _stored_vector(postgres, "precise-embedding:0")

    assert_that(stored).is_not_none()
    if stored is None:
        raise AssertionError
    assert_that(stored).is_length(DIMENSIONS)
    assert_that(_float32(stored)).is_equal_to(_float32(vector))
    # The database rounds: a value needing double precision does not come back.
    assert_that(stored[0]).is_not_equal_to(precise[0])


def test_embedding_of_wrong_dimension_is_rejected(
    postgres: PostgresDatabase,
) -> None:
    """The vector(1536) column refuses an embedding of another dimension."""
    with postgres.sessions() as db:
        transcript = _add_transcript(db, slug="short-embedding")
        db.commit()
        db.add(_chunk(transcript, key="short-embedding:0", vector=[0.5, 1.0, 2.0]))
        with pytest.raises(DataError) as raised:
            db.flush()
        db.rollback()

    with postgres.sessions() as db:
        assert_that(db.query(SemanticChunk).count()).is_zero()
    assert_that(str(raised.value.orig)).contains(
        f"expected {DIMENSIONS} dimensions, not 3",
    )


def test_embedding_updates_from_null_to_value_and_back(
    postgres: PostgresDatabase,
) -> None:
    """An UPDATE sets an embedding on a NULL row and clears it again."""
    vector = _quarter_vector(5)
    with postgres.sessions() as db:
        transcript = _add_transcript(db, slug="updated-embedding")
        chunk = _chunk(transcript, key="updated-embedding:0", vector=None)
        db.add(chunk)
        db.commit()

        chunk.embedding_vector = vector
        db.commit()
        after_set = _stored_vector(postgres, "updated-embedding:0")

        chunk.embedding_vector = None
        db.commit()
        after_clear = _stored_vector(postgres, "updated-embedding:0")

    assert_that(after_set).is_equal_to(vector)
    assert_that(after_clear).is_none()


def test_sync_bulk_inserts_and_embeds_chunks(postgres: PostgresDatabase) -> None:
    """The application sync writes several chunks, then embeds them in bulk."""
    words = " ".join(f"word{index}" for index in range(12))
    policy = SemanticChunkingPolicy(max_words=4, overlap_words=0, min_words=2)
    with postgres.sessions() as db:
        transcript = _add_transcript(db, slug="bulk-embedding", text_value=words)
        pending = sync_semantic_chunks_for_transcript(
            db=db,
            transcript=transcript,
            policy=policy,
        )
        db.commit()
        embedded = sync_semantic_chunks_for_transcript(
            db=db,
            transcript=transcript,
            embedding_provider=FullDimensionEmbeddingProvider(),
            policy=policy,
        )
        db.commit()

    with postgres.sessions() as db:
        rows = db.execute(
            select(
                SemanticChunk.chunk_key,
                SemanticChunk.embedding_status,
                SemanticChunk.embedding_vector,
            ).order_by(SemanticChunk.chunk_index),
        ).all()

    assert_that(pending.created_count).is_equal_to(3)
    assert_that(embedded.embedded_count).is_equal_to(3)
    assert_that([row.chunk_key for row in rows]).is_equal_to(
        list(embedded.chunk_keys),
    )
    assert_that({row.embedding_status for row in rows}).is_equal_to(
        {SemanticChunkEmbeddingStatus.EMBEDDED.value},
    )
    assert_that([row.embedding_vector for row in rows]).is_equal_to(
        [_quarter_vector(index) for index in range(3)],
    )


def test_sync_bulk_inserts_embedded_chunks_in_one_pass(
    postgres: PostgresDatabase,
) -> None:
    """New chunks with embeddings insert together through the sync."""
    words = " ".join(f"term{index}" for index in range(9))
    policy = SemanticChunkingPolicy(max_words=3, overlap_words=0, min_words=2)
    with postgres.sessions() as db:
        transcript = _add_transcript(db, slug="bulk-insert", text_value=words)
        result = sync_semantic_chunks_for_transcript(
            db=db,
            transcript=transcript,
            embedding_provider=FullDimensionEmbeddingProvider(),
            policy=policy,
        )
        db.commit()

    with postgres.sessions() as db:
        vectors = (
            db.execute(
                select(SemanticChunk.embedding_vector).order_by(
                    SemanticChunk.chunk_index,
                ),
            )
            .scalars()
            .all()
        )

    assert_that(result.created_count).is_equal_to(3)
    assert_that(result.embedded_count).is_equal_to(3)
    assert_that(list(vectors)).is_equal_to(
        [_quarter_vector(index) for index in range(3)],
    )

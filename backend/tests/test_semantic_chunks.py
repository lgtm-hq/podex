"""Tests for semantic transcript chunk derivatives."""

import array
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from unittest.mock import Mock

import pytest
from assertpy import assert_that
from sqlalchemy.orm import Session

from podex.models import (
    EMBEDDING_DIMENSIONS,
    SemanticChunk,
    SemanticChunkEmbeddingStatus,
)
from podex.services.semantic_chunks import (
    SemanticChunkingPolicy,
    build_semantic_chunk_candidates,
    sync_semantic_chunks_for_transcript,
)
from tests.semantic_chunk_support import create_transcript as _create_transcript
from tests.semantic_chunk_support import sync_and_commit


# This adapter implements one protocol operation; extra methods have no purpose.
# pylint: disable-next=too-few-public-methods
class FakeEmbeddingProvider:
    """Deterministic embedding provider for semantic chunk tests."""

    model_name = "fake-embedding-v1"
    dimensions = EMBEDDING_DIMENSIONS

    def embed_texts(
        self,
        texts: list[str],
    ) -> list[list[float]]:
        """Return stable toy vectors of the column's dimension."""
        return [
            _padded([float(index), float(len(text.split())), 1.0])
            for index, text in enumerate(texts)
        ]


# This adapter implements one protocol operation; extra methods have no purpose.
# pylint: disable-next=too-few-public-methods
class BadEmbeddingProvider:
    """Embedding provider that returns malformed vectors."""

    model_name = "bad-embedding-v1"
    dimensions = EMBEDDING_DIMENSIONS

    def embed_texts(
        self,
        texts: list[str],
    ) -> list[list[float]]:
        """Return vectors with the wrong dimensions."""
        return [[1.0, 2.0] for _text in texts]


def test_build_semantic_chunk_candidates_uses_segment_timestamps(
    db_session: Session,
) -> None:
    """Verify semantic chunks preserve transcript segment timestamp provenance."""
    transcript = _create_transcript(
        db_session=db_session,
        segments_json=[
            {"start": 0.2, "end": 5.2, "text": "alpha beta gamma"},
            {"start": 5.6, "end": 9.1, "text": "delta epsilon zeta"},
            {"start": 9.8, "end": 12.4, "text": "eta theta iota"},
        ],
    )
    policy = SemanticChunkingPolicy(max_words=6, overlap_words=3, min_words=2)

    candidates = build_semantic_chunk_candidates(
        transcript=transcript,
        policy=policy,
    )

    assert_that(candidates).is_length(2)
    assert_that(candidates[0].text).is_equal_to(
        "alpha beta gamma delta epsilon zeta",
    )
    assert_that(candidates[0].start_seconds).is_equal_to(0)
    assert_that(candidates[0].end_seconds).is_equal_to(9)
    assert_that(candidates[1].text).is_equal_to("delta epsilon zeta eta theta iota")
    assert_that(candidates[1].start_seconds).is_equal_to(6)
    assert_that(candidates[1].end_seconds).is_equal_to(12)


def test_sync_semantic_chunks_is_replay_safe(
    db_session: Session,
) -> None:
    """Verify repeated syncs avoid duplicate semantic chunks."""
    transcript = _create_transcript(
        db_session=db_session,
        cleaned_text="one two three four five six seven eight nine ten",
    )
    policy = SemanticChunkingPolicy(max_words=5, overlap_words=2, min_words=2)

    first = sync_semantic_chunks_for_transcript(
        db=db_session,
        transcript=transcript,
        policy=policy,
    )
    second = sync_semantic_chunks_for_transcript(
        db=db_session,
        transcript=transcript,
        policy=policy,
    )
    db_session.commit()

    assert_that(first.created_count).is_equal_to(3)
    assert_that(first.updated_count).is_equal_to(0)
    assert_that(second.created_count).is_equal_to(0)
    assert_that(second.updated_count).is_equal_to(0)
    assert_that(db_session.query(SemanticChunk).count()).is_equal_to(3)


def test_sync_semantic_chunks_persists_embeddings(
    db_session: Session,
) -> None:
    """Verify embedding providers populate semantic chunk vector metadata."""
    transcript = _create_transcript(
        db_session=db_session,
        cleaned_text="one two three four five six",
    )
    policy = SemanticChunkingPolicy(max_words=6, overlap_words=0, min_words=2)
    now = datetime(2026, 5, 22, tzinfo=UTC)

    result = sync_semantic_chunks_for_transcript(
        db=db_session,
        transcript=transcript,
        embedding_provider=FakeEmbeddingProvider(),
        policy=policy,
        now=now,
    )
    db_session.commit()

    chunk = db_session.query(SemanticChunk).one()
    assert_that(result.embedded_count).is_equal_to(1)
    assert_that(chunk.embedding_status).is_equal_to(
        SemanticChunkEmbeddingStatus.EMBEDDED.value,
    )
    assert_that(chunk.embedding_model).is_equal_to("fake-embedding-v1")
    assert_that(chunk.embedding_dimensions).is_equal_to(EMBEDDING_DIMENSIONS)
    assert_that(chunk.embedding_vector).is_equal_to(_padded([0.0, 6.0, 1.0]))
    embedded_at = chunk.embedded_at
    assert_that(embedded_at).is_not_none()
    if embedded_at is None:  # pragma: no cover - narrowed above
        raise AssertionError
    assert_that(embedded_at.replace(tzinfo=UTC)).is_equal_to(now)


def test_sync_semantic_chunks_marks_bad_embedding_output_failed(
    db_session: Session,
) -> None:
    """Verify malformed provider output records failure without dropping chunks."""
    transcript = _create_transcript(
        db_session=db_session,
        cleaned_text="one two three four five six",
    )
    policy = SemanticChunkingPolicy(max_words=6, overlap_words=0, min_words=2)

    result = sync_semantic_chunks_for_transcript(
        db=db_session,
        transcript=transcript,
        embedding_provider=BadEmbeddingProvider(),
        policy=policy,
    )
    db_session.commit()

    chunk = db_session.query(SemanticChunk).one()
    assert_that(result.failed_count).is_equal_to(1)
    assert_that(chunk.embedding_status).is_equal_to(
        SemanticChunkEmbeddingStatus.FAILED.value,
    )
    assert_that(chunk.embedding_error).is_equal_to(
        "embedding: PipelineFailure; embedding_dimensions_mismatch"
    )


def test_sync_semantic_chunks_deletes_stale_replay_windows(
    db_session: Session,
) -> None:
    """Verify transcript text changes remove obsolete semantic chunks."""
    transcript = _create_transcript(
        db_session=db_session,
        cleaned_text="one two three four five six seven eight nine ten",
    )
    policy = SemanticChunkingPolicy(max_words=5, overlap_words=0, min_words=2)
    sync_semantic_chunks_for_transcript(
        db=db_session,
        transcript=transcript,
        policy=policy,
    )
    transcript.cleaned_text = "alpha beta gamma delta epsilon"

    result = sync_semantic_chunks_for_transcript(
        db=db_session,
        transcript=transcript,
        policy=policy,
    )
    db_session.commit()

    chunks = db_session.query(SemanticChunk).all()
    assert_that(result.deleted_count).is_equal_to(2)
    assert_that(result.created_count).is_equal_to(1)
    assert_that(chunks).is_length(1)
    assert_that(chunks[0].text).is_equal_to("alpha beta gamma delta epsilon")


def _padded(values: list[float]) -> list[float]:
    """Pad ``values`` with zeros to the embedding column's dimension."""
    return values + [0.0] * (EMBEDDING_DIMENSIONS - len(values))


def _with_element(element: object) -> list[object]:
    """Return a column-length vector whose middle element is ``element``."""
    vector: list[object] = [0.25] * EMBEDDING_DIMENSIONS
    vector[EMBEDDING_DIMENSIONS // 2] = element
    return vector


_FLOAT32_MAX = 3.4028234663852886e38
_DIMENSIONS_MISMATCH = "embedding: PipelineFailure; embedding_dimensions_mismatch"
_INVALID_VALUE = "embedding: PipelineFailure; embedding_invalid_value"
_INVALID_TYPE = "embedding: PipelineFailure; embedding_invalid_type"

# Vectors the embedding column cannot store, each with its chunk's stored error.
_REJECTED_VECTORS: dict[str, tuple[object, str]] = {
    "one-element-short": ([0.25] * (EMBEDDING_DIMENSIONS - 1), _DIMENSIONS_MISMATCH),
    "one-element-long": ([0.25] * (EMBEDDING_DIMENSIONS + 1), _DIMENSIONS_MISMATCH),
    "provider-sized": ([0.25, 0.5, 0.75], _DIMENSIONS_MISMATCH),
    "empty": ([], _DIMENSIONS_MISMATCH),
    "text-of-column-length": ("0" * EMBEDDING_DIMENSIONS, _INVALID_TYPE),
    "array-of-column-length": (
        array.array("d", [0.25] * EMBEDDING_DIMENSIONS),
        _INVALID_TYPE,
    ),
    "generator-of-column-length": (
        (0.25 for _index in range(EMBEDDING_DIMENSIONS)),
        _INVALID_TYPE,
    ),
    "missing": (None, _INVALID_TYPE),
    "nan": (_with_element(math.nan), _INVALID_VALUE),
    "positive-infinity": (_with_element(math.inf), _INVALID_VALUE),
    "negative-infinity": (_with_element(-math.inf), _INVALID_VALUE),
    "float32-overflow": (_with_element(1e39), _INVALID_VALUE),
    "negative-float32-overflow": (_with_element(-1e39), _INVALID_VALUE),
    "integer-beyond-float": (_with_element(10**400), _INVALID_VALUE),
    "numeric-string": (_with_element("0.5"), _INVALID_VALUE),
    "boolean": (_with_element(True), _INVALID_VALUE),
    "null-element": (_with_element(None), _INVALID_VALUE),
    "complex": (_with_element(1 + 0j), _INVALID_VALUE),
}

# Storable vectors of unusual shape: a tuple of integers, and float32 limits.
_ACCEPTED_FIRST = tuple(index % 5 - 2 for index in range(EMBEDDING_DIMENSIONS))
_ACCEPTED_LAST = _padded([_FLOAT32_MAX, -_FLOAT32_MAX, 5e-324, -0.0])


@dataclass(slots=True)
class MiddleVectorEmbeddingProvider:
    """Provider returning ``middle`` for the second of three texts."""

    middle: object
    model_name: str = "middle-embedding-v1"
    dimensions: int = EMBEDDING_DIMENSIONS

    def embed_texts(self, texts: list[str]) -> list[Any]:
        """Return storable vectors around ``middle``."""
        assert_that(texts).is_length(3)
        return [_ACCEPTED_FIRST, self.middle, _ACCEPTED_LAST]


@pytest.mark.parametrize(
    ("vector", "error"),
    _REJECTED_VECTORS.values(),
    ids=_REJECTED_VECTORS.keys(),
)
def test_rejected_vector_fails_only_its_chunk(
    db_session: Session,
    vector: object,
    error: str,
) -> None:
    """A vector the column cannot store fails its chunk; the others are stored."""
    transcript = _create_transcript(
        db_session=db_session,
        cleaned_text="one two three four five six seven eight nine",
    )
    policy = SemanticChunkingPolicy(max_words=3, overlap_words=0, min_words=2)
    now = datetime(2026, 11, 17, tzinfo=UTC)

    result = sync_semantic_chunks_for_transcript(
        db=db_session,
        transcript=transcript,
        embedding_provider=MiddleVectorEmbeddingProvider(middle=vector),
        policy=policy,
        now=now,
    )
    db_session.commit()
    db_session.expire_all()

    chunks = db_session.query(SemanticChunk).order_by(SemanticChunk.chunk_index).all()
    assert_that(result.embedded_count).is_equal_to(2)
    assert_that(result.failed_count).is_equal_to(1)
    assert_that([chunk.embedding_status for chunk in chunks]).is_equal_to(
        [
            SemanticChunkEmbeddingStatus.EMBEDDED.value,
            SemanticChunkEmbeddingStatus.FAILED.value,
            SemanticChunkEmbeddingStatus.EMBEDDED.value,
        ],
    )
    assert_that([chunk.embedding_error for chunk in chunks]).is_equal_to(
        [None, error, None],
    )
    assert_that([chunk.embedding_vector for chunk in chunks]).is_equal_to(
        [[float(item) for item in _ACCEPTED_FIRST], None, _ACCEPTED_LAST],
    )
    assert_that(chunks[1].embedded_at).is_none()
    assert_that(chunks[1].embedding_model).is_equal_to("middle-embedding-v1")
    assert_that(chunks[1].embedding_dimensions).is_equal_to(EMBEDDING_DIMENSIONS)


@pytest.mark.parametrize("dimensions", [3, EMBEDDING_DIMENSIONS - 1, 3072])
def test_provider_of_another_dimension_is_never_called(
    db_session: Session,
    dimensions: int,
) -> None:
    """A provider whose dimension differs from the column fails every chunk."""
    transcript = _create_transcript(
        db_session=db_session,
        cleaned_text="one two three four five six seven eight nine",
    )
    policy = SemanticChunkingPolicy(max_words=3, overlap_words=0, min_words=2)
    provider = Mock(model_name="other-embedding-v1", dimensions=dimensions)

    result = sync_and_commit(db_session, transcript, provider=provider, policy=policy)

    chunks = db_session.query(SemanticChunk).all()
    provider.embed_texts.assert_not_called()
    assert_that(result.created_count).is_equal_to(3)
    assert_that(result.failed_count).is_equal_to(3)
    assert_that(result.embedded_count).is_zero()
    assert_that({chunk.embedding_status for chunk in chunks}).is_equal_to(
        {SemanticChunkEmbeddingStatus.FAILED.value},
    )
    assert_that({chunk.embedding_error for chunk in chunks}).is_equal_to(
        {_DIMENSIONS_MISMATCH},
    )
    assert_that({chunk.embedding_dimensions for chunk in chunks}).is_equal_to(
        {dimensions},
    )
    assert_that([chunk.embedding_vector for chunk in chunks]).is_equal_to(
        [None, None, None],
    )

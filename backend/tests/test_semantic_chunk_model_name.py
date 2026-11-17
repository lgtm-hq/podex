"""Embedding provider model names the ``embedding_model`` column cannot store.

``embedding_model`` is ``String(120)``. A provider whose model name is not a
non-empty string of at most 120 characters without control characters is
refused before it is called, like a provider with an unstorable dimension.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from operator import attrgetter
from unittest.mock import Mock

import pytest
from assertpy import assert_that
from sqlalchemy.orm import Session

from podex.models import (
    EMBEDDING_DIMENSIONS,
    SemanticChunk,
    SemanticChunkEmbeddingStatus,
    Transcript,
)
from podex.services.semantic_chunks import SemanticChunkingPolicy
from tests.sdk_log_support import logged, synthetic_secret
from tests.semantic_chunk_support import create_transcript, sync_and_commit

_INVALID_MODEL_NAME = "embedding: PipelineFailure; embedding_invalid_model_name"
_MODEL_COLUMN_LENGTH = 120
_COLUMN_VECTOR = [0.25] * EMBEDDING_DIMENSIONS
_POLICY = SemanticChunkingPolicy(max_words=3, overlap_words=0, min_words=2)

_embedding_columns = attrgetter(
    "embedding_status",
    "embedding_error",
    "embedding_model",
    "embedding_dimensions",
    "embedding_vector",
    "embedded_at",
)

# Model names the column cannot store, each built around a planted secret.
_REJECTED_NAMES: dict[str, Callable[[str], object]] = {
    "none": lambda _secret: None,
    "list": lambda secret: [secret],
    "bytes": lambda secret: secret.encode(),
    "empty": lambda _secret: "",
    "whitespace-only": lambda _secret: "   ",
    "one-over-length": lambda secret: secret.ljust(_MODEL_COLUMN_LENGTH + 1, "m"),
    "newline": lambda secret: f"{secret}\nmodel",
    "escape": lambda secret: f"{secret}\x1b[0m",
    "delete": lambda secret: f"{secret}\x7f",
    "c1-control": lambda secret: f"{secret}\x85",
    "nul": lambda secret: f"{secret}\x00",
    "lone-surrogate": lambda secret: f"{secret}\ud800",
}


def _provider(model_name: object) -> Mock:
    """Return a column-dimension provider named ``model_name``."""
    return Mock(
        model_name=model_name,
        dimensions=EMBEDDING_DIMENSIONS,
        embed_texts=Mock(side_effect=lambda texts: [_COLUMN_VECTOR] * len(texts)),
    )


def _embedded_transcript(db_session: Session) -> Transcript:
    """Create a two-chunk transcript whose chunks are already embedded."""
    transcript = create_transcript(
        db_session=db_session,
        cleaned_text="one two three four five six",
    )
    first = sync_and_commit(
        db_session,
        transcript,
        provider=_provider("first-embedding-v1"),
        policy=_POLICY,
        now=datetime(2026, 11, 1, tzinfo=UTC),
    )
    assert_that(first.embedded_count).is_equal_to(2)
    return transcript


@pytest.mark.parametrize(
    "build",
    _REJECTED_NAMES.values(),
    ids=_REJECTED_NAMES.keys(),
)
def test_unstorable_model_name_is_refused_and_not_recorded(
    db_session: Session,
    caplog: pytest.LogCaptureFixture,
    build: Callable[[str], object],
) -> None:
    """The provider is never called; each chunk fails without a model name.

    The name is checked before ``dimensions`` is read, so neither is recorded.
    """
    secret = synthetic_secret("embedding-model-name")
    transcript = _embedded_transcript(db_session)
    provider = _provider(build(secret))

    with caplog.at_level(logging.DEBUG):
        result = sync_and_commit(
            db_session,
            transcript,
            provider=provider,
            policy=_POLICY,
        )
    db_session.expire_all()

    chunks = db_session.query(SemanticChunk).all()
    provider.embed_texts.assert_not_called()
    assert_that(result.embedded_count).is_zero()
    assert_that(result.failed_count).is_equal_to(2)
    failed = (SemanticChunkEmbeddingStatus.FAILED.value, _INVALID_MODEL_NAME)
    assert_that([_embedding_columns(chunk) for chunk in chunks]).is_equal_to(
        [(*failed, None, None, None, None)] * 2,
    )
    assert_that(logged(caplog)).does_not_contain(secret)
    assert_that(logged(caplog)).contains(_INVALID_MODEL_NAME)


@pytest.mark.parametrize(
    "model_name",
    ["m" * _MODEL_COLUMN_LENGTH, "é" * _MODEL_COLUMN_LENGTH, " spaced model "],
    ids=["column-length", "column-length-non-ascii", "inner-and-outer-spaces"],
)
def test_storable_model_name_is_embedded_and_recorded(
    db_session: Session,
    model_name: str,
) -> None:
    """A name the column holds, up to exactly its length, labels the vectors."""
    transcript = create_transcript(
        db_session=db_session,
        cleaned_text="one two three four five six",
    )

    result = sync_and_commit(
        db_session,
        transcript,
        provider=_provider(model_name),
        policy=_POLICY,
    )
    db_session.expire_all()

    chunks = db_session.query(SemanticChunk).all()
    assert_that(result.embedded_count).is_equal_to(2)
    assert_that(
        {
            (chunk.embedding_status, chunk.embedding_model, chunk.embedding_error)
            for chunk in chunks
        },
    ).is_equal_to(
        {(SemanticChunkEmbeddingStatus.EMBEDDED.value, model_name, None)},
    )

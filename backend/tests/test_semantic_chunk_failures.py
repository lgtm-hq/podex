"""Embedding failures of the semantic chunk sync.

How a failed embed is recorded, which provider errors count as failures, and
what a later sync without a provider leaves in place (ADR 0018).
"""

import asyncio
import logging
import math
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import Mock

import httpx
import pytest
from assertpy import assert_that
from sqlalchemy.orm import Session

from podex.models import (
    EMBEDDING_DIMENSIONS,
    ContentSuppressedError,
    ContentSuppressionScope,
    SemanticChunk,
    SemanticChunkEmbeddingStatus,
    Transcript,
)
from podex.services import semantic_chunks
from podex.services.semantic_chunks import (
    EmbeddingProvider,
    SemanticChunkingPolicy,
    sync_semantic_chunks_for_transcript,
)
from tests.sdk_log_support import logged, synthetic_secret
from tests.semantic_chunk_support import create_transcript as _create_transcript
from tests.semantic_chunk_support import sync_and_commit

_DIMENSIONS_MISMATCH = "embedding: PipelineFailure; embedding_dimensions_mismatch"
_INVALID_VALUE = "embedding: PipelineFailure; embedding_invalid_value"
_COUNT_MISMATCH = "embedding: PipelineFailure; embedding_count_mismatch"


def _with_element(element: object) -> list[object]:
    """Return a column-length vector whose middle element is ``element``."""
    vector: list[object] = [0.25] * EMBEDDING_DIMENSIONS
    vector[EMBEDDING_DIMENSIONS // 2] = element
    return vector


_FIRST_MODEL = "first-embedding-v1"
_FAILING_MODEL = "failing-embedding-v2"


@dataclass(slots=True)
class FailingEmbeddingProvider:
    """Provider whose ``embed_texts`` result comes from ``respond``."""

    respond: Callable[[list[str]], Any]
    model_name: str = _FAILING_MODEL
    dimensions: Any = EMBEDDING_DIMENSIONS

    def embed_texts(self, texts: list[str]) -> Any:
        """Return whatever ``respond`` produces for ``texts``."""
        return self.respond(texts)


def _raise(error: BaseException) -> Callable[[list[str]], list[Any]]:
    """Return a ``respond`` callable that raises ``error``."""

    def respond(_texts: list[str]) -> list[Any]:
        raise error

    return respond


def _vectors(count_delta: int, vector: object) -> Callable[[list[str]], list[Any]]:
    """Return a ``respond`` callable giving ``len(texts) + count_delta`` vectors."""

    def respond(texts: list[str]) -> list[Any]:
        return [vector] * (len(texts) + count_delta)

    return respond


_COLUMN_VECTOR = [0.25] * EMBEDDING_DIMENSIONS

# Ways a re-embed can fail, each with the error every chunk then stores.
_REEMBED_FAILURES: dict[str, tuple[FailingEmbeddingProvider, str]] = {
    "provider-error": (
        FailingEmbeddingProvider(respond=_raise(OSError("unreachable"))),
        "embedding: OSError; request_failed",
    ),
    "vector-count": (
        FailingEmbeddingProvider(respond=_vectors(-1, _COLUMN_VECTOR)),
        _COUNT_MISMATCH,
    ),
    "declared-dimension": (
        FailingEmbeddingProvider(respond=_vectors(0, _COLUMN_VECTOR), dimensions=3),
        _DIMENSIONS_MISMATCH,
    ),
    "rejected-vector": (
        FailingEmbeddingProvider(respond=_vectors(0, _with_element(math.nan))),
        _INVALID_VALUE,
    ),
}


@pytest.mark.parametrize(
    ("provider", "error"),
    _REEMBED_FAILURES.values(),
    ids=_REEMBED_FAILURES.keys(),
)
def test_failed_reembed_keeps_no_vector_under_the_new_model(
    db_session: Session,
    provider: FailingEmbeddingProvider,
    error: str,
) -> None:
    """A failed re-embed clears the previous vector and its embedding time."""
    transcript = _create_transcript(
        db_session=db_session,
        cleaned_text="one two three four five six",
    )
    policy = SemanticChunkingPolicy(max_words=3, overlap_words=0, min_words=2)
    first = FailingEmbeddingProvider(
        respond=_vectors(0, _COLUMN_VECTOR),
        model_name=_FIRST_MODEL,
    )
    embedded = sync_semantic_chunks_for_transcript(
        db=db_session,
        transcript=transcript,
        embedding_provider=first,
        policy=policy,
        now=datetime(2026, 11, 1, tzinfo=UTC),
    )
    db_session.commit()
    db_session.expire_all()
    before = db_session.query(SemanticChunk).all()
    assert_that(embedded.embedded_count).is_equal_to(2)
    assert_that(
        [(chunk.embedding_model, chunk.embedding_vector) for chunk in before],
    ).is_equal_to([(_FIRST_MODEL, _COLUMN_VECTOR)] * 2)
    assert_that([chunk.embedded_at for chunk in before]).does_not_contain(None)

    result = sync_and_commit(
        db_session,
        transcript,
        provider=provider,
        policy=policy,
        now=datetime(2026, 11, 17, tzinfo=UTC),
    )
    db_session.expire_all()

    chunks = db_session.query(SemanticChunk).all()
    assert_that(result.failed_count).is_equal_to(2)
    assert_that(result.embedded_count).is_zero()
    assert_that(
        [
            (
                chunk.embedding_status,
                chunk.embedding_model,
                chunk.embedding_error,
                chunk.embedding_vector,
                chunk.embedded_at,
            )
            for chunk in chunks
        ],
    ).is_equal_to(
        [
            (
                SemanticChunkEmbeddingStatus.FAILED.value,
                _FAILING_MODEL,
                error,
                None,
                None,
            ),
        ]
        * 2,
    )


def _http_status_error(secret: str) -> Exception:
    """Return an HTTP client status error whose message carries ``secret``."""
    request = httpx.Request("POST", f"https://embeddings.invalid/{secret}")
    response = httpx.Response(503, request=request, text=secret)
    error: Exception = httpx.HTTPStatusError(
        secret,
        request=request,
        response=response,
    )
    return error


def _connect_error(secret: str) -> Exception:
    """Return an HTTP client transport error whose message carries ``secret``."""
    error: Exception = httpx.ConnectError(secret)
    return error


class ProviderSdkError(Exception):
    """Error type of a provider client library, outside the builtin hierarchy."""


# Exceptions a provider can raise, each with the descriptor stored for it.
_PROVIDER_ERRORS: dict[str, tuple[Callable[[str], Exception], str]] = {
    "os-error": (OSError, "embedding: OSError; request_failed"),
    "timeout": (TimeoutError, "embedding: TimeoutError; request_failed"),
    "http-status": (
        _http_status_error,
        "embedding: HTTPStatusError (HTTP 503); request_failed",
    ),
    "http-transport": (_connect_error, "embedding: ConnectError; request_failed"),
    "key-error": (KeyError, "embedding: KeyError; request_failed"),
    "client-library": (
        ProviderSdkError,
        "embedding: ProviderSdkError; request_failed",
    ),
    "runtime-error": (RuntimeError, "embedding: RuntimeError; request_failed"),
}


@pytest.mark.parametrize(
    ("build", "descriptor"),
    _PROVIDER_ERRORS.values(),
    ids=_PROVIDER_ERRORS.keys(),
)
def test_any_provider_exception_fails_the_batch(
    db_session: Session,
    caplog: pytest.LogCaptureFixture,
    build: Callable[[str], Exception],
    descriptor: str,
) -> None:
    """Every provider exception fails each chunk with a bounded descriptor."""
    secret = synthetic_secret("embedding-provider")
    transcript = _create_transcript(
        db_session=db_session,
        cleaned_text="one two three four five six",
    )
    policy = SemanticChunkingPolicy(max_words=3, overlap_words=0, min_words=2)

    with caplog.at_level(logging.DEBUG):
        result = sync_semantic_chunks_for_transcript(
            db=db_session,
            transcript=transcript,
            embedding_provider=FailingEmbeddingProvider(respond=_raise(build(secret))),
            policy=policy,
        )
    db_session.commit()

    chunks = db_session.query(SemanticChunk).all()
    assert_that(result.created_count).is_equal_to(2)
    assert_that(result.failed_count).is_equal_to(2)
    assert_that(
        {(chunk.embedding_status, chunk.embedding_error) for chunk in chunks},
    ).is_equal_to({(SemanticChunkEmbeddingStatus.FAILED.value, descriptor)})
    assert_that(logged(caplog)).does_not_contain(secret)
    assert_that(logged(caplog)).contains(descriptor)


@pytest.mark.parametrize(
    "error",
    [
        KeyboardInterrupt(),
        SystemExit(1),
        GeneratorExit(),
        asyncio.CancelledError(),
        BaseExceptionGroup("provider stopped", [KeyboardInterrupt()]),
    ],
    ids=[
        "keyboard-interrupt",
        "system-exit",
        "generator-exit",
        "cancelled",
        "base-exception-group",
    ],
)
def test_interpreter_exits_from_the_provider_propagate(
    db_session: Session,
    error: BaseException,
) -> None:
    """Interrupts and exits are not embedding failures; the sync stops."""
    transcript = _create_transcript(
        db_session=db_session,
        cleaned_text="one two three four five six",
    )

    with pytest.raises(type(error)):
        sync_semantic_chunks_for_transcript(
            db=db_session,
            transcript=transcript,
            embedding_provider=FailingEmbeddingProvider(respond=_raise(error)),
        )


@pytest.mark.parametrize(
    "dimensions",
    [10**20, 2**31, -(2**31) - 1, "1536", "many", 1536.0, None, object()],
    ids=[
        "huge",
        "int4-overflow",
        "int4-underflow",
        "numeric-text",
        "text",
        "float",
        "none",
        "object",
    ],
)
def test_unstorable_declared_dimension_is_refused_and_not_recorded(
    db_session: Session,
    dimensions: object,
) -> None:
    """A declared dimension the integer column cannot hold is never written."""
    transcript = _create_transcript(
        db_session=db_session,
        cleaned_text="one two three four five six",
    )
    policy = SemanticChunkingPolicy(max_words=3, overlap_words=0, min_words=2)
    provider = Mock(model_name="odd-embedding-v1", dimensions=dimensions)

    result = sync_and_commit(db_session, transcript, provider=provider, policy=policy)
    db_session.expire_all()

    chunks = db_session.query(SemanticChunk).all()
    provider.embed_texts.assert_not_called()
    assert_that(result.failed_count).is_equal_to(2)
    assert_that(
        {
            (
                chunk.embedding_status,
                chunk.embedding_error,
                chunk.embedding_model,
                chunk.embedding_dimensions,
            )
            for chunk in chunks
        },
    ).is_equal_to(
        {
            (
                SemanticChunkEmbeddingStatus.FAILED.value,
                _DIMENSIONS_MISMATCH,
                "odd-embedding-v1",
                None,
            ),
        },
    )


@pytest.mark.parametrize("count_delta", [-1, 1, -2], ids=["fewer", "more", "none"])
def test_wrong_vector_count_fails_the_whole_batch(
    db_session: Session,
    count_delta: int,
) -> None:
    """Too few or too many vectors fail every chunk; none is stored."""
    transcript = _create_transcript(
        db_session=db_session,
        cleaned_text="one two three four five six",
    )
    policy = SemanticChunkingPolicy(max_words=3, overlap_words=0, min_words=2)

    result = sync_semantic_chunks_for_transcript(
        db=db_session,
        transcript=transcript,
        embedding_provider=FailingEmbeddingProvider(
            respond=_vectors(count_delta, _COLUMN_VECTOR),
        ),
        policy=policy,
    )
    db_session.commit()

    chunks = db_session.query(SemanticChunk).all()
    assert_that(result.embedded_count).is_zero()
    assert_that(result.failed_count).is_equal_to(2)
    assert_that(
        {
            (chunk.embedding_status, chunk.embedding_error, chunk.embedded_at)
            for chunk in chunks
        },
    ).is_equal_to({(SemanticChunkEmbeddingStatus.FAILED.value, _COUNT_MISMATCH, None)})
    assert_that([chunk.embedding_vector for chunk in chunks]).is_equal_to([None, None])


def _two_chunk_transcript(
    db_session: Session,
) -> tuple[Transcript, SemanticChunkingPolicy]:
    """Create a two-chunk transcript and return it with its policy."""
    transcript = _create_transcript(
        db_session=db_session,
        cleaned_text="one two three four five six",
    )
    return transcript, SemanticChunkingPolicy(
        max_words=3,
        overlap_words=0,
        min_words=2,
    )


def test_content_suppression_raised_by_the_provider_propagates(
    db_session: Session,
) -> None:
    """A takedown refusal is never recorded as an embedding failure."""
    transcript, policy = _two_chunk_transcript(db_session)
    refusal = ContentSuppressedError(
        scope=ContentSuppressionScope.DERIVATIVES,
        episode_id=transcript.episode_id,
    )

    with pytest.raises(ContentSuppressedError):
        sync_semantic_chunks_for_transcript(
            db=db_session,
            transcript=transcript,
            embedding_provider=FailingEmbeddingProvider(respond=_raise(refusal)),
            policy=policy,
        )


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("defect in our own validation"),
        ContentSuppressedError(
            scope=ContentSuppressionScope.DERIVATIVES,
            episode_id=1,
        ),
    ],
    ids=["defect", "content-suppressed"],
)
@pytest.mark.parametrize(
    "own_step",
    ["_check_provider_dimensions", "_validate_embedding_count"],
)
def test_errors_from_our_own_checks_are_not_provider_failures(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    own_step: str,
    error: Exception,
) -> None:
    """Only the provider's own code is guarded; our checks raise unchanged."""
    transcript, policy = _two_chunk_transcript(db_session)

    def fail(**_kwargs: object) -> None:
        raise error

    def fail_positional(_dimensions: object) -> None:
        raise error

    monkeypatch.setattr(
        semantic_chunks,
        own_step,
        fail_positional if own_step == "_check_provider_dimensions" else fail,
    )

    with pytest.raises(type(error)):
        sync_semantic_chunks_for_transcript(
            db=db_session,
            transcript=transcript,
            embedding_provider=FailingEmbeddingProvider(
                respond=_vectors(0, _COLUMN_VECTOR),
            ),
            policy=policy,
        )


class RaisingModelNameProvider:
    """Provider whose ``model_name`` property raises."""

    dimensions = EMBEDDING_DIMENSIONS

    @property
    def model_name(self) -> str:
        """Fail as a misconfigured client would."""
        raise OSError("model name unavailable")

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Return storable vectors; never reached."""
        return [_COLUMN_VECTOR for _text in texts]


class RaisingDimensionsProvider:
    """Provider whose ``dimensions`` property raises."""

    model_name = "raising-dimensions-v1"

    @property
    def dimensions(self) -> int:
        """Fail as a misconfigured client would."""
        raise OSError("dimensions unavailable")

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Return storable vectors; never reached."""
        return [_COLUMN_VECTOR for _text in texts]


@pytest.mark.parametrize(
    ("provider", "model_name"),
    [
        (RaisingModelNameProvider(), None),
        (RaisingDimensionsProvider(), "raising-dimensions-v1"),
    ],
    ids=["model-name", "dimensions"],
)
def test_provider_attribute_errors_fail_the_batch(
    db_session: Session,
    provider: object,
    model_name: str | None,
) -> None:
    """A provider attribute that raises fails every chunk; the sync returns."""
    transcript, policy = _two_chunk_transcript(db_session)

    result = sync_and_commit(
        db_session,
        transcript,
        provider=cast(EmbeddingProvider, provider),
        policy=policy,
    )

    chunks = db_session.query(SemanticChunk).all()
    assert_that(result.failed_count).is_equal_to(2)
    assert_that(
        {
            (
                chunk.embedding_status,
                chunk.embedding_error,
                chunk.embedding_model,
                chunk.embedding_dimensions,
            )
            for chunk in chunks
        },
    ).is_equal_to(
        {
            (
                SemanticChunkEmbeddingStatus.FAILED.value,
                "embedding: OSError; request_failed",
                model_name,
                None,
            ),
        },
    )


def test_provider_result_failing_while_read_fails_the_batch(
    db_session: Session,
) -> None:
    """A result that raises part-way through iteration fails every chunk."""
    transcript, policy = _two_chunk_transcript(db_session)

    def respond(_texts: list[str]) -> Iterator[list[float]]:
        yield _COLUMN_VECTOR
        raise OSError("stream reset")

    result = sync_and_commit(
        db_session,
        transcript,
        provider=FailingEmbeddingProvider(respond=respond),
        policy=policy,
    )

    chunks = db_session.query(SemanticChunk).all()
    assert_that(result.failed_count).is_equal_to(2)
    assert_that(
        {(chunk.embedding_status, chunk.embedding_error) for chunk in chunks},
    ).is_equal_to(
        {
            (
                SemanticChunkEmbeddingStatus.FAILED.value,
                "embedding: OSError; request_failed",
            ),
        },
    )


class OverreadError(Exception):
    """Raised when the sync reads further into a result than it needs."""


def test_unbounded_provider_result_is_read_one_past_the_expected_count(
    db_session: Session,
) -> None:
    """An endless result stops one vector past the texts and fails the count."""
    transcript, policy = _two_chunk_transcript(db_session)
    produced: list[int] = []

    def respond(texts: list[str]) -> Iterator[list[float]]:
        while True:
            if len(produced) > len(texts) + 1:
                raise OverreadError
            produced.append(len(produced))
            yield _COLUMN_VECTOR

    result = sync_and_commit(
        db_session,
        transcript,
        provider=FailingEmbeddingProvider(respond=respond),
        policy=policy,
    )

    chunks = db_session.query(SemanticChunk).all()
    assert_that(produced).is_length(3)
    assert_that(result.failed_count).is_equal_to(2)
    assert_that({chunk.embedding_error for chunk in chunks}).is_equal_to(
        {_COUNT_MISMATCH},
    )


def test_pending_resync_keeps_the_last_successful_embedding(
    db_session: Session,
) -> None:
    """A sync without a provider marks chunks pending and keeps their vector.

    The vector stays labelled with the model, dimension and time of the embed
    that produced it.
    """
    transcript, policy = _two_chunk_transcript(db_session)
    embedded_at = datetime(2026, 11, 1, tzinfo=UTC)
    sync_semantic_chunks_for_transcript(
        db=db_session,
        transcript=transcript,
        embedding_provider=FailingEmbeddingProvider(
            respond=_vectors(0, _COLUMN_VECTOR),
            model_name=_FIRST_MODEL,
        ),
        policy=policy,
        now=embedded_at,
    )
    db_session.commit()

    result = sync_and_commit(db_session, transcript, provider=None, policy=policy)
    db_session.expire_all()

    chunks = db_session.query(SemanticChunk).all()
    assert_that(result.embedded_count).is_zero()
    assert_that(
        [
            (
                chunk.embedding_status,
                chunk.embedding_error,
                chunk.embedding_model,
                chunk.embedding_dimensions,
                chunk.embedding_vector,
                (
                    None
                    if chunk.embedded_at is None
                    else chunk.embedded_at.replace(tzinfo=UTC)
                ),
            )
            for chunk in chunks
        ],
    ).is_equal_to(
        [
            (
                SemanticChunkEmbeddingStatus.PENDING.value,
                None,
                _FIRST_MODEL,
                EMBEDDING_DIMENSIONS,
                _COLUMN_VECTOR,
                embedded_at,
            ),
        ]
        * 2,
    )


def test_pending_resync_of_a_failed_chunk_still_has_no_vector(
    db_session: Session,
) -> None:
    """A failed chunk synced again without a provider stays without a vector."""
    transcript, policy = _two_chunk_transcript(db_session)
    sync_semantic_chunks_for_transcript(
        db=db_session,
        transcript=transcript,
        embedding_provider=FailingEmbeddingProvider(
            respond=_raise(OSError("unreachable")),
        ),
        policy=policy,
    )
    db_session.commit()

    sync_and_commit(db_session, transcript, provider=None, policy=policy)
    db_session.expire_all()

    chunks = db_session.query(SemanticChunk).all()
    assert_that(
        {
            (chunk.embedding_status, chunk.embedding_vector, chunk.embedded_at)
            for chunk in chunks
        },
    ).is_equal_to({(SemanticChunkEmbeddingStatus.PENDING.value, None, None)})

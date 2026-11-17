"""Semantic chunk generation and persistence services."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from itertools import islice
from typing import Any, Protocol, TypeVar

from sqlalchemy.orm import Session

from podex.models import (
    EMBEDDING_DIMENSIONS,
    ContentSuppressedError,
    SemanticChunk,
    SemanticChunkEmbeddingStatus,
    Transcript,
)
from podex.models.semantic_chunk import storable_vector
from podex.services.content_suppression import authorize_episode_text
from podex.services.failure_codes import FailureReason, PipelineFailure
from podex.services.failure_descriptors import (
    describe_failure,
    log_failure,
    persisted_failure,
)

logger = logging.getLogger(__name__)

# ``embedding_dimensions`` is a PostgreSQL ``integer`` (int4) column.
_INT4_MIN = -(2**31)
_INT4_MAX = 2**31 - 1

_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class SemanticChunkingPolicy:
    """Policy controlling transcript semantic windows."""

    max_words: int = 220
    overlap_words: int = 40
    min_words: int = 20
    context_words: int = 24
    pipeline_version: str = "semantic-chunk-v1"

    def __post_init__(self) -> None:
        """Validate chunking thresholds."""
        if self.max_words <= 0:
            raise ValueError("max_words must be positive")
        if self.overlap_words < 0:
            raise ValueError("overlap_words must be non-negative")
        if self.overlap_words >= self.max_words:
            raise ValueError("overlap_words must be less than max_words")
        if self.min_words <= 0:
            raise ValueError("min_words must be positive")
        if self.context_words < 0:
            raise ValueError("context_words must be non-negative")
        if not self.pipeline_version:
            raise ValueError("pipeline_version must be non-empty")


@dataclass(frozen=True, slots=True)
# This flat data record mirrors its published fields; nesting changes the contract.
# pylint: disable-next=too-many-instance-attributes
class SemanticChunkCandidateData:
    """Candidate semantic chunk derived from transcript text."""

    chunk_index: int
    chunk_key: str
    source_text_hash: str
    text: str
    context_snippet: str
    token_count: int
    start_seconds: int | None
    end_seconds: int | None
    pipeline_version: str


@dataclass(frozen=True, slots=True)
class SemanticChunkSyncResultData:
    """Summary of persisted semantic chunk sync work."""

    created_count: int
    updated_count: int
    deleted_count: int
    embedded_count: int
    failed_count: int
    chunk_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TranscriptSegmentData:
    """Normalized transcript segment used for timestamped chunking."""

    text: str
    start_seconds: int | None = None
    end_seconds: int | None = None


class EmbeddingProvider(Protocol):
    """Protocol for model-backed embedding providers.

    ``dimensions`` must equal the embedding column's dimension
    (``EMBEDDING_DIMENSIONS``): a provider declaring any other value is never
    called, and every chunk it would embed is marked failed.

    Each vector must be a ``list`` or ``tuple`` of exactly
    ``EMBEDDING_DIMENSIONS`` Python ``int`` or ``float`` values, each finite in
    float32. Adapters convert client-library types themselves (for example a
    NumPy array with ``.tolist()``); any other vector type fails its chunk with
    ``embedding_invalid_type``, whatever its length.

    Any ``Exception`` raised while reading these attributes, embedding, or
    iterating the result fails the whole batch (ADR 0018). Only the exception's
    class (and a validated HTTP status) is recorded, never its message.
    ``ContentSuppressedError``, interrupts, exits and cancellation propagate.
    The result is read at most one vector past the number of texts. Each call
    must be bounded in time (ADR 0015).
    """

    model_name: str
    dimensions: int

    def embed_texts(
        self,
        texts: list[str],
    ) -> list[list[float]]:
        """Embed transcript chunk text.

        Args:
            texts: Chunk text values in request order.

        Returns:
            Embedding vectors in the same order as ``texts``, one per text.
        """


def build_semantic_chunk_candidates(
    *,
    transcript: Transcript,
    policy: SemanticChunkingPolicy | None = None,
) -> list[SemanticChunkCandidateData]:
    """Build semantic chunk candidates from a transcript.

    Args:
        transcript: Transcript source row.
        policy: Chunking policy.

    Returns:
        Deterministic chunk candidates with stable idempotency keys.
    """
    effective_policy = policy or SemanticChunkingPolicy()
    source_segments = _segments_from_transcript(transcript)
    if source_segments:
        return _candidates_from_segments(
            transcript=transcript,
            segments=source_segments,
            policy=effective_policy,
        )

    source_text = _transcript_text(transcript)
    if not source_text:
        return []
    return _candidates_from_words(
        transcript=transcript,
        words=source_text.split(),
        policy=effective_policy,
    )


def sync_semantic_chunks_for_transcript(
    *,
    db: Session,
    transcript: Transcript,
    embedding_provider: EmbeddingProvider | None = None,
    policy: SemanticChunkingPolicy | None = None,
    now: datetime | None = None,
) -> SemanticChunkSyncResultData:
    """Persist semantic chunks and optional embeddings for a transcript.

    Args:
        db: Database session.
        transcript: Transcript source row.
        embedding_provider: Optional provider used to generate embeddings.
        policy: Chunking policy.
        now: Timestamp used for deterministic embedded timestamps.

    Returns:
        Summary of created, updated, deleted, and embedded chunks. A vector the
        embedding column cannot store marks only its own chunk failed; it is
        never sent to the database. A failed embed clears the chunk's vector
        and ``embedded_at``; without a provider, chunks become ``pending`` and
        keep their last successful embedding (see ``SemanticChunk``).

    Raises:
        ContentSuppressedError: If an approved takedown suppresses the
            episode's derivatives; nothing is chunked or embedded. The check
            holds the takedown lock until this transaction ends.
    """
    authorize_episode_text(db=db, episode_ids=[transcript.episode_id])
    now = now or datetime.now(UTC)
    policy = policy or SemanticChunkingPolicy()
    candidates = build_semantic_chunk_candidates(
        transcript=transcript,
        policy=policy,
    )
    existing_chunks = {
        chunk.chunk_key: chunk
        for chunk in db.query(SemanticChunk)
        .filter(SemanticChunk.transcript_id == transcript.id)
        .filter(SemanticChunk.pipeline_version == policy.pipeline_version)
        .all()
    }
    created_count = 0
    updated_count = 0
    deleted_count = _remove_stale_chunks(
        db=db,
        existing=existing_chunks,
        candidates=candidates,
    )

    chunks_to_embed: list[SemanticChunk] = []
    for candidate in candidates:
        chunk = existing_chunks.get(candidate.chunk_key)
        if chunk is None:
            chunk = SemanticChunk(
                episode_id=transcript.episode_id,
                transcript_id=transcript.id,
                chunk_key=candidate.chunk_key,
            )
            db.add(chunk)
            created_count += 1
        else:
            updated_count += int(_semantic_chunk_needs_update(chunk, candidate))

        _apply_candidate(chunk=chunk, candidate=candidate)
        if embedding_provider is None:
            chunk.embedding_status = SemanticChunkEmbeddingStatus.PENDING.value
            chunk.embedding_error = None
            continue
        chunks_to_embed.append(chunk)

    embedded_count, failed_count = _embed_chunks(
        chunks=chunks_to_embed,
        provider=embedding_provider,
        now=now,
    )

    db.flush()
    return SemanticChunkSyncResultData(
        created_count=created_count,
        updated_count=updated_count,
        deleted_count=deleted_count,
        embedded_count=embedded_count,
        failed_count=failed_count,
        chunk_keys=tuple(candidate.chunk_key for candidate in candidates),
    )


def _remove_stale_chunks(
    *,
    db: Session,
    existing: dict[str, SemanticChunk],
    candidates: list[SemanticChunkCandidateData],
) -> int:
    """Remove stored chunks absent from the current candidate set.

    Args:
        db: Session containing the stored chunks.
        existing: Existing chunks indexed by their persisted identity.
        candidates: Current transcript candidates.

    Returns:
        The number of deleted chunks.
    """
    candidate_keys = {candidate.chunk_key for candidate in candidates}
    deleted = 0
    for key, chunk in existing.items():
        if key not in candidate_keys:
            db.delete(chunk)
            deleted += 1
    return deleted


class _ProviderFailure(Exception):
    """Carry an exception the embedding provider's own code raised.

    Args:
        error: The provider's exception, also kept as ``__cause__``.
    """

    def __init__(self, error: Exception) -> None:
        super().__init__("embedding provider failed")
        self.error = error


def _from_provider(read: Callable[[], _T]) -> _T:
    """Run one step of the provider's own code, marking what it raises.

    Provider adapters wrap arbitrary client libraries, so any ``Exception``
    they raise is an embedding failure. ``ContentSuppressedError`` is a
    takedown refusal, never an embedding failure, and propagates unchanged, as
    do ``BaseException`` subclasses outside ``Exception`` (interrupts, exits,
    cancellation).

    Args:
        read: The provider attribute read, call or result read.

    Returns:
        What ``read`` returned.

    Raises:
        _ProviderFailure: If ``read`` raised any other ``Exception``.
    """
    try:
        return read()
    except ContentSuppressedError:
        raise
    except Exception as error:
        raise _ProviderFailure(error) from error


def _embed_chunks(
    *,
    chunks: list[SemanticChunk],
    provider: EmbeddingProvider | None,
    now: datetime,
) -> tuple[int, int]:
    """Apply one embedding batch, storing only vectors the column accepts.

    A provider whose declared dimension differs from the column is refused
    before it is called. Any exception from the provider's own code (its
    attribute reads, the call, reading its result) or a wrong vector count
    fails the whole batch; a single vector that cannot be stored fails only
    its chunk. Errors from this module's own code propagate.

    The result is read at most one vector past the number of texts, so an
    endless result fails the count instead of hanging the sync.

    Args:
        chunks: Ordered chunks awaiting embeddings.
        provider: Configured embedding provider, when available.
        now: Timestamp for successfully embedded chunks.

    Returns:
        Counts of successfully embedded and failed chunks.
    """
    if provider is None or not chunks:
        return 0, 0
    embedder: EmbeddingProvider = provider
    texts = [chunk.text for chunk in chunks]
    model_name: str | None = None
    dimensions: object = None
    try:
        model_name = _from_provider(lambda: embedder.model_name)
        dimensions = _from_provider(lambda: embedder.dimensions)
        _check_provider_dimensions(dimensions)
        vectors: list[Any] = _from_provider(
            lambda: list(islice(embedder.embed_texts(texts=texts), len(texts) + 1)),
        )
        _validate_embedding_count(vectors=vectors, expected_count=len(texts))
    except _ProviderFailure as failure:
        _mark_embedding_failed(
            chunks=chunks,
            model_name=model_name,
            dimensions=dimensions,
            error=failure.error,
        )
        return 0, len(chunks)
    except PipelineFailure as failure:
        _mark_embedding_failed(
            chunks=chunks,
            model_name=model_name,
            dimensions=dimensions,
            error=failure,
        )
        return 0, len(chunks)
    embedded_count = 0
    for chunk, vector in zip(chunks, vectors, strict=True):
        try:
            stored_vector = _storable_embedding(vector)
        except PipelineFailure as error:
            _mark_embedding_failed(
                chunks=[chunk],
                model_name=model_name,
                dimensions=dimensions,
                error=error,
            )
            continue
        chunk.embedding_status = SemanticChunkEmbeddingStatus.EMBEDDED.value
        chunk.embedding_model = model_name
        chunk.embedding_dimensions = EMBEDDING_DIMENSIONS
        chunk.embedding_vector = stored_vector
        chunk.embedding_error = None
        chunk.embedded_at = now
        embedded_count += 1
    return embedded_count, len(chunks) - embedded_count


def _mark_embedding_failed(
    *,
    chunks: list[SemanticChunk],
    model_name: str | None,
    dimensions: object,
    error: Exception,
) -> None:
    """Record one bounded embedding failure on each of ``chunks``.

    Any previous vector and embedding time are cleared, so a failed chunk
    never pairs a vector with a model that did not produce it.

    Args:
        chunks: Chunks whose embedding failed.
        model_name: Model the embedding was requested from, if it was read.
        dimensions: Dimension the provider declared, if it was read.
        error: Failure, whose message is never stored or logged.
    """
    descriptor = persisted_failure(describe_failure("embedding", error))
    log_failure(logger, descriptor, error)
    for chunk in chunks:
        chunk.embedding_status = SemanticChunkEmbeddingStatus.FAILED.value
        chunk.embedding_model = model_name
        chunk.embedding_dimensions = _storable_dimensions(dimensions)
        chunk.embedding_vector = None
        chunk.embedding_error = descriptor
        chunk.embedded_at = None


def _segments_from_transcript(
    transcript: Transcript,
) -> list[TranscriptSegmentData]:
    """Extract normalized segments from transcript JSON."""
    if not transcript.segments_json:
        return []

    segments: list[TranscriptSegmentData] = []
    for segment in transcript.segments_json:
        text_value = segment.get("text")
        if not isinstance(text_value, str) or not text_value.strip():
            continue
        segments.append(
            TranscriptSegmentData(
                text=" ".join(text_value.split()),
                start_seconds=_optional_seconds(segment.get("start")),
                end_seconds=_optional_seconds(segment.get("end")),
            ),
        )
    return segments


def _candidates_from_segments(
    *,
    transcript: Transcript,
    segments: list[TranscriptSegmentData],
    policy: SemanticChunkingPolicy,
) -> list[SemanticChunkCandidateData]:
    """Build timestamped candidates from transcript segments."""
    candidates: list[SemanticChunkCandidateData] = []
    current: list[TranscriptSegmentData] = []
    current_words = 0

    for segment in segments:
        segment_words = len(segment.text.split())
        if current and current_words + segment_words > policy.max_words:
            candidates.append(
                _candidate_from_segments(
                    transcript=transcript,
                    segments=current,
                    chunk_index=len(candidates),
                    policy=policy,
                ),
            )
            current = _overlap_segments(segments=current, policy=policy)
            current_words = sum(len(item.text.split()) for item in current)

        current.append(segment)
        current_words += segment_words

    if current_words >= policy.min_words or not candidates:
        candidates.append(
            _candidate_from_segments(
                transcript=transcript,
                segments=current,
                chunk_index=len(candidates),
                policy=policy,
            ),
        )
    return candidates


def _candidate_from_segments(
    *,
    transcript: Transcript,
    segments: list[TranscriptSegmentData],
    chunk_index: int,
    policy: SemanticChunkingPolicy,
) -> SemanticChunkCandidateData:
    """Create a chunk candidate from a list of transcript segments."""
    text = " ".join(segment.text for segment in segments).strip()
    words = text.split()
    source_hash = _source_text_hash(text)
    return SemanticChunkCandidateData(
        chunk_index=chunk_index,
        chunk_key=_chunk_key(
            transcript=transcript,
            chunk_index=chunk_index,
            source_text_hash=source_hash,
            pipeline_version=policy.pipeline_version,
        ),
        source_text_hash=source_hash,
        text=text,
        context_snippet=_context_snippet(words=words, policy=policy),
        token_count=len(words),
        start_seconds=_first_seconds(segment.start_seconds for segment in segments),
        end_seconds=_last_seconds(segment.end_seconds for segment in segments),
        pipeline_version=policy.pipeline_version,
    )


def _candidates_from_words(
    *,
    transcript: Transcript,
    words: list[str],
    policy: SemanticChunkingPolicy,
) -> list[SemanticChunkCandidateData]:
    """Build candidates from plain transcript words."""
    candidates: list[SemanticChunkCandidateData] = []
    start = 0
    step = policy.max_words - policy.overlap_words

    while start < len(words):
        chunk_words = words[start : start + policy.max_words]
        if len(chunk_words) < policy.min_words and candidates:
            break
        text = " ".join(chunk_words)
        source_hash = _source_text_hash(text)
        candidates.append(
            SemanticChunkCandidateData(
                chunk_index=len(candidates),
                chunk_key=_chunk_key(
                    transcript=transcript,
                    chunk_index=len(candidates),
                    source_text_hash=source_hash,
                    pipeline_version=policy.pipeline_version,
                ),
                source_text_hash=source_hash,
                text=text,
                context_snippet=_context_snippet(words=chunk_words, policy=policy),
                token_count=len(chunk_words),
                start_seconds=None,
                end_seconds=None,
                pipeline_version=policy.pipeline_version,
            ),
        )
        start += step
    return candidates


def _overlap_segments(
    *,
    segments: list[TranscriptSegmentData],
    policy: SemanticChunkingPolicy,
) -> list[TranscriptSegmentData]:
    """Choose trailing segments to carry into the next chunk."""
    if policy.overlap_words == 0:
        return []

    overlap: list[TranscriptSegmentData] = []
    overlap_words = 0
    for segment in reversed(segments):
        segment_words = len(segment.text.split())
        if overlap and overlap_words + segment_words > policy.overlap_words:
            break
        overlap.append(segment)
        overlap_words += segment_words
    return list(reversed(overlap))


def _apply_candidate(
    *,
    chunk: SemanticChunk,
    candidate: SemanticChunkCandidateData,
) -> None:
    """Apply candidate values to a semantic chunk row."""
    chunk.chunk_index = candidate.chunk_index
    chunk.pipeline_version = candidate.pipeline_version
    chunk.source_text_hash = candidate.source_text_hash
    chunk.text = candidate.text
    chunk.context_snippet = candidate.context_snippet
    chunk.token_count = candidate.token_count
    chunk.start_seconds = candidate.start_seconds
    chunk.end_seconds = candidate.end_seconds


def _semantic_chunk_needs_update(
    chunk: SemanticChunk,
    candidate: SemanticChunkCandidateData,
) -> bool:
    """Check whether a persisted chunk differs from its candidate."""
    return any(
        (
            chunk.chunk_index != candidate.chunk_index,
            chunk.source_text_hash != candidate.source_text_hash,
            chunk.text != candidate.text,
            chunk.context_snippet != candidate.context_snippet,
            chunk.token_count != candidate.token_count,
            chunk.start_seconds != candidate.start_seconds,
            chunk.end_seconds != candidate.end_seconds,
        ),
    )


def _check_provider_dimensions(dimensions: object) -> None:
    """Refuse a provider whose vectors could never fit the embedding column.

    Args:
        dimensions: Dimension the provider declares.

    Raises:
        PipelineFailure: If ``dimensions`` is not ``EMBEDDING_DIMENSIONS``.
    """
    if (
        isinstance(dimensions, bool)
        or not isinstance(dimensions, int)
        or dimensions != EMBEDDING_DIMENSIONS
    ):
        raise PipelineFailure(
            "embedding provider dimensions differ from the embedding column",
            reason=FailureReason.EMBEDDING_DIMENSIONS_MISMATCH,
        )


def _storable_dimensions(dimensions: object) -> int | None:
    """Return ``dimensions`` if the integer column can record it, else ``None``.

    Args:
        dimensions: Dimension a provider declared.

    Returns:
        ``dimensions`` when it is an ``int`` (not ``bool``) within int4.
    """
    if isinstance(dimensions, bool) or not isinstance(dimensions, int):
        return None
    if not _INT4_MIN <= dimensions <= _INT4_MAX:
        return None
    return int(dimensions)


def _validate_embedding_count(
    *,
    vectors: list[Any],
    expected_count: int,
) -> None:
    """Require one vector per requested text.

    Args:
        vectors: Vectors returned by the provider.
        expected_count: Number of texts sent.

    Raises:
        PipelineFailure: If the provider returned another number of vectors.
    """
    if len(vectors) != expected_count:
        raise PipelineFailure(
            "embedding provider returned an unexpected vector count",
            reason=FailureReason.EMBEDDING_COUNT_MISMATCH,
        )


def _storable_embedding(vector: object) -> list[float]:
    """Return ``vector`` as floats if the embedding column can store it.

    The column type would raise at flush for these vectors, losing the whole
    sync, so each is refused here and fails only its chunk.

    Args:
        vector: One vector returned by the provider.

    Returns:
        The vector's elements as floats.

    Raises:
        PipelineFailure: ``embedding_invalid_type`` if ``vector`` is not a
            list or tuple; ``embedding_dimensions_mismatch`` if it does not
            have exactly ``EMBEDDING_DIMENSIONS`` elements;
            ``embedding_invalid_value`` if an element is not an ``int`` or
            ``float`` (strings and booleans included) finite in float32.
    """
    if not isinstance(vector, list | tuple):
        raise PipelineFailure(
            "embedding vector is not a list or tuple",
            reason=FailureReason.EMBEDDING_INVALID_TYPE,
        )
    if len(vector) != EMBEDDING_DIMENSIONS:
        raise PipelineFailure(
            "embedding vector does not match the embedding column dimension",
            reason=FailureReason.EMBEDDING_DIMENSIONS_MISMATCH,
        )
    try:
        stored: list[float] = storable_vector(vector)
    except (TypeError, ValueError):
        raise PipelineFailure(
            "embedding vector has an element the embedding column cannot store",
            reason=FailureReason.EMBEDDING_INVALID_VALUE,
        ) from None
    return stored


def _transcript_text(
    transcript: Transcript,
) -> str:
    """Choose the best available transcript text for chunking."""
    text = transcript.cleaned_text or transcript.raw_text or ""
    return " ".join(text.split())


def _chunk_key(
    *,
    transcript: Transcript,
    chunk_index: int,
    source_text_hash: str,
    pipeline_version: str,
) -> str:
    """Build a deterministic chunk idempotency key."""
    return (
        f"{pipeline_version}:transcript:{transcript.id}:chunk:{chunk_index}:"
        f"{source_text_hash[:16]}"
    )


def _source_text_hash(
    text: str,
) -> str:
    """Hash normalized chunk text."""
    return sha256(" ".join(text.split()).encode("utf-8")).hexdigest()


def _context_snippet(
    *,
    words: list[str],
    policy: SemanticChunkingPolicy,
) -> str:
    """Build a short context snippet for ops and retrieval previews."""
    if len(words) <= policy.context_words * 2 or policy.context_words == 0:
        return " ".join(words)
    prefix = " ".join(words[: policy.context_words])
    suffix = " ".join(words[-policy.context_words :])
    return f"{prefix} ... {suffix}"


def _optional_seconds(
    value: object,
) -> int | None:
    """Convert numeric segment offsets to whole seconds."""
    if isinstance(value, int | float):
        return max(0, round(value))
    return None


def _first_seconds(
    values: Iterable[int | None],
) -> int | None:
    """Return the first non-null timestamp."""
    for value in values:
        if value is not None:
            return value
    return None


def _last_seconds(
    values: Iterable[int | None],
) -> int | None:
    """Return the last non-null timestamp."""
    last_value: int | None = None
    for value in values:
        if value is not None:
            last_value = value
    return last_value

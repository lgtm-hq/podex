"""Semantic chunk generation and persistence services."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Protocol

from sqlalchemy.orm import Session

from podex.models import (
    SemanticChunk,
    SemanticChunkEmbeddingStatus,
    Transcript,
)


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
    """Protocol for model-backed embedding providers."""

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
            Embedding vectors in the same order as ``texts``.
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
        Summary of created, updated, deleted, and embedded chunks.
    """
    effective_now = now or datetime.now(UTC)
    effective_policy = policy or SemanticChunkingPolicy()
    candidates = build_semantic_chunk_candidates(
        transcript=transcript,
        policy=effective_policy,
    )
    existing_chunks = {
        chunk.chunk_key: chunk
        for chunk in db.query(SemanticChunk)
        .filter(SemanticChunk.transcript_id == transcript.id)
        .filter(SemanticChunk.pipeline_version == effective_policy.pipeline_version)
        .all()
    }
    candidate_keys = {candidate.chunk_key for candidate in candidates}

    created_count = 0
    updated_count = 0
    deleted_count = 0
    embedded_count = 0
    failed_count = 0

    for stale_key, stale_chunk in existing_chunks.items():
        if stale_key not in candidate_keys:
            db.delete(stale_chunk)
            deleted_count += 1

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

    if embedding_provider is not None and chunks_to_embed:
        try:
            vectors = embedding_provider.embed_texts(
                texts=[chunk.text for chunk in chunks_to_embed],
            )
            _validate_embeddings(
                vectors=vectors,
                expected_count=len(chunks_to_embed),
                expected_dimensions=embedding_provider.dimensions,
            )
        except (RuntimeError, ValueError, TypeError) as exc:
            failed_count = len(chunks_to_embed)
            for chunk in chunks_to_embed:
                chunk.embedding_status = SemanticChunkEmbeddingStatus.FAILED.value
                chunk.embedding_model = embedding_provider.model_name
                chunk.embedding_dimensions = embedding_provider.dimensions
                chunk.embedding_error = str(exc)
        else:
            embedded_count = len(chunks_to_embed)
            for chunk, vector in zip(chunks_to_embed, vectors, strict=True):
                chunk.embedding_status = SemanticChunkEmbeddingStatus.EMBEDDED.value
                chunk.embedding_model = embedding_provider.model_name
                chunk.embedding_dimensions = embedding_provider.dimensions
                chunk.embedding_vector = vector
                chunk.embedding_error = None
                chunk.embedded_at = effective_now

    db.flush()
    return SemanticChunkSyncResultData(
        created_count=created_count,
        updated_count=updated_count,
        deleted_count=deleted_count,
        embedded_count=embedded_count,
        failed_count=failed_count,
        chunk_keys=tuple(candidate.chunk_key for candidate in candidates),
    )


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


def _validate_embeddings(
    *,
    vectors: list[list[float]],
    expected_count: int,
    expected_dimensions: int,
) -> None:
    """Validate embedding provider output shape."""
    if len(vectors) != expected_count:
        raise ValueError("embedding provider returned an unexpected vector count")
    for vector in vectors:
        if len(vector) != expected_dimensions:
            raise ValueError("embedding provider returned unexpected dimensions")


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

"""Replay-safe derivative generation orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256

from sqlalchemy.orm import Session

from podex.models import (
    DerivativeGenerationRun,
    DerivativeGenerationRunStatus,
    Episode,
    Media,
    Mention,
    Transcript,
)
from podex.services.derivative_summaries import (
    SummaryProvider,
    build_episode_summary_source,
    generate_episode_summary,
    generate_media_summary,
    stable_source_text_hash,
)
from podex.services.graph_relations import GraphTripleInputData, upsert_graph_triple
from podex.services.semantic_chunks import (
    EmbeddingProvider,
    SemanticChunkingPolicy,
    sync_semantic_chunks_for_transcript,
)


@dataclass(frozen=True, slots=True)
class DerivativeGenerationConfig:
    """Versioned configuration for derivative generation."""

    pipeline_version: str = "derivative-generation-v1"
    chunk_pipeline_version: str = "semantic-chunk-v1"
    summary_prompt_version: str = "summary-prompt-v1"


def generate_episode_derivatives(
    *,
    db: Session,
    episode: Episode,
    config: DerivativeGenerationConfig | None = None,
    transcript: Transcript | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    summary_provider: SummaryProvider | None = None,
    chunking_policy: SemanticChunkingPolicy | None = None,
    graph_triples: list[GraphTripleInputData] | None = None,
    now: datetime | None = None,
) -> DerivativeGenerationRun:
    """Generate durable derivatives for a single episode.

    Args:
        db: Database session.
        episode: Episode to process.
        config: Versioned derivative generation configuration.
        transcript: Optional transcript source; latest episode transcript is used
            when omitted.
        embedding_provider: Optional embedding provider for semantic chunks.
        summary_provider: Optional summary provider for episode and media summaries.
        chunking_policy: Optional semantic chunking policy.
        graph_triples: Optional graph triples to upsert with this run.
        now: Optional timestamp for deterministic tests.

    Returns:
        Persisted derivative generation run summary.
    """
    effective_config = config or DerivativeGenerationConfig()
    effective_now = now or datetime.now(UTC)
    effective_transcript = transcript or _latest_transcript(db=db, episode=episode)
    source_text_hash = _run_source_hash(
        db=db,
        episode=episode,
        transcript=effective_transcript,
    )
    effective_policy = chunking_policy or SemanticChunkingPolicy(
        pipeline_version=effective_config.chunk_pipeline_version,
    )
    run = _upsert_generation_run(
        db=db,
        episode=episode,
        transcript=effective_transcript,
        config=effective_config,
        summary_model=summary_provider.model_name if summary_provider else None,
        source_text_hash=source_text_hash,
        started_at=effective_now,
    )

    semantic_result = None
    if effective_transcript is not None:
        semantic_result = sync_semantic_chunks_for_transcript(
            db=db,
            transcript=effective_transcript,
            embedding_provider=embedding_provider,
            policy=effective_policy,
            now=effective_now,
        )

    episode_summary = None
    media_summary_count = 0
    if summary_provider is not None:
        episode_summary = generate_episode_summary(
            db=db,
            episode=episode,
            provider=summary_provider,
            pipeline_version=effective_config.pipeline_version,
            prompt_version=effective_config.summary_prompt_version,
            generated_at=effective_now,
        )
        for media in _episode_media(db=db, episode=episode):
            generate_media_summary(
                db=db,
                media=media,
                provider=summary_provider,
                pipeline_version=effective_config.pipeline_version,
                prompt_version=effective_config.summary_prompt_version,
                generated_at=effective_now,
            )
            media_summary_count += 1

    graph_triple_count = 0
    for graph_triple in graph_triples or []:
        upsert_graph_triple(db=db, payload=graph_triple)
        graph_triple_count += 1

    run.status = DerivativeGenerationRunStatus.COMPLETED.value
    run.episode_summary_id = episode_summary.id if episode_summary else None
    run.semantic_chunks_created = (
        semantic_result.created_count if semantic_result is not None else 0
    )
    run.semantic_chunks_updated = (
        semantic_result.updated_count if semantic_result is not None else 0
    )
    run.semantic_chunks_deleted = (
        semantic_result.deleted_count if semantic_result is not None else 0
    )
    run.semantic_chunks_embedded = (
        semantic_result.embedded_count if semantic_result is not None else 0
    )
    run.semantic_chunks_failed = (
        semantic_result.failed_count if semantic_result is not None else 0
    )
    run.media_summaries_generated = media_summary_count
    run.graph_triples_upserted = graph_triple_count
    run.completed_at = effective_now
    run.error_message = None
    run.metadata_json = {
        "semantic_chunk_keys": (
            list(semantic_result.chunk_keys) if semantic_result is not None else []
        ),
    }
    db.flush()
    return run


def stable_derivative_generation_run_key(
    *,
    episode_id: int,
    transcript_id: int | None,
    pipeline_version: str,
    chunk_pipeline_version: str,
    summary_prompt_version: str,
    summary_model: str | None,
    source_text_hash: str,
) -> str:
    """Build a stable idempotency key for a derivative generation run.

    Args:
        episode_id: Episode id.
        transcript_id: Optional transcript id.
        pipeline_version: Derivative orchestration version.
        chunk_pipeline_version: Semantic chunk pipeline version.
        summary_prompt_version: Summary prompt version.
        summary_model: Optional summary model version.
        source_text_hash: Hash of summarized source text.

    Returns:
        Stable derivative run key.
    """
    digest = sha256(
        "|".join(
            (
                str(episode_id),
                str(transcript_id or ""),
                _normalize_required(
                    value=pipeline_version,
                    field_name="pipeline_version",
                ),
                _normalize_required(
                    value=chunk_pipeline_version,
                    field_name="chunk_pipeline_version",
                ),
                _normalize_required(
                    value=summary_prompt_version,
                    field_name="summary_prompt_version",
                ),
                summary_model or "",
                _normalize_source_hash(source_text_hash),
            ),
        ).encode("utf-8"),
    ).hexdigest()
    return f"derivative-run:{digest[:64]}"


def _upsert_generation_run(
    *,
    db: Session,
    episode: Episode,
    transcript: Transcript | None,
    config: DerivativeGenerationConfig,
    summary_model: str | None,
    source_text_hash: str,
    started_at: datetime,
) -> DerivativeGenerationRun:
    """Create or reset a derivative generation run record."""
    run_key = stable_derivative_generation_run_key(
        episode_id=episode.id,
        transcript_id=transcript.id if transcript is not None else None,
        pipeline_version=config.pipeline_version,
        chunk_pipeline_version=config.chunk_pipeline_version,
        summary_prompt_version=config.summary_prompt_version,
        summary_model=summary_model,
        source_text_hash=source_text_hash,
    )
    run = (
        db.query(DerivativeGenerationRun)
        .filter(DerivativeGenerationRun.run_key == run_key)
        .first()
    )
    if run is None:
        run = DerivativeGenerationRun(
            run_key=run_key,
            episode_id=episode.id,
            transcript_id=transcript.id if transcript is not None else None,
            pipeline_version=config.pipeline_version,
            chunk_pipeline_version=config.chunk_pipeline_version,
            summary_prompt_version=config.summary_prompt_version,
            summary_model=summary_model,
            source_text_hash=source_text_hash,
            started_at=started_at,
        )
        db.add(run)

    run.status = DerivativeGenerationRunStatus.RUNNING.value
    run.started_at = started_at
    run.completed_at = None
    run.error_message = None
    db.flush()
    return run


def _run_source_hash(
    *,
    db: Session,
    episode: Episode,
    transcript: Transcript | None,
) -> str:
    """Build a source hash for the orchestration run."""
    if transcript is not None:
        return stable_source_text_hash(
            transcript.cleaned_text or transcript.raw_text or ""
        )
    return build_episode_summary_source(db=db, episode=episode).source_text_hash


def _latest_transcript(
    *,
    db: Session,
    episode: Episode,
) -> Transcript | None:
    """Return the newest transcript for an episode."""
    return (
        db.query(Transcript)
        .filter(Transcript.episode_id == episode.id)
        .order_by(Transcript.id.desc())
        .first()
    )


def _episode_media(
    *,
    db: Session,
    episode: Episode,
) -> list[Media]:
    """Return media mentioned in an episode exactly once."""
    return (
        db.query(Media)
        .join(Mention, Mention.media_id == Media.id)
        .filter(Mention.episode_id == episode.id)
        .order_by(Media.id)
        .distinct()
        .all()
    )


def _normalize_source_hash(
    source_text_hash: str,
) -> str:
    """Validate and normalize a source text hash."""
    normalized = _normalize_required(
        value=source_text_hash,
        field_name="source_text_hash",
    )
    if len(normalized) != 64:
        raise ValueError("source_text_hash must be a SHA-256 hex digest")
    return normalized


def _normalize_required(
    *,
    value: str,
    field_name: str,
) -> str:
    """Normalize a required string field."""
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must be non-empty")
    return normalized

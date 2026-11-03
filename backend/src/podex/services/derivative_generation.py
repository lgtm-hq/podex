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
from podex.services.field_normalization import (
    normalize_required as _normalize_required,
)
from podex.services.field_normalization import (
    normalize_source_hash as _normalize_source_hash,
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


# Keep the existing named command inputs compatible with callers.
# pylint: disable-next=too-many-arguments
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
    config = config or DerivativeGenerationConfig()
    now = now or datetime.now(UTC)
    transcript = transcript or _latest_transcript(db=db, episode=episode)
    chunking_policy = chunking_policy or SemanticChunkingPolicy(
        pipeline_version=config.chunk_pipeline_version,
    )
    run = _upsert_generation_run(
        db=db,
        candidate=DerivativeGenerationRun(
            episode_id=episode.id,
            transcript_id=(transcript.id if transcript is not None else None),
            pipeline_version=config.pipeline_version,
            chunk_pipeline_version=config.chunk_pipeline_version,
            summary_prompt_version=config.summary_prompt_version,
            summary_model=summary_provider.model_name if summary_provider else None,
            source_text_hash=_run_source_hash(
                db=db,
                episode=episode,
                transcript=transcript,
            ),
            started_at=now,
        ),
    )

    semantic_result = None
    if transcript is not None:
        semantic_result = sync_semantic_chunks_for_transcript(
            db=db,
            transcript=transcript,
            embedding_provider=embedding_provider,
            policy=chunking_policy,
            now=now,
        )

    episode_summary = None
    media_summary_count = 0
    if summary_provider is not None:
        episode_summary = generate_episode_summary(
            db=db,
            episode=episode,
            provider=summary_provider,
            pipeline_version=config.pipeline_version,
            prompt_version=config.summary_prompt_version,
            generated_at=now,
        )
        for media in _episode_media(db=db, episode=episode):
            generate_media_summary(
                db=db,
                media=media,
                provider=summary_provider,
                pipeline_version=config.pipeline_version,
                prompt_version=config.summary_prompt_version,
                generated_at=now,
            )
            media_summary_count += 1

    for graph_triple in graph_triples or []:
        upsert_graph_triple(db=db, payload=graph_triple)

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
    run.graph_triples_upserted = len(graph_triples or [])
    run.completed_at = now
    run.error_message = None
    run.metadata_json = {
        "semantic_chunk_keys": (
            list(semantic_result.chunk_keys) if semantic_result is not None else []
        ),
    }
    db.flush()
    return run


# Each identity field is explicit so persisted hash keys remain compatible.
# pylint: disable-next=too-many-arguments
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
                _normalize_source_hash(source_text_hash=source_text_hash),
            ),
        ).encode("utf-8"),
    ).hexdigest()
    return f"derivative-run:{digest[:64]}"


def _upsert_generation_run(
    *,
    db: Session,
    candidate: DerivativeGenerationRun,
) -> DerivativeGenerationRun:
    """Create or reset a run using its complete persisted identity record.

    Args:
        db: Database session responsible for the run.
        candidate: Unpersisted run fields for this generation attempt.

    Returns:
        The newly created or reset persisted run.
    """
    run_key = stable_derivative_generation_run_key(
        episode_id=candidate.episode_id,
        transcript_id=candidate.transcript_id,
        pipeline_version=candidate.pipeline_version,
        chunk_pipeline_version=candidate.chunk_pipeline_version,
        summary_prompt_version=candidate.summary_prompt_version,
        summary_model=candidate.summary_model,
        source_text_hash=candidate.source_text_hash,
    )
    run = (
        db.query(DerivativeGenerationRun)
        .filter(DerivativeGenerationRun.run_key == run_key)
        .first()
    )
    if run is None:
        candidate.run_key = run_key
        run = candidate
        db.add(run)
    run.status = DerivativeGenerationRunStatus.RUNNING.value
    run.started_at = candidate.started_at
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
        return str(
            stable_source_text_hash(
                transcript.cleaned_text or transcript.raw_text or "",
            ),
        )
    source = build_episode_summary_source(db=db, episode=episode)
    return str(source.source_text_hash)


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
    return list(
        db.query(Media)
        .join(Mention, Mention.media_id == Media.id)
        .filter(Mention.episode_id == episode.id)
        .order_by(Media.id)
        .distinct()
        .all(),
    )

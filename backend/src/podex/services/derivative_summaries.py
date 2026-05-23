"""Derivative summary generation and persistence services."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from typing import Protocol

from sqlalchemy.orm import Session

from podex.models import (
    DerivativeSummaryKind,
    DerivativeSummaryStatus,
    Episode,
    EpisodeSummary,
    Media,
    MediaSummary,
    Mention,
    SemanticChunk,
    Transcript,
)


@dataclass(frozen=True, slots=True)
class SummarySourceData:
    """Source material used to generate a derivative summary."""

    resource_kind: str
    resource_id: int
    source_text: str
    source_text_hash: str
    metadata_json: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class GeneratedSummaryData:
    """Generated narrative summary payload."""

    summary_text: str
    short_summary: str | None = None
    highlights: list[str] | None = None
    citations: list[dict[str, object]] | None = None
    metadata_json: dict[str, object] | None = None


class SummaryProvider(Protocol):
    """Port implemented by LLM or deterministic summary generators."""

    model_name: str

    def summarize(
        self,
        source: SummarySourceData,
    ) -> GeneratedSummaryData:
        """Generate a summary from source material.

        Args:
            source: Source material and provenance metadata.

        Returns:
            Generated summary payload.
        """


def generate_episode_summary(
    *,
    db: Session,
    episode: Episode,
    provider: SummaryProvider,
    summary_kind: DerivativeSummaryKind = DerivativeSummaryKind.OVERVIEW,
    pipeline_version: str,
    prompt_version: str,
    generated_at: datetime | None = None,
) -> EpisodeSummary:
    """Generate and persist an episode summary.

    Args:
        db: Database session.
        episode: Episode to summarize.
        provider: Summary generation provider.
        summary_kind: Narrative summary kind.
        pipeline_version: Derivative pipeline version.
        prompt_version: Summary prompt version.
        generated_at: Optional generation timestamp.

    Returns:
        Persisted episode summary.
    """
    source = build_episode_summary_source(db=db, episode=episode)
    generated = provider.summarize(source)
    return upsert_episode_summary(
        db=db,
        episode=episode,
        summary_kind=summary_kind,
        pipeline_version=pipeline_version,
        prompt_version=prompt_version,
        source_model=provider.model_name,
        source_text_hash=source.source_text_hash,
        summary_text=generated.summary_text,
        short_summary=generated.short_summary,
        highlights_json=generated.highlights,
        citations_json=generated.citations,
        metadata_json={
            **source.metadata_json,
            **(generated.metadata_json or {}),
        },
        generated_at=generated_at,
    )


def generate_media_summary(
    *,
    db: Session,
    media: Media,
    provider: SummaryProvider,
    summary_kind: DerivativeSummaryKind = DerivativeSummaryKind.OVERVIEW,
    pipeline_version: str,
    prompt_version: str,
    generated_at: datetime | None = None,
) -> MediaSummary:
    """Generate and persist a media summary.

    Args:
        db: Database session.
        media: Media entity to summarize.
        provider: Summary generation provider.
        summary_kind: Narrative summary kind.
        pipeline_version: Derivative pipeline version.
        prompt_version: Summary prompt version.
        generated_at: Optional generation timestamp.

    Returns:
        Persisted media summary.
    """
    source = build_media_summary_source(db=db, media=media)
    generated = provider.summarize(source)
    return upsert_media_summary(
        db=db,
        media=media,
        summary_kind=summary_kind,
        pipeline_version=pipeline_version,
        prompt_version=prompt_version,
        source_model=provider.model_name,
        source_text_hash=source.source_text_hash,
        summary_text=generated.summary_text,
        short_summary=generated.short_summary,
        highlights_json=generated.highlights,
        citations_json=generated.citations,
        metadata_json={
            **source.metadata_json,
            **(generated.metadata_json or {}),
        },
        generated_at=generated_at,
    )


def upsert_episode_summary(
    *,
    db: Session,
    episode: Episode,
    summary_kind: DerivativeSummaryKind,
    pipeline_version: str,
    prompt_version: str,
    source_model: str,
    source_text_hash: str,
    summary_text: str,
    short_summary: str | None = None,
    highlights_json: list[str] | None = None,
    citations_json: list[dict[str, object]] | None = None,
    metadata_json: dict[str, object] | None = None,
    status: DerivativeSummaryStatus = DerivativeSummaryStatus.READY,
    error_message: str | None = None,
    generated_at: datetime | None = None,
) -> EpisodeSummary:
    """Create or update an idempotent episode summary record.

    Args:
        db: Database session.
        episode: Episode being summarized.
        summary_kind: Narrative summary kind.
        pipeline_version: Derivative pipeline version.
        prompt_version: Summary prompt version.
        source_model: Model or generator version.
        source_text_hash: Hash of the summarized source material.
        summary_text: Long-form generated summary text.
        short_summary: Optional display-length summary.
        highlights_json: Optional highlighted facts.
        citations_json: Optional provenance citations.
        metadata_json: Optional summary metadata.
        status: Summary lifecycle status.
        error_message: Optional generation failure details.
        generated_at: Optional generation timestamp.

    Returns:
        Existing or newly created episode summary.
    """
    summary_key = stable_episode_summary_key(
        episode_id=episode.id,
        summary_kind=summary_kind,
        pipeline_version=pipeline_version,
        prompt_version=prompt_version,
        source_model=source_model,
        source_text_hash=source_text_hash,
    )
    summary = (
        db.query(EpisodeSummary)
        .filter(EpisodeSummary.summary_key == summary_key)
        .first()
    )
    if summary is None:
        summary = EpisodeSummary(
            episode_id=episode.id,
            summary_key=summary_key,
            summary_kind=summary_kind.value,
            pipeline_version=_normalize_required(
                value=pipeline_version,
                field_name="pipeline_version",
            ),
            prompt_version=_normalize_required(
                value=prompt_version,
                field_name="prompt_version",
            ),
            source_model=_normalize_required(
                value=source_model,
                field_name="source_model",
            ),
            source_text_hash=_normalize_source_hash(source_text_hash),
            generated_at=generated_at or datetime.now(UTC),
        )
        db.add(summary)

    _apply_summary_fields(
        summary=summary,
        summary_text=summary_text,
        short_summary=short_summary,
        highlights_json=highlights_json,
        citations_json=citations_json,
        metadata_json=metadata_json,
        status=status,
        error_message=error_message,
        generated_at=generated_at,
    )
    db.flush()
    return summary


def upsert_media_summary(
    *,
    db: Session,
    media: Media,
    summary_kind: DerivativeSummaryKind,
    pipeline_version: str,
    prompt_version: str,
    source_model: str,
    source_text_hash: str,
    summary_text: str,
    short_summary: str | None = None,
    highlights_json: list[str] | None = None,
    citations_json: list[dict[str, object]] | None = None,
    metadata_json: dict[str, object] | None = None,
    status: DerivativeSummaryStatus = DerivativeSummaryStatus.READY,
    error_message: str | None = None,
    generated_at: datetime | None = None,
) -> MediaSummary:
    """Create or update an idempotent media summary record.

    Args:
        db: Database session.
        media: Media entity being summarized.
        summary_kind: Narrative summary kind.
        pipeline_version: Derivative pipeline version.
        prompt_version: Summary prompt version.
        source_model: Model or generator version.
        source_text_hash: Hash of the summarized source material.
        summary_text: Long-form generated summary text.
        short_summary: Optional display-length summary.
        highlights_json: Optional highlighted facts.
        citations_json: Optional provenance citations.
        metadata_json: Optional summary metadata.
        status: Summary lifecycle status.
        error_message: Optional generation failure details.
        generated_at: Optional generation timestamp.

    Returns:
        Existing or newly created media summary.
    """
    summary_key = stable_media_summary_key(
        media_id=media.id,
        summary_kind=summary_kind,
        pipeline_version=pipeline_version,
        prompt_version=prompt_version,
        source_model=source_model,
        source_text_hash=source_text_hash,
    )
    summary = (
        db.query(MediaSummary).filter(MediaSummary.summary_key == summary_key).first()
    )
    if summary is None:
        summary = MediaSummary(
            media_id=media.id,
            summary_key=summary_key,
            summary_kind=summary_kind.value,
            pipeline_version=_normalize_required(
                value=pipeline_version,
                field_name="pipeline_version",
            ),
            prompt_version=_normalize_required(
                value=prompt_version,
                field_name="prompt_version",
            ),
            source_model=_normalize_required(
                value=source_model,
                field_name="source_model",
            ),
            source_text_hash=_normalize_source_hash(source_text_hash),
            generated_at=generated_at or datetime.now(UTC),
        )
        db.add(summary)

    _apply_summary_fields(
        summary=summary,
        summary_text=summary_text,
        short_summary=short_summary,
        highlights_json=highlights_json,
        citations_json=citations_json,
        metadata_json=metadata_json,
        status=status,
        error_message=error_message,
        generated_at=generated_at,
    )
    db.flush()
    return summary


def build_episode_summary_source(
    *,
    db: Session,
    episode: Episode,
) -> SummarySourceData:
    """Build deterministic source material for an episode summary.

    Args:
        db: Database session.
        episode: Episode to summarize.

    Returns:
        Source text and provenance metadata.
    """
    chunks = (
        db.query(SemanticChunk)
        .filter(SemanticChunk.episode_id == episode.id)
        .order_by(SemanticChunk.chunk_index)
        .all()
    )
    if chunks:
        source_text = "\n\n".join(chunk.text for chunk in chunks)
        metadata_json: dict[str, object] = {
            "source": "semantic_chunks",
            "chunk_count": len(chunks),
        }
    else:
        transcripts = (
            db.query(Transcript)
            .filter(Transcript.episode_id == episode.id)
            .order_by(Transcript.id.desc())
            .all()
        )
        transcript_texts: list[str] = []
        for transcript in transcripts:
            transcript_text = transcript.cleaned_text or transcript.raw_text
            if transcript_text:
                transcript_texts.append(transcript_text)
        source_text = "\n\n".join(transcript_texts) or episode.title
        metadata_json = {
            "source": "transcripts" if transcript_texts else "episode_metadata",
            "transcript_count": len(transcript_texts),
        }
    return SummarySourceData(
        resource_kind="episode",
        resource_id=episode.id,
        source_text=source_text,
        source_text_hash=stable_source_text_hash(source_text),
        metadata_json=metadata_json,
    )


def build_media_summary_source(
    *,
    db: Session,
    media: Media,
) -> SummarySourceData:
    """Build deterministic source material for a media summary.

    Args:
        db: Database session.
        media: Media entity to summarize.

    Returns:
        Source text and provenance metadata.
    """
    identity_parts = [
        f"Title: {media.title}",
        f"Type: {media.type}",
    ]
    if media.author:
        identity_parts.append(f"Author: {media.author}")
    if media.year is not None:
        identity_parts.append(f"Year: {media.year}")
    if media.description:
        identity_parts.append(f"Description: {media.description}")

    mentions = (
        db.query(Mention)
        .filter(Mention.media_id == media.id)
        .order_by(Mention.episode_id, Mention.timestamp_seconds)
        .all()
    )
    mention_contexts = [
        mention.context.strip()
        for mention in mentions
        if mention.context and mention.context.strip()
    ]
    source_text = "\n".join(identity_parts + mention_contexts)
    return SummarySourceData(
        resource_kind="media",
        resource_id=media.id,
        source_text=source_text,
        source_text_hash=stable_source_text_hash(source_text),
        metadata_json={
            "source": "media_metadata_mentions",
            "mention_count": len(mentions),
            "mention_context_count": len(mention_contexts),
        },
    )


def stable_episode_summary_key(
    *,
    episode_id: int,
    summary_kind: DerivativeSummaryKind,
    pipeline_version: str,
    prompt_version: str,
    source_model: str,
    source_text_hash: str,
) -> str:
    """Build a stable key for an episode summary derivative.

    Args:
        episode_id: Episode id.
        summary_kind: Narrative summary kind.
        pipeline_version: Derivative pipeline version.
        prompt_version: Summary prompt version.
        source_model: Model or generator version.
        source_text_hash: Hash of summarized source material.

    Returns:
        Stable summary key.
    """
    return _stable_key(
        prefix="episode-summary",
        parts=(
            str(episode_id),
            summary_kind.value,
            _normalize_required(value=pipeline_version, field_name="pipeline_version"),
            _normalize_required(value=prompt_version, field_name="prompt_version"),
            _normalize_required(value=source_model, field_name="source_model"),
            _normalize_source_hash(source_text_hash),
        ),
    )


def stable_media_summary_key(
    *,
    media_id: int,
    summary_kind: DerivativeSummaryKind,
    pipeline_version: str,
    prompt_version: str,
    source_model: str,
    source_text_hash: str,
) -> str:
    """Build a stable key for a media summary derivative.

    Args:
        media_id: Media id.
        summary_kind: Narrative summary kind.
        pipeline_version: Derivative pipeline version.
        prompt_version: Summary prompt version.
        source_model: Model or generator version.
        source_text_hash: Hash of summarized source material.

    Returns:
        Stable summary key.
    """
    return _stable_key(
        prefix="media-summary",
        parts=(
            str(media_id),
            summary_kind.value,
            _normalize_required(value=pipeline_version, field_name="pipeline_version"),
            _normalize_required(value=prompt_version, field_name="prompt_version"),
            _normalize_required(value=source_model, field_name="source_model"),
            _normalize_source_hash(source_text_hash),
        ),
    )


def stable_source_text_hash(
    source_text: str,
) -> str:
    """Hash normalized summary source material.

    Args:
        source_text: Source material to hash.

    Returns:
        SHA-256 source text hash.
    """
    normalized = " ".join(source_text.split())
    return sha256(normalized.encode("utf-8")).hexdigest()


def _apply_summary_fields(
    *,
    summary: EpisodeSummary | MediaSummary,
    summary_text: str,
    short_summary: str | None,
    highlights_json: list[str] | None,
    citations_json: list[dict[str, object]] | None,
    metadata_json: dict[str, object] | None,
    status: DerivativeSummaryStatus,
    error_message: str | None,
    generated_at: datetime | None,
) -> None:
    """Apply mutable summary fields to an existing summary model."""
    summary.summary_text = _normalize_required(
        value=summary_text,
        field_name="summary_text",
    )
    summary.short_summary = _normalize_optional(short_summary)
    summary.highlights_json = highlights_json
    summary.citations_json = citations_json
    summary.metadata_json = metadata_json
    summary.status = status.value
    summary.error_message = _normalize_optional(error_message)
    if generated_at is not None:
        summary.generated_at = generated_at


def _stable_key(
    *,
    prefix: str,
    parts: tuple[str, ...],
) -> str:
    """Build a compact stable key from string parts."""
    digest = sha256("|".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}:{digest[:64]}"


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


def _normalize_optional(
    value: str | None,
) -> str | None:
    """Normalize an optional string field."""
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None

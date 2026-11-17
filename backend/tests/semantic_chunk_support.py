"""Shared helpers for the semantic chunk tests."""

from datetime import datetime

from sqlalchemy.orm import Session

from podex.models import Transcript
from podex.services.semantic_chunks import (
    EmbeddingProvider,
    SemanticChunkingPolicy,
    SemanticChunkSyncResultData,
    sync_semantic_chunks_for_transcript,
)
from tests.conftest import create_jre_episode


def create_transcript(
    *,
    db_session: Session,
    cleaned_text: str | None = None,
    segments_json: list[dict[str, object]] | None = None,
) -> Transcript:
    """Create a persisted transcript with podcast and episode parents.

    Args:
        db_session: Session to add the rows to.
        cleaned_text: Raw and cleaned transcript text.
        segments_json: Timestamped transcript segments.

    Returns:
        The flushed transcript.
    """
    episode = create_jre_episode(db_session)
    transcript = Transcript(
        episode_id=episode.id,
        provider="podscripts",
        raw_text=cleaned_text,
        cleaned_text=cleaned_text,
        segments_json=segments_json,
    )
    db_session.add(transcript)
    db_session.flush()
    return transcript


def sync_and_commit(
    db_session: Session,
    transcript: Transcript,
    /,
    *,
    provider: EmbeddingProvider | None,
    policy: SemanticChunkingPolicy,
    now: datetime | None = None,
) -> SemanticChunkSyncResultData:
    """Run one chunk sync of ``transcript`` with ``provider`` and commit it.

    Args:
        db_session: Session to sync in.
        transcript: Transcript to chunk.
        provider: Embedding provider, or ``None`` for a sync without one.
        policy: Chunking policy.
        now: Embedding time, when the test pins it.

    Returns:
        The sync result.
    """
    result = sync_semantic_chunks_for_transcript(
        db=db_session,
        transcript=transcript,
        embedding_provider=provider,
        policy=policy,
        now=now,
    )
    db_session.commit()
    return result

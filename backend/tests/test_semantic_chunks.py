"""Tests for semantic transcript chunk derivatives."""

from datetime import UTC, datetime

from assertpy import assert_that
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
    build_semantic_chunk_candidates,
    sync_semantic_chunks_for_transcript,
)


class FakeEmbeddingProvider:
    """Deterministic embedding provider for semantic chunk tests."""

    model_name = "fake-embedding-v1"
    dimensions = 3

    def embed_texts(
        self,
        texts: list[str],
    ) -> list[list[float]]:
        """Return stable toy vectors for the provided texts."""
        return [
            [float(index), float(len(text.split())), 1.0]
            for index, text in enumerate(texts)
        ]


class BadEmbeddingProvider:
    """Embedding provider that returns malformed vectors."""

    model_name = "bad-embedding-v1"
    dimensions = 3

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
    assert_that(chunk.embedding_dimensions).is_equal_to(3)
    assert_that(chunk.embedding_vector).is_equal_to([0.0, 6.0, 1.0])
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
    assert_that(chunk.embedding_error).contains("unexpected dimensions")


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


def _create_transcript(
    *,
    db_session: Session,
    cleaned_text: str | None = None,
    segments_json: list[dict[str, object]] | None = None,
) -> Transcript:
    """Create a persisted transcript with podcast and episode parents."""
    podcast = Podcast(name="The Joe Rogan Experience", slug="jre")
    db_session.add(podcast)
    db_session.flush()
    episode = Episode(
        podcast_id=podcast.id,
        title="JRE #1",
        episode_number=1,
    )
    db_session.add(episode)
    db_session.flush()
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

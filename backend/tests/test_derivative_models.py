"""Round-trip tests for the derivative-layer models."""

from datetime import UTC, datetime

from assertpy import assert_that
from sqlalchemy import select
from sqlalchemy.orm import Session

from podex.models import (
    DerivativeGenerationRun,
    DerivativeGenerationRunStatus,
    EpisodeSummary,
    GraphTriple,
    GraphTripleObjectKind,
    MediaSummary,
    SemanticChunk,
    SemanticChunkEmbeddingStatus,
    Transcript,
)
from tests.conftest import seed_catalog_graph

_NOW = datetime(2026, 7, 18, tzinfo=UTC)


def _transcript(db_session: Session, episode_id: int) -> Transcript:
    transcript = Transcript(
        episode_id=episode_id,
        provider="podscripts",
        cleaned_text="clean words",
    )
    db_session.add(transcript)
    db_session.commit()
    return transcript


def test_semantic_chunk_round_trips_with_embedding(
    db_session: Session,
) -> None:
    """Chunks persist text, hashes, and embedding vectors."""
    graph = seed_catalog_graph(db_session)
    transcript = _transcript(db_session, graph.episode_id)

    chunk = SemanticChunk(
        episode_id=graph.episode_id,
        transcript_id=transcript.id,
        chunk_key="ep1:0:v1",
        chunk_index=0,
        pipeline_version="v1",
        source_text_hash="a" * 64,
        text="clean words",
        context_snippet="…clean words…",
        token_count=2,
        embedding_status=SemanticChunkEmbeddingStatus.PENDING.value,
        embedding_vector=[0.1, 0.2, 0.3],
    )
    db_session.add(chunk)
    db_session.commit()

    stored = db_session.execute(select(SemanticChunk)).scalar_one()
    assert_that(stored.chunk_key).is_equal_to("ep1:0:v1")
    assert_that(stored.embedding_vector).is_equal_to([0.1, 0.2, 0.3])
    assert_that(stored.episode.semantic_chunks).contains(stored)
    assert_that(stored.transcript.semantic_chunks).contains(stored)


def test_episode_and_media_summaries_round_trip(db_session: Session) -> None:
    """Summaries persist versioned provenance for episodes and media."""
    graph = seed_catalog_graph(db_session)

    episode_summary = EpisodeSummary(
        episode_id=graph.episode_id,
        summary_key="ep1:overview:v1",
        summary_kind="overview",
        pipeline_version="v1",
        prompt_version="p1",
        source_text_hash="b" * 64,
        source_model="claude-opus-4-8",
        summary_text="An overview.",
        generated_at=_NOW,
    )
    media_summary = MediaSummary(
        media_id=graph.media_id,
        summary_key="med1:overview:v1",
        summary_kind="overview",
        pipeline_version="v1",
        prompt_version="p1",
        source_text_hash="c" * 64,
        source_model="claude-opus-4-8",
        summary_text="A media overview.",
        generated_at=_NOW,
    )
    db_session.add_all([episode_summary, media_summary])
    db_session.commit()

    stored_ep = db_session.execute(select(EpisodeSummary)).scalar_one()
    stored_med = db_session.execute(select(MediaSummary)).scalar_one()
    assert_that(stored_ep.episode.summaries).contains(stored_ep)
    assert_that(stored_med.media.summaries).contains(stored_med)


def test_graph_triple_round_trips_both_object_kinds(
    db_session: Session,
) -> None:
    """Triples support media objects and literal values."""
    graph = seed_catalog_graph(db_session)

    media_triple = GraphTriple(
        triple_key="t1",
        subject_media_id=graph.media_id,
        predicate="similar_to",
        object_kind=GraphTripleObjectKind.MEDIA.value,
        object_media_id=graph.media_id,
        source="test",
        confidence=0.9,
    )
    value_triple = GraphTriple(
        triple_key="t2",
        subject_media_id=graph.media_id,
        predicate="published_year",
        object_kind=GraphTripleObjectKind.LITERAL.value,
        object_value="1965",
        source="test",
        confidence=1.0,
    )
    db_session.add_all([media_triple, value_triple])
    db_session.commit()

    stored = db_session.execute(select(GraphTriple)).scalars().all()
    assert_that(stored).is_length(2)
    media = stored[0].subject_media
    assert_that(media.subject_graph_triples).is_length(2)


def test_derivative_generation_run_links(db_session: Session) -> None:
    """Runs link episodes, transcripts, and summaries with a run key."""
    graph = seed_catalog_graph(db_session)
    transcript = _transcript(db_session, graph.episode_id)

    run = DerivativeGenerationRun(
        run_key="run1",
        episode_id=graph.episode_id,
        transcript_id=transcript.id,
        status=DerivativeGenerationRunStatus.RUNNING.value,
        pipeline_version="v1",
        chunk_pipeline_version="v1",
        summary_prompt_version="p1",
        summary_model="claude-opus-4-8",
        source_text_hash="d" * 64,
        started_at=_NOW,
    )
    db_session.add(run)
    db_session.commit()

    stored = db_session.execute(
        select(DerivativeGenerationRun),
    ).scalar_one()
    assert_that(stored.episode.derivative_runs).contains(stored)
    assert_that(stored.transcript.derivative_runs).contains(stored)

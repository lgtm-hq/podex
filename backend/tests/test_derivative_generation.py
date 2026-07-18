"""Tests for derivative generation orchestration."""

from datetime import UTC, datetime

from assertpy import assert_that
from sqlalchemy.orm import Session

from podex.models import (
    DerivativeGenerationRun,
    DerivativeGenerationRunStatus,
    Episode,
    EpisodeSummary,
    GraphTriple,
    Media,
    MediaSummary,
    MediaType,
    Mention,
    Podcast,
    SemanticChunk,
    Transcript,
)
from podex.services.derivative_generation import (
    DerivativeGenerationConfig,
    generate_episode_derivatives,
)
from podex.services.derivative_summaries import GeneratedSummaryData, SummarySourceData
from podex.services.graph_relations import GraphTripleInputData
from podex.services.semantic_chunks import SemanticChunkingPolicy


class FakeSummaryProvider:
    """Deterministic summary provider for orchestration tests."""

    model_name = "fake-summary-v1"

    def summarize(
        self,
        source: SummarySourceData,
    ) -> GeneratedSummaryData:
        """Return stable text from the supplied source."""
        return GeneratedSummaryData(
            summary_text=f"{source.resource_kind} summary",
            short_summary=f"{source.resource_kind} short",
        )


def test_generate_episode_derivatives_orchestrates_derivative_layers(
    db_session: Session,
) -> None:
    """Verify one orchestration run persists chunks, summaries, and triples."""
    episode, transcript, media = _create_episode_graph(db_session)
    config = DerivativeGenerationConfig(
        pipeline_version="derivatives-v1",
        chunk_pipeline_version="chunks-v1",
        summary_prompt_version="summary-v1",
    )
    now = datetime(2026, 5, 22, tzinfo=UTC)

    run = generate_episode_derivatives(
        db=db_session,
        episode=episode,
        transcript=transcript,
        config=config,
        summary_provider=FakeSummaryProvider(),
        chunking_policy=SemanticChunkingPolicy(
            max_words=6,
            overlap_words=0,
            min_words=2,
            pipeline_version=config.chunk_pipeline_version,
        ),
        graph_triples=[
            GraphTripleInputData(
                subject_media_id=media.id,
                predicate="mentioned_context",
                object_value="discussed on JRE",
                source="orchestration-test",
                provenance_episode_id=episode.id,
            ),
        ],
        now=now,
    )
    db_session.commit()

    assert_that(run.status).is_equal_to(DerivativeGenerationRunStatus.COMPLETED.value)
    assert_that(run.semantic_chunks_created).is_equal_to(1)
    assert_that(run.media_summaries_generated).is_equal_to(1)
    assert_that(run.graph_triples_upserted).is_equal_to(1)
    completed_at = run.completed_at
    assert_that(completed_at).is_not_none()
    if completed_at is None:  # pragma: no cover - narrowed above
        raise AssertionError
    assert_that(completed_at.replace(tzinfo=UTC)).is_equal_to(now)
    assert_that(db_session.query(SemanticChunk).count()).is_equal_to(1)
    assert_that(db_session.query(EpisodeSummary).count()).is_equal_to(1)
    assert_that(db_session.query(MediaSummary).count()).is_equal_to(1)
    assert_that(db_session.query(GraphTriple).count()).is_equal_to(1)


def test_generate_episode_derivatives_is_replay_safe(
    db_session: Session,
) -> None:
    """Verify replaying the same run does not duplicate derivatives."""
    episode, transcript, _media = _create_episode_graph(db_session)
    config = DerivativeGenerationConfig(
        pipeline_version="derivatives-v1",
        chunk_pipeline_version="chunks-v1",
        summary_prompt_version="summary-v1",
    )

    first = generate_episode_derivatives(
        db=db_session,
        episode=episode,
        transcript=transcript,
        config=config,
        summary_provider=FakeSummaryProvider(),
        chunking_policy=SemanticChunkingPolicy(
            max_words=6,
            overlap_words=0,
            min_words=2,
            pipeline_version=config.chunk_pipeline_version,
        ),
    )
    second = generate_episode_derivatives(
        db=db_session,
        episode=episode,
        transcript=transcript,
        config=config,
        summary_provider=FakeSummaryProvider(),
        chunking_policy=SemanticChunkingPolicy(
            max_words=6,
            overlap_words=0,
            min_words=2,
            pipeline_version=config.chunk_pipeline_version,
        ),
    )
    db_session.commit()

    assert_that(second.id).is_equal_to(first.id)
    assert_that(second.semantic_chunks_created).is_equal_to(0)
    assert_that(second.semantic_chunks_updated).is_equal_to(0)
    assert_that(db_session.query(DerivativeGenerationRun).count()).is_equal_to(1)
    assert_that(db_session.query(SemanticChunk).count()).is_equal_to(1)
    assert_that(db_session.query(EpisodeSummary).count()).is_equal_to(1)
    assert_that(db_session.query(MediaSummary).count()).is_equal_to(1)


def _create_episode_graph(
    db_session: Session,
) -> tuple[Episode, Transcript, Media]:
    """Create a persisted JRE episode with transcript and media mention."""
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
        raw_text="Joe mentions a book during the conversation.",
        cleaned_text="Joe mentions a book during the conversation.",
    )
    media = Media(type=MediaType.BOOK.value, title="Dune")
    db_session.add_all([transcript, media])
    db_session.flush()
    db_session.add(
        Mention(
            episode_id=episode.id,
            media_id=media.id,
            context="Joe mentions Dune during the conversation.",
        ),
    )
    db_session.flush()
    return episode, transcript, media

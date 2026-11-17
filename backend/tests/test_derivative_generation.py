"""Tests for derivative generation orchestration."""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from assertpy import assert_that
from sqlalchemy import select
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
    SemanticChunk,
    Transcript,
)
from podex.services.derivative_generation import (
    DerivativeGenerationConfig,
    _episode_media,
    generate_episode_derivatives,
)
from podex.services.derivative_summaries import GeneratedSummaryData, SummarySourceData
from podex.services.graph_relations import GraphTripleInputData
from podex.services.semantic_chunks import SemanticChunkingPolicy
from tests.conftest import create_jre_episode
from tests.json_comparison import captured_statements, json_distinct_columns


# This adapter implements one protocol operation; extra methods have no purpose.
# pylint: disable-next=too-few-public-methods
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
    episode = create_jre_episode(db_session)
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


@dataclass
class RecordingSummaryProvider:
    """Summary provider that records each source it summarises, in order."""

    model_name: str = "fake-summary-v1"
    sources: list[tuple[str, int]] = field(default_factory=list)

    def summarize(
        self,
        source: SummarySourceData,
    ) -> GeneratedSummaryData:
        """Record the source, then return stable text."""
        self.sources.append((source.resource_kind, source.resource_id))
        return FakeSummaryProvider().summarize(source)


def media_with_json(title: str) -> Media:
    """Return an unsaved book whose JSON columns hold values."""
    return Media(
        type=MediaType.BOOK.value,
        title=title,
        metadata_json={"source": "test"},
        verification_sources=["test"],
    )


def test_json_distinct_columns_flags_distinct_media_rows() -> None:
    """The PostgreSQL check names the JSON columns a whole-row DISTINCT compares."""
    statement = select(Media).join(Mention, Mention.media_id == Media.id).distinct()

    assert_that(json_distinct_columns(statement)).contains(
        "media.metadata_json",
        "media.verification_sources",
    )


def test_episode_media_query_compares_no_json_column_on_postgresql(
    db_session: Session,
) -> None:
    """Listing an episode's media compiles to SQL PostgreSQL accepts."""
    episode, first, second, _other = _create_repeated_mentions(db_session)

    with captured_statements(db_session) as statements:
        media = _episode_media(db=db_session, episode=episode)

    assert_that([item.id for item in media]).is_equal_to([first.id, second.id])
    assert_that(statements).is_not_empty()
    for statement in statements:
        assert_that(json_distinct_columns(statement)).is_empty()


def test_generate_episode_derivatives_summarises_each_media_once_in_id_order(
    db_session: Session,
) -> None:
    """Repeated mentions yield one media summary per record, in ascending id."""
    episode, first, second, other = _create_repeated_mentions(db_session)
    provider = RecordingSummaryProvider()

    run = generate_episode_derivatives(
        db=db_session,
        episode=episode,
        summary_provider=provider,
    )
    db_session.commit()

    media_sources = [
        resource_id for kind, resource_id in provider.sources if kind == "media"
    ]
    assert_that(media_sources).is_equal_to([first.id, second.id])
    assert_that(run.status).is_equal_to(DerivativeGenerationRunStatus.COMPLETED.value)
    assert_that(run.media_summaries_generated).is_equal_to(2)
    summarised = [
        media_id
        for (media_id,) in db_session.query(MediaSummary.media_id)
        .order_by(MediaSummary.media_id)
        .all()
    ]
    assert_that(summarised).is_equal_to([first.id, second.id])
    assert_that(summarised).does_not_contain(other.id)


def _create_repeated_mentions(
    db_session: Session,
) -> tuple[Episode, Media, Media, Media]:
    """Create an episode mentioning two media records several times each.

    The record with the higher id is mentioned first, and a third record is
    mentioned only in another episode. Every record carries JSON values.

    Returns:
        The episode, its two media records in ascending id, and the record
        mentioned only elsewhere.
    """
    episode = create_jre_episode(db_session)
    elsewhere = Episode(podcast_id=episode.podcast_id, title="JRE #2")
    first, second, other = (
        media_with_json(title) for title in ("Dune", "Foundation", "Hyperion")
    )
    db_session.add_all([elsewhere, first, second, other])
    db_session.flush()
    db_session.add_all(
        [
            Mention(episode_id=episode.id, media_id=media.id, context=context)
            for media, context in (
                (second, "Foundation comes up first."),
                (first, "Then Dune."),
                (second, "Foundation again."),
                (first, "Dune again."),
            )
        ]
        + [Mention(episode_id=elsewhere.id, media_id=other.id, context="Hyperion.")],
    )
    db_session.flush()
    return episode, first, second, other

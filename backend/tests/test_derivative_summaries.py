"""Tests for durable episode and media derivative summaries."""

from datetime import UTC, datetime

from assertpy import assert_that
from sqlalchemy.orm import Session

from podex.models import (
    DerivativeSummaryKind,
    Episode,
    EpisodeSummary,
    Media,
    MediaSummary,
    MediaType,
    Mention,
    Podcast,
    SemanticChunk,
    Transcript,
)
from podex.services.derivative_summaries import (
    GeneratedSummaryData,
    SummarySourceData,
    build_episode_summary_source,
    generate_episode_summary,
    generate_media_summary,
    stable_source_text_hash,
    upsert_episode_summary,
)


class FakeSummaryProvider:
    """Deterministic summary provider for derivative summary tests."""

    model_name = "fake-summary-v1"

    def summarize(
        self,
        source: SummarySourceData,
    ) -> GeneratedSummaryData:
        """Return a stable summary derived from source metadata."""
        return GeneratedSummaryData(
            summary_text=f"{source.resource_kind}:{source.source_text[:30]}",
            short_summary=f"{source.resource_kind} short",
            highlights=[source.source_text_hash[:8]],
            citations=[
                {
                    "resource_kind": source.resource_kind,
                    "resource_id": source.resource_id,
                },
            ],
            metadata_json={"provider": "fake"},
        )


def test_upsert_episode_summary_is_replay_safe(
    db_session: Session,
) -> None:
    """Verify replaying the same summary version updates without duplicates."""
    episode = _create_episode(db_session)
    source_hash = stable_source_text_hash("episode source")
    now = datetime(2026, 5, 22, tzinfo=UTC)

    first = upsert_episode_summary(
        db=db_session,
        episode=episode,
        summary_kind=DerivativeSummaryKind.OVERVIEW,
        pipeline_version="derivatives-v1",
        prompt_version="episode-prompt-v1",
        source_model="summary-model-v1",
        source_text_hash=source_hash,
        summary_text="Initial summary",
        generated_at=now,
    )
    second = upsert_episode_summary(
        db=db_session,
        episode=episode,
        summary_kind=DerivativeSummaryKind.OVERVIEW,
        pipeline_version="derivatives-v1",
        prompt_version="episode-prompt-v1",
        source_model="summary-model-v1",
        source_text_hash=source_hash,
        summary_text="Updated summary",
        short_summary="Short",
        highlights_json=["first highlight"],
        generated_at=now,
    )
    db_session.commit()

    assert_that(second.id).is_equal_to(first.id)
    assert_that(second.summary_text).is_equal_to("Updated summary")
    assert_that(second.short_summary).is_equal_to("Short")
    assert_that(second.highlights_json).is_equal_to(["first highlight"])
    assert_that(db_session.query(EpisodeSummary).count()).is_equal_to(1)


def test_episode_summary_source_prefers_semantic_chunks(
    db_session: Session,
) -> None:
    """Verify episode summaries use semantic chunks before raw transcript text."""
    episode = _create_episode(db_session)
    transcript = Transcript(
        episode_id=episode.id,
        provider="podscripts",
        raw_text="raw transcript",
        cleaned_text="cleaned transcript",
    )
    db_session.add(transcript)
    db_session.flush()
    db_session.add(
        SemanticChunk(
            episode_id=episode.id,
            transcript_id=transcript.id,
            chunk_key="chunk-key-1",
            chunk_index=0,
            pipeline_version="chunks-v1",
            source_text_hash=stable_source_text_hash("cleaned transcript"),
            text="semantic chunk text",
            context_snippet="semantic chunk text",
            token_count=3,
        ),
    )
    db_session.flush()

    source = build_episode_summary_source(db=db_session, episode=episode)

    assert_that(source.source_text).is_equal_to("semantic chunk text")
    assert_that(source.metadata_json["source"]).is_equal_to("semantic_chunks")
    assert_that(source.metadata_json["chunk_count"]).is_equal_to(1)


def test_generate_media_summary_is_version_tracked(
    db_session: Session,
) -> None:
    """Verify media summary generation persists provenance and model metadata."""
    episode = _create_episode(db_session)
    media = Media(
        type=MediaType.BOOK.value,
        title="Dune",
        author="Frank Herbert",
        year=1965,
        description="A desert planet epic.",
    )
    db_session.add(media)
    db_session.flush()
    db_session.add(
        Mention(
            episode_id=episode.id,
            media_id=media.id,
            context="Joe discussed Dune and its ecology.",
        ),
    )
    db_session.flush()

    first = generate_media_summary(
        db=db_session,
        media=media,
        provider=FakeSummaryProvider(),
        pipeline_version="derivatives-v1",
        prompt_version="media-prompt-v1",
    )
    second = generate_media_summary(
        db=db_session,
        media=media,
        provider=FakeSummaryProvider(),
        pipeline_version="derivatives-v1",
        prompt_version="media-prompt-v1",
    )
    db_session.commit()

    assert_that(second.id).is_equal_to(first.id)
    assert_that(second.source_model).is_equal_to("fake-summary-v1")
    assert_that(second.summary_text).contains("media:Title: Dune")
    assert_that(second.metadata_json).contains_key("mention_count")
    assert_that(second.metadata_json).contains_key("provider")
    assert_that(db_session.query(MediaSummary).count()).is_equal_to(1)


def test_generate_episode_summary_creates_new_record_when_source_changes(
    db_session: Session,
) -> None:
    """Verify source hash changes produce auditable summary versions."""
    episode = _create_episode(db_session)
    transcript = Transcript(
        episode_id=episode.id,
        provider="podscripts",
        raw_text="first source",
        cleaned_text="first source",
    )
    db_session.add(transcript)
    db_session.flush()

    first = generate_episode_summary(
        db=db_session,
        episode=episode,
        provider=FakeSummaryProvider(),
        pipeline_version="derivatives-v1",
        prompt_version="episode-prompt-v1",
    )
    transcript.cleaned_text = "changed source"
    second = generate_episode_summary(
        db=db_session,
        episode=episode,
        provider=FakeSummaryProvider(),
        pipeline_version="derivatives-v1",
        prompt_version="episode-prompt-v1",
    )
    db_session.commit()

    assert_that(second.id).is_not_equal_to(first.id)
    assert_that(second.source_text_hash).is_not_equal_to(first.source_text_hash)
    assert_that(db_session.query(EpisodeSummary).count()).is_equal_to(2)


def _create_episode(
    db_session: Session,
) -> Episode:
    """Create a persisted JRE episode."""
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
    return episode

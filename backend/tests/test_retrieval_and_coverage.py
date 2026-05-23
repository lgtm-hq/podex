"""Tests for derivative retrieval ports and purge coverage gates."""

from assertpy import assert_that
from sqlalchemy.orm import Session

from podex.models import (
    DerivativeSummaryKind,
    Episode,
    Media,
    MediaType,
    Mention,
    Podcast,
    SemanticChunk,
    Transcript,
)
from podex.services.derivative_coverage import (
    PublicDerivativeQueryClass,
    evaluate_episode_derivative_coverage,
)
from podex.services.derivative_summaries import (
    stable_source_text_hash,
    upsert_episode_summary,
    upsert_media_summary,
)
from podex.services.graph_relations import GraphTripleInputData, upsert_graph_triple
from podex.services.retrieval_ports import (
    PostgresDerivativeRetrievalPort,
    RetrievalHitKind,
    RetrievalQueryData,
)


def test_retrieval_port_searches_summaries_chunks_and_graph(
    db_session: Session,
) -> None:
    """Verify retrieval is exposed through the derivative port."""
    episode, transcript, media = _create_episode_media(db_session)
    _persist_complete_derivatives(
        db_session=db_session,
        episode=episode,
        transcript=transcript,
        media=media,
    )

    result = PostgresDerivativeRetrievalPort(db_session).search(
        RetrievalQueryData(query="desert ecology", limit=10),
    )

    hit_kinds = {hit.hit_kind for hit in result.hits}
    assert_that(hit_kinds).contains(RetrievalHitKind.MEDIA_SUMMARY)
    assert_that(hit_kinds).contains(RetrievalHitKind.SEMANTIC_CHUNK)
    assert_that(hit_kinds).contains(RetrievalHitKind.GRAPH_TRIPLE)
    assert_that(result.hits[0].score).is_greater_than(0)


def test_derivative_coverage_blocks_purge_until_public_queries_are_covered(
    db_session: Session,
) -> None:
    """Verify retention purge is blocked until derivative coverage is complete."""
    episode, transcript, media = _create_episode_media(db_session)

    missing = evaluate_episode_derivative_coverage(
        db=db_session,
        episode=episode,
    )
    _persist_complete_derivatives(
        db_session=db_session,
        episode=episode,
        transcript=transcript,
        media=media,
    )
    covered = evaluate_episode_derivative_coverage(
        db=db_session,
        episode=episode,
    )

    assert_that(missing.purge_safe).is_false()
    assert_that(missing.missing_query_classes).contains(
        PublicDerivativeQueryClass.GLOBAL_SEARCH,
    )
    assert_that(covered.purge_safe).is_true()
    assert_that(covered.missing_query_classes).is_empty()
    assert_that(covered.covered_query_classes).contains(
        PublicDerivativeQueryClass.EPISODE_DETAIL,
    )


def _create_episode_media(
    db_session: Session,
) -> tuple[Episode, Transcript, Media]:
    """Create a persisted JRE episode with a media mention."""
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
        raw_text="A conversation about Dune and desert ecology.",
        cleaned_text="A conversation about Dune and desert ecology.",
    )
    media = Media(type=MediaType.BOOK.value, title="Dune")
    db_session.add_all([transcript, media])
    db_session.flush()
    db_session.add(
        Mention(
            episode_id=episode.id,
            media_id=media.id,
            context="Joe mentions Dune and desert ecology.",
        ),
    )
    db_session.flush()
    return episode, transcript, media


def _persist_complete_derivatives(
    *,
    db_session: Session,
    episode: Episode,
    transcript: Transcript,
    media: Media,
) -> None:
    """Persist the derivative set required for public query coverage."""
    db_session.add(
        SemanticChunk(
            episode_id=episode.id,
            transcript_id=transcript.id,
            chunk_key="coverage-chunk-1",
            chunk_index=0,
            pipeline_version="chunks-v1",
            source_text_hash=stable_source_text_hash(transcript.cleaned_text or ""),
            text="Dune is discussed through desert ecology and myth.",
            context_snippet="Dune desert ecology",
            token_count=9,
        ),
    )
    upsert_episode_summary(
        db=db_session,
        episode=episode,
        summary_kind=DerivativeSummaryKind.OVERVIEW,
        pipeline_version="derivatives-v1",
        prompt_version="episode-summary-v1",
        source_model="summary-model-v1",
        source_text_hash=stable_source_text_hash("episode summary source"),
        summary_text="The episode covers Dune and desert ecology.",
    )
    upsert_media_summary(
        db=db_session,
        media=media,
        summary_kind=DerivativeSummaryKind.OVERVIEW,
        pipeline_version="derivatives-v1",
        prompt_version="media-summary-v1",
        source_model="summary-model-v1",
        source_text_hash=stable_source_text_hash("media summary source"),
        summary_text="Dune is a desert ecology novel mentioned on JRE.",
    )
    upsert_graph_triple(
        db=db_session,
        payload=GraphTripleInputData(
            subject_media_id=media.id,
            predicate="topic",
            object_value="desert ecology",
            provenance_episode_id=episode.id,
            source="coverage-test",
            evidence_text="Dune was connected to desert ecology.",
        ),
    )
    db_session.flush()

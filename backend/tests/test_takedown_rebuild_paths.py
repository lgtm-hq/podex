"""Every path that can write content again, run after an approved takedown.

Each test approves a takedown and then runs one rebuild, re-extraction,
re-publication or merge path, and checks that nothing suppressed is written
or visible through the public API.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from assertpy import assert_that
from fastapi.testclient import TestClient
from sqlalchemy import insert
from sqlalchemy.orm import Session

from podex.models import (
    GUARDED_MODELS,
    Base,
    ContentSuppressedError,
    ContentSuppressionScope,
    DerivativeGenerationRun,
    Episode,
    EpisodeSummary,
    GraphTriple,
    Media,
    MediaRelation,
    MediaType,
    Mention,
    MentionCandidate,
    Podcast,
    SemanticChunk,
    TakedownSubjectType,
    Transcript,
)
from podex.services.content_suppression import (
    is_episode_suppressed,
    is_mention_pair_suppressed,
    suppressed_episode_ids,
)
from podex.services.derivative_generation import generate_episode_derivatives
from podex.services.derivative_summaries import (
    build_media_summary_source,
    generate_episode_summary,
)
from podex.services.extraction import materialize_mentions
from podex.services.extraction_review import persist_extracted_candidates
from podex.services.graph_relations import GraphTripleInputData, upsert_graph_triple
from podex.services.llm_extraction import ExtractedMedia
from podex.services.ops_media_commands import merge_ops_media
from podex.services.semantic_chunks import (
    SemanticChunkingPolicy,
    sync_semantic_chunks_for_transcript,
)
from tests.conftest import SeededGraph, seed_catalog_graph
from tests.takedown_support import (
    approve_takedown,
    take_down_mention,
    take_down_new_mention,
    write_episode_summary,
)

_TEXT = "one two three four five six"
_POLICY = SemanticChunkingPolicy(max_words=6, overlap_words=0, min_words=2)
_GENERATED_AT = datetime(2026, 7, 1, tzinfo=UTC)


def _episode(db: Session, episode_id: int) -> Episode:
    """Load an episode that the test seeded."""
    return db.query(Episode).filter(Episode.id == episode_id).one()


def _transcript(db: Session, episode_id: int) -> Transcript:
    """Persist and commit a chunkable transcript for an episode."""
    transcript = Transcript(
        episode_id=episode_id, provider="podscripts", raw_text=_TEXT
    )
    db.add(transcript)
    db.commit()
    return transcript


def _suppress(db: Session, graph: SeededGraph, *actions: str) -> None:
    """Approve an episode-level takedown of the seeded episode."""
    approve_takedown(
        db,
        subject_type=TakedownSubjectType.EPISODE,
        subject_id=graph.episode_id,
        actions=list(actions),
    )


def _derived_rows(db: Session, episode_id: int) -> dict[str, int]:
    """Count the episode's stored derived rows of every guarded kind."""
    db.expire_all()
    return {
        "chunks": db.query(SemanticChunk)
        .filter(SemanticChunk.episode_id == episode_id)
        .count(),
        "summaries": db.query(EpisodeSummary)
        .filter(EpisodeSummary.episode_id == episode_id)
        .count(),
        "runs": db.query(DerivativeGenerationRun)
        .filter(DerivativeGenerationRun.episode_id == episode_id)
        .count(),
        "triples": db.query(GraphTriple)
        .filter(GraphTriple.provenance_episode_id == episode_id)
        .count(),
        "cleaned": db.query(Transcript)
        .filter(
            Transcript.episode_id == episode_id,
            Transcript.cleaned_text.is_not(None),
        )
        .count(),
    }


_NO_DERIVED_ROWS = {"chunks": 0, "summaries": 0, "runs": 0, "triples": 0, "cleaned": 0}


def test_chunk_sync_is_refused_before_embedding(db_session: Session) -> None:
    """Re-chunking a suppressed transcript calls no embedding provider."""
    graph = seed_catalog_graph(db_session)
    transcript = _transcript(db_session, graph.episode_id)
    _suppress(db_session, graph, "suppress_derivatives")
    provider = Mock()

    with pytest.raises(ContentSuppressedError):
        sync_semantic_chunks_for_transcript(
            db=db_session,
            transcript=transcript,
            embedding_provider=provider,
            policy=_POLICY,
        )

    assert_that(provider.mock_calls).is_empty()
    assert_that(_derived_rows(db_session, graph.episode_id)).is_equal_to(
        _NO_DERIVED_ROWS,
    )


def test_episode_summary_is_refused_before_the_provider(db_session: Session) -> None:
    """Re-summarizing a suppressed episode never sends its text to a provider."""
    graph = seed_catalog_graph(db_session)
    _transcript(db_session, graph.episode_id)
    _suppress(db_session, graph, "suppress_derivatives")
    provider = Mock()

    with pytest.raises(ContentSuppressedError):
        generate_episode_summary(
            db=db_session,
            episode=_episode(db_session, graph.episode_id),
            provider=provider,
            pipeline_version="v1",
            prompt_version="p1",
        )

    assert_that(provider.mock_calls).is_empty()


def test_direct_derived_writes_are_refused_at_flush(db_session: Session) -> None:
    """Lower-level writers with no pre-check of their own still write nothing."""
    graph = seed_catalog_graph(db_session)
    _transcript(db_session, graph.episode_id)
    _suppress(db_session, graph, "suppress_derivatives")
    episode = _episode(db_session, graph.episode_id)
    writes: list[Callable[[], object]] = [
        lambda: write_episode_summary(db_session, episode, "Regenerated summary."),
        lambda: upsert_graph_triple(
            db=db_session,
            payload=GraphTripleInputData(
                subject_media_id=graph.media_id,
                predicate="mentioned_context",
                object_value="discussed again",
                source="rebuild",
                provenance_episode_id=graph.episode_id,
            ),
        ),
        lambda: setattr(
            db_session.query(Transcript).one(),
            "cleaned_text",
            "cleaned again",
        ),
        lambda: db_session.add(
            DerivativeGenerationRun(
                run_key="rebuild",
                episode_id=graph.episode_id,
                pipeline_version="v1",
                chunk_pipeline_version="c1",
                summary_prompt_version="p1",
                source_text_hash="b" * 64,
                started_at=_GENERATED_AT,
            ),
        ),
    ]

    for write in writes:
        with pytest.raises(ContentSuppressedError):
            write()
            db_session.commit()
        db_session.rollback()

    assert_that(_derived_rows(db_session, graph.episode_id)).is_equal_to(
        _NO_DERIVED_ROWS,
    )


def test_republished_mention_is_refused_and_never_listed(
    client: TestClient,
    db_session: Session,
) -> None:
    """Re-extracted mentions of a taken-down pair are not published again."""
    graph = seed_catalog_graph(db_session)
    take_down_mention(db_session, graph.mention_id)
    media = db_session.query(Media).filter(Media.id == graph.media_id).one()
    segments = [{"text": "I loved reading Dune last year", "start": 42}]

    db_session.add_all(
        materialize_mentions(graph.episode_id, segments, [media], Mock()),
    )
    with pytest.raises(ContentSuppressedError):
        db_session.commit()
    db_session.rollback()

    assert_that(db_session.query(Mention).count()).is_zero()
    episode_mentions = client.get(f"/api/v2/episodes/{graph.episode_id}/mentions")
    assert_that(episode_mentions.json()["total"]).is_zero()
    assert_that(client.get("/api/v2/stats").json()["mentions"]).is_zero()


def test_reextraction_after_takedown_publishes_nothing(
    client: TestClient,
    db_session: Session,
) -> None:
    """Re-extraction writes review candidates only; nothing public reappears."""
    graph = seed_catalog_graph(db_session)
    take_down_mention(db_session, graph.mention_id)

    persist_extracted_candidates(
        db=db_session,
        episode=_episode(db_session, graph.episode_id),
        items=[ExtractedMedia(title="Dune", media_type=MediaType.BOOK)],
        segments=[{"text": "Dune is a great book", "start": 42}],
        min_confidence=0.1,
        extraction_source="llm",
    )
    db_session.commit()

    assert_that(db_session.query(MentionCandidate).count()).is_positive()
    assert_that(db_session.query(Mention).count()).is_zero()
    listed = client.get(f"/api/v2/media/{graph.media_id}/mentions")
    assert_that(listed.json()["total"]).is_zero()


def test_episode_takedown_refuses_every_new_mention_in_it(db_session: Session) -> None:
    """An episode-level unpublish covers mentions of any media in the episode."""
    graph = seed_catalog_graph(db_session)
    _suppress(db_session, graph, "unpublish_mentions")
    other = Media(type=MediaType.MOVIE, title="Arrival")
    db_session.add(other)
    db_session.commit()

    db_session.add(Mention(episode_id=graph.episode_id, media_id=other.id))
    with pytest.raises(ContentSuppressedError):
        db_session.commit()
    db_session.rollback()

    assert_that(db_session.query(Mention).count()).is_zero()


def test_podcast_takedown_covers_episodes_discovered_later(
    db_session: Session,
) -> None:
    """A podcast suppression applies to episodes added after its approval."""
    graph = seed_catalog_graph(db_session)
    approve_takedown(
        db_session,
        subject_type=TakedownSubjectType.PODCAST,
        subject_id=graph.podcast_id,
        actions=["unpublish_mentions", "suppress_derivatives"],
    )
    later = Episode(podcast_id=graph.podcast_id, title="Rediscovered")
    db_session.add(later)
    db_session.commit()
    transcript = _transcript(db_session, later.id)

    with pytest.raises(ContentSuppressedError):
        generate_episode_derivatives(db=db_session, episode=later)
    with pytest.raises(ContentSuppressedError):
        sync_semantic_chunks_for_transcript(db=db_session, transcript=transcript)
    db_session.add(Mention(episode_id=later.id, media_id=graph.media_id))
    with pytest.raises(ContentSuppressedError):
        db_session.commit()
    db_session.rollback()

    assert_that(_derived_rows(db_session, later.id)).is_equal_to(_NO_DERIVED_ROWS)
    assert_that(db_session.query(Mention).count()).is_zero()


def _merge_fixture(db: Session) -> tuple[SeededGraph, Media, Episode]:
    """Seed a duplicate media record mentioned in a second episode.

    Returns:
        The graph, the duplicate record and the second episode.
    """
    graph = seed_catalog_graph(db)
    duplicate = Media(type=MediaType.BOOK, title="Dune (dupe)")
    second = Episode(podcast_id=graph.podcast_id, title="Second")
    db.add_all([duplicate, second])
    db.commit()
    return graph, duplicate, second


def test_merge_cannot_move_a_mention_into_a_suppressed_pair(
    client: TestClient,
    db_session: Session,
) -> None:
    """Merging a duplicate record does not republish a taken-down pair."""
    graph, duplicate, _ = _merge_fixture(db_session)
    db_session.add(Mention(episode_id=graph.episode_id, media_id=duplicate.id))
    db_session.commit()
    take_down_mention(db_session, graph.mention_id)

    with pytest.raises(ContentSuppressedError):
        merge_ops_media(
            db=db_session,
            source_media_id=duplicate.id,
            target_media_id=graph.media_id,
        )
    db_session.rollback()

    listed = client.get(f"/api/v2/media/{graph.media_id}/mentions")
    assert_that(listed.json()["total"]).is_zero()
    assert_that(db_session.get(Media, duplicate.id)).is_not_none()


def test_merge_carries_pair_suppression_to_the_survivor(db_session: Session) -> None:
    """A taken-down pair on a merged-away record stays suppressed on the target."""
    graph, duplicate, second = _merge_fixture(db_session)
    take_down_new_mention(db_session, episode_id=second.id, media_id=duplicate.id)

    merge_ops_media(
        db=db_session,
        source_media_id=duplicate.id,
        target_media_id=graph.media_id,
    )
    db_session.commit()

    assert_that(
        is_mention_pair_suppressed(
            db=db_session,
            episode_id=second.id,
            media_id=graph.media_id,
        ),
    ).is_true()
    db_session.add(Mention(episode_id=second.id, media_id=graph.media_id))
    with pytest.raises(ContentSuppressedError):
        db_session.commit()
    db_session.rollback()


def test_merge_into_an_already_suppressed_pair_keeps_one_row(
    db_session: Session,
) -> None:
    """When both records were taken down in one episode, one suppression stays."""
    graph, duplicate, _ = _merge_fixture(db_session)
    duplicate_mention = Mention(episode_id=graph.episode_id, media_id=duplicate.id)
    db_session.add(duplicate_mention)
    db_session.commit()
    for mention_id in (graph.mention_id, duplicate_mention.id):
        take_down_mention(db_session, mention_id)

    merge_ops_media(
        db=db_session,
        source_media_id=duplicate.id,
        target_media_id=graph.media_id,
    )
    db_session.commit()

    assert_that(
        is_mention_pair_suppressed(
            db=db_session,
            episode_id=graph.episode_id,
            media_id=graph.media_id,
        ),
    ).is_true()


def test_public_reads_hide_mentions_written_around_the_guard(
    client: TestClient,
    db_session: Session,
) -> None:
    """A bulk insert skips the flush guard; public reads still filter it out."""
    graph = seed_catalog_graph(db_session)
    take_down_mention(db_session, graph.mention_id)
    db_session.execute(
        insert(Mention).values(
            episode_id=graph.episode_id,
            media_id=graph.media_id,
            context="bulk restored",
        ),
    )
    db_session.commit()
    media = db_session.query(Media).filter(Media.id == graph.media_id).one()

    for path in (
        f"/api/v2/episodes/{graph.episode_id}/mentions",
        f"/api/v2/media/{graph.media_id}/mentions",
    ):
        body = client.get(path).json()
        assert_that(body["items"]).described_as(path).is_empty()
        assert_that(body["total"]).described_as(path).is_zero()
    assert_that(client.get("/api/v2/stats").json()["mentions"]).is_zero()
    source = build_media_summary_source(db=db_session, media=media)
    assert_that(source.source_text).does_not_contain("bulk restored")


def test_scopes_and_subjects_do_not_spill_over(db_session: Session) -> None:
    """Each suppression covers only its own scope and subject."""
    graph = seed_catalog_graph(db_session)
    other = Media(type=MediaType.MOVIE, title="Arrival")
    neighbour = Episode(podcast_id=graph.podcast_id, title="Neighbour")
    db_session.add_all([other, neighbour])
    db_session.commit()
    take_down_mention(db_session, graph.mention_id)
    approve_takedown(
        db_session,
        subject_type=TakedownSubjectType.EPISODE,
        subject_id=neighbour.id,
        actions=["suppress_derivatives", "suppress_raw_transcript"],
    )

    db_session.add_all(
        [
            Mention(episode_id=graph.episode_id, media_id=other.id),
            Mention(episode_id=neighbour.id, media_id=graph.media_id),
        ],
    )
    db_session.commit()
    run = generate_episode_derivatives(
        db=db_session,
        episode=_episode(db_session, graph.episode_id),
        transcript=_transcript(db_session, graph.episode_id),
        chunking_policy=_POLICY,
    )
    db_session.commit()

    assert_that(run.semantic_chunks_created).is_equal_to(1)
    assert_that(db_session.query(Mention).count()).is_equal_to(2)
    assert_that(
        is_episode_suppressed(
            db=db_session,
            episode_id=graph.episode_id,
            scope=ContentSuppressionScope.MENTIONS,
        ),
    ).is_false()
    assert_that(
        suppressed_episode_ids(
            db=db_session,
            scope=ContentSuppressionScope.DERIVATIVES,
            episode_ids=[graph.episode_id, neighbour.id, 999_999],
        ),
    ).is_equal_to({neighbour.id})
    assert_that(
        suppressed_episode_ids(
            db=db_session,
            scope=ContentSuppressionScope.DERIVATIVES,
            episode_ids=[],
        ),
    ).is_empty()


def test_raw_retention_actions_record_no_suppression(db_session: Session) -> None:
    """Raw transcript and opt-out actions keep their semantics and block nothing."""
    graph = seed_catalog_graph(db_session)
    _suppress(db_session, graph, "suppress_raw_transcript")

    run = generate_episode_derivatives(
        db=db_session,
        episode=_episode(db_session, graph.episode_id),
        transcript=_transcript(db_session, graph.episode_id),
        chunking_policy=_POLICY,
    )
    db_session.add(
        Mention(
            episode_id=graph.episode_id,
            media_id=graph.media_id,
            context="second mention",
        ),
    )
    db_session.commit()

    assert_that(run.semantic_chunks_created).is_equal_to(1)
    assert_that(db_session.query(Mention).count()).is_equal_to(2)


def test_unrelated_podcast_is_not_locked_or_refused(db_session: Session) -> None:
    """Content of a podcast without a takedown is written as before."""
    graph = seed_catalog_graph(db_session)
    _suppress(db_session, graph, "unpublish_mentions", "suppress_derivatives")
    podcast = Podcast(name="Other Show", slug="other-show")
    db_session.add(podcast)
    db_session.flush()
    episode = Episode(podcast_id=podcast.id, title="Other")
    db_session.add(episode)
    db_session.flush()
    db_session.add(Mention(episode=episode, media_id=graph.media_id))
    db_session.commit()

    run = generate_episode_derivatives(
        db=db_session,
        episode=episode,
        transcript=_transcript(db_session, episode.id),
        chunking_policy=_POLICY,
    )
    db_session.commit()

    assert_that(run.semantic_chunks_created).is_equal_to(1)
    assert_that(db_session.query(Mention).count()).is_equal_to(1)


_UNGUARDED_EPISODE_REFERENCES = {
    "content_suppressions.episode_id": "the suppression records themselves",
    "mention_candidates.episode_id": "review queue, not public; publication is",
    "transcript_artifacts.episode_id": "raw retention, governed by retention rules",
    "transcript_digests.episode_id": "purge proofs written by takedowns themselves",
}


def test_every_episode_reference_is_guarded_or_exempted() -> None:
    """A new table that stores episode content must choose: guard or exempt."""
    guarded = {model.__table__.name for model in GUARDED_MODELS}
    unclassified = sorted(
        f"{table.name}.{column.name}"
        for table in Base.metadata.sorted_tables
        for column in table.columns
        if any(fk.column.table.name == "episodes" for fk in column.foreign_keys)
        and table.name not in guarded
        and f"{table.name}.{column.name}" not in _UNGUARDED_EPISODE_REFERENCES
    )

    assert_that(unclassified).is_empty()


def test_media_relation_with_suppressed_provenance_is_refused(
    db_session: Session,
) -> None:
    """A media-to-media triple from a suppressed episode writes no relation."""
    graph = seed_catalog_graph(db_session)
    other = Media(type=MediaType.MOVIE, title="Dune (film)")
    db_session.add(other)
    db_session.commit()
    _suppress(db_session, graph, "suppress_derivatives")

    with pytest.raises(ContentSuppressedError):
        upsert_graph_triple(
            db=db_session,
            payload=GraphTripleInputData(
                subject_media_id=other.id,
                predicate="adapted_from",
                source="rebuild",
                object_media_id=graph.media_id,
                provenance_episode_id=graph.episode_id,
            ),
        )
        db_session.commit()
    db_session.rollback()

    assert_that(db_session.query(MediaRelation).count()).is_zero()
    assert_that(db_session.query(GraphTriple).count()).is_zero()

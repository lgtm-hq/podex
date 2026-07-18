"""Tests for the extraction-workflow models."""

from assertpy import assert_that
from sqlalchemy import select
from sqlalchemy.orm import Session

from podex.models import (
    MentionCandidate,
    MentionCandidateProvenance,
    MentionCandidateProvenanceEventType,
    MentionCandidateState,
    ReviewItem,
    ReviewItemStatus,
    ReviewPriority,
    Transcript,
)
from tests.conftest import seed_catalog_graph


def test_transcript_round_trips_and_links_to_episode(
    db_session: Session,
) -> None:
    """A transcript persists its text payloads and reaches its episode."""
    graph = seed_catalog_graph(db_session)
    transcript = Transcript(
        episode_id=graph.episode_id,
        provider="podscripts",
        raw_text="raw words",
        segments_json=[{"start": 0, "end": 5, "text": "raw words"}],
        cleaned_text="clean words",
    )
    db_session.add(transcript)
    db_session.commit()

    stored = db_session.execute(select(Transcript)).scalar_one()
    assert_that(stored.provider).is_equal_to("podscripts")
    assert_that(stored.cleaned_text).is_equal_to("clean words")
    assert_that(stored.episode.id).is_equal_to(graph.episode_id)
    assert_that(stored.episode.transcripts).contains(stored)


def test_mention_candidate_defaults_to_pending_review(
    db_session: Session,
) -> None:
    """New candidates start pending review with zero confidence recorded."""
    graph = seed_catalog_graph(db_session)
    candidate = MentionCandidate(
        episode_id=graph.episode_id,
        media_type="book",
        raw_title="Dune",
        confidence=0.87,
        extraction_source="llm",
    )
    db_session.add(candidate)
    db_session.commit()

    stored = db_session.execute(select(MentionCandidate)).scalar_one()
    assert_that(stored.state).is_equal_to(
        MentionCandidateState.PENDING_REVIEW.value,
    )
    assert_that(stored.confidence).is_equal_to(0.87)
    assert_that(stored.mention_id).is_none()


def test_provenance_event_links_to_candidate(db_session: Session) -> None:
    """Provenance events attach to their candidate with a typed event."""
    graph = seed_catalog_graph(db_session)
    candidate = MentionCandidate(
        episode_id=graph.episode_id,
        media_type="book",
        raw_title="Dune",
    )
    db_session.add(candidate)
    db_session.commit()

    event = MentionCandidateProvenance(
        mention_candidate_id=candidate.id,
        event_type=MentionCandidateProvenanceEventType.CREATED.value,
        raw_title="Dune",
        confidence=0.5,
    )
    db_session.add(event)
    db_session.commit()

    stored = db_session.execute(
        select(MentionCandidateProvenance),
    ).scalar_one()
    assert_that(stored.mention_candidate.id).is_equal_to(candidate.id)
    assert_that(candidate.provenance_events).contains(stored)


def test_review_item_defaults_and_candidate_link(db_session: Session) -> None:
    """Review items default to pending/medium and back-reference candidates."""
    graph = seed_catalog_graph(db_session)
    candidate = MentionCandidate(
        episode_id=graph.episode_id,
        media_type="book",
        raw_title="Dune",
    )
    db_session.add(candidate)
    db_session.commit()

    item = ReviewItem(mention_candidate_id=candidate.id)
    db_session.add(item)
    db_session.commit()

    stored = db_session.execute(select(ReviewItem)).scalar_one()
    assert_that(stored.status).is_equal_to(ReviewItemStatus.PENDING.value)
    assert_that(stored.priority).is_equal_to(ReviewPriority.MEDIUM.value)
    assert_that(stored.mention_candidate.id).is_equal_to(candidate.id)
    assert_that(candidate.review_item).is_equal_to(stored)

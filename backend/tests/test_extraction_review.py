"""Tests for extraction review persistence."""

from assertpy import assert_that
from sqlalchemy.orm import Session

from podex.models import (
    Episode,
    Media,
    MediaAlias,
    Mention,
    MentionCandidate,
    MentionCandidateProvenance,
    MentionCandidateProvenanceEventType,
    Podcast,
    ReviewItem,
    TranscriptionJob,
)
from podex.models.media import MediaType
from podex.services.extraction_review import persist_extracted_candidates
from podex.services.llm_extraction import ExtractedMedia
from podex.services.media_alias_repository import ensure_media_alias
from podex.services.review_queue import (
    ReviewDecisionInputData,
    approve_review_queue_item,
)


def test_persist_extracted_candidates_creates_candidate_and_review_item(
    db_session: Session,
) -> None:
    """Verify extraction output creates a reviewable mention candidate."""
    podcast = Podcast(name="Candidate Podcast", slug="candidate-podcast")
    db_session.add(podcast)
    db_session.flush()

    episode = Episode(podcast_id=podcast.id, title="Candidate Episode")
    db_session.add(episode)
    db_session.flush()

    summary = persist_extracted_candidates(
        db=db_session,
        episode=episode,
        items=[
            ExtractedMedia(
                title="The Pragmatic Programmer",
                media_type=MediaType.BOOK,
                creator="Andrew Hunt",
                year=1999,
                confidence=0.74,
            )
        ],
        segments=None,
        min_confidence=0.5,
        extraction_source="llm_extract",
    )
    db_session.commit()

    candidate = db_session.query(MentionCandidate).one()
    review_item = db_session.query(ReviewItem).one()

    assert_that(summary.candidates_created).is_equal_to(1)
    assert_that(summary.review_items_created).is_equal_to(1)
    assert_that(candidate.raw_title).is_equal_to("The Pragmatic Programmer")
    assert_that(candidate.normalized_title).is_equal_to("the pragmatic programmer")
    assert_that(candidate.suggested_author).is_equal_to("Andrew Hunt")
    assert_that(candidate.media_id).is_none()
    assert_that(candidate.metadata_json).is_equal_to({"year": 1999})
    assert_that(review_item.mention_candidate_id).is_equal_to(candidate.id)
    assert_that(review_item.priority).is_equal_to("medium")
    assert_that(db_session.query(Mention).count()).is_equal_to(0)


def test_approve_review_item_reuses_canonical_media_alias(
    db_session: Session,
) -> None:
    """Verify approval resolves candidates through existing canonical aliases."""
    podcast = Podcast(name="Alias Podcast", slug="alias-podcast")
    db_session.add(podcast)
    db_session.flush()
    episode = Episode(podcast_id=podcast.id, title="Alias Episode")
    media = Media(type=MediaType.MOVIE.value, title="Star Wars")
    db_session.add_all([episode, media])
    db_session.flush()
    ensure_media_alias(
        db=db_session,
        media=media,
        alias="A New Hope",
    )

    candidate = MentionCandidate(
        episode_id=episode.id,
        media_type=MediaType.MOVIE.value,
        raw_title="A New Hope",
        normalized_title="a new hope",
        confidence=0.91,
    )
    db_session.add(candidate)
    db_session.flush()
    review_item = ReviewItem(mention_candidate_id=candidate.id)
    db_session.add(review_item)
    db_session.commit()

    approved = approve_review_queue_item(
        db=db_session,
        review_item_id=review_item.id,
        payload=ReviewDecisionInputData(),
    )
    db_session.commit()

    assert_that(approved).is_not_none()
    assert_that(candidate.media_id).is_equal_to(media.id)
    assert_that(db_session.query(Media).count()).is_equal_to(1)
    assert_that(db_session.query(MediaAlias).count()).is_equal_to(1)
    assert_that(db_session.query(MediaAlias).one().alias).is_equal_to("A New Hope")


def test_persist_extracted_candidates_skips_existing_published_mentions(
    db_session: Session,
) -> None:
    """Verify replay-safe persistence skips already published episode mentions."""
    podcast = Podcast(name="Published Podcast", slug="published-podcast")
    db_session.add(podcast)
    db_session.flush()

    episode = Episode(podcast_id=podcast.id, title="Published Episode")
    media = Media(type=MediaType.BOOK.value, title="Deep Work")
    db_session.add_all([episode, media])
    db_session.flush()

    db_session.add(Mention(episode_id=episode.id, media_id=media.id, confidence=0.9))
    db_session.commit()

    summary = persist_extracted_candidates(
        db=db_session,
        episode=episode,
        items=[
            ExtractedMedia(
                title="Deep Work",
                media_type=MediaType.BOOK,
                creator="Cal Newport",
                confidence=0.88,
            )
        ],
        segments=None,
        min_confidence=0.5,
        extraction_source="llm_extract",
    )
    db_session.commit()

    assert_that(summary.skipped_existing_mentions).is_equal_to(1)
    assert_that(db_session.query(MentionCandidate).count()).is_equal_to(0)
    assert_that(db_session.query(ReviewItem).count()).is_equal_to(0)


def test_persist_extracted_candidates_updates_existing_open_candidate(
    db_session: Session,
) -> None:
    """Verify repeated extraction runs update rather than duplicate open candidates."""
    podcast = Podcast(name="Replay Podcast", slug="replay-podcast")
    db_session.add(podcast)
    db_session.flush()

    episode = Episode(podcast_id=podcast.id, title="Replay Episode")
    db_session.add(episode)
    db_session.flush()

    first_job = TranscriptionJob(episode_id=episode.id, job_type="extract")
    second_job = TranscriptionJob(episode_id=episode.id, job_type="extract")
    db_session.add_all([first_job, second_job])
    db_session.flush()

    first_summary = persist_extracted_candidates(
        db=db_session,
        episode=episode,
        items=[
            ExtractedMedia(
                title="Project Hail Mary",
                media_type=MediaType.BOOK,
                confidence=0.62,
            )
        ],
        segments=None,
        min_confidence=0.5,
        extraction_source="llm_extract",
        source_job_id=first_job.id,
    )
    db_session.flush()

    second_summary = persist_extracted_candidates(
        db=db_session,
        episode=episode,
        items=[
            ExtractedMedia(
                title="Project Hail Mary",
                media_type=MediaType.BOOK,
                creator="Andy Weir",
                year=2021,
                confidence=0.91,
            )
        ],
        segments=None,
        min_confidence=0.5,
        extraction_source="llm_extract",
        source_job_id=second_job.id,
    )
    db_session.commit()

    candidate = db_session.query(MentionCandidate).one()
    review_item = db_session.query(ReviewItem).one()

    assert_that(first_summary.candidates_created).is_equal_to(1)
    assert_that(second_summary.candidates_updated).is_equal_to(1)
    assert_that(db_session.query(MentionCandidate).count()).is_equal_to(1)
    assert_that(db_session.query(ReviewItem).count()).is_equal_to(1)
    assert_that(candidate.suggested_author).is_equal_to("Andy Weir")
    assert_that(candidate.metadata_json).is_equal_to({"year": 2021})
    assert_that(candidate.confidence).is_equal_to(0.91)
    assert_that(candidate.source_job_id).is_equal_to(second_job.id)
    assert_that(review_item.priority).is_equal_to("low")

    provenance_events = (
        db_session.query(MentionCandidateProvenance)
        .filter(MentionCandidateProvenance.mention_candidate_id == candidate.id)
        .order_by(MentionCandidateProvenance.id.asc())
        .all()
    )
    assert_that(provenance_events).is_length(2)
    assert_that(provenance_events[0].event_type).is_equal_to(
        MentionCandidateProvenanceEventType.CREATED.value,
    )
    assert_that(provenance_events[1].event_type).is_equal_to(
        MentionCandidateProvenanceEventType.UPDATED.value,
    )
    assert_that(provenance_events[1].change_summary).contains("suggested_author")
    assert_that(provenance_events[1].change_summary).contains("confidence")
    metadata = provenance_events[1].metadata_json
    assert metadata is not None
    assert_that(metadata["changed_fields"]).contains(
        "source_job_id",
    )


def test_persist_extracted_candidates_enriches_timestamp_and_context(
    db_session: Session,
) -> None:
    """Verify candidate persistence derives timestamp and context from segments."""
    podcast = Podcast(name="Context Podcast", slug="context-podcast")
    db_session.add(podcast)
    db_session.flush()

    episode = Episode(podcast_id=podcast.id, title="Context Episode")
    db_session.add(episode)
    db_session.flush()

    persist_extracted_candidates(
        db=db_session,
        episode=episode,
        items=[
            ExtractedMedia(
                title="Deep Work",
                media_type=MediaType.BOOK,
                creator="Cal Newport",
                confidence=0.82,
            )
        ],
        segments=[
            {"start": 112, "text": "We were just talking about focus and habits."},
            {
                "start": 126,
                "text": "Cal Newport's book Deep Work completely changed how I schedule my week.",
            },
            {"start": 141, "text": "It is one of the best productivity books around."},
        ],
        min_confidence=0.5,
        extraction_source="llm_extract",
    )
    db_session.commit()

    candidate = db_session.query(MentionCandidate).one()

    assert_that(candidate.timestamp_seconds).is_equal_to(126)
    assert_that(candidate.context).contains("Deep Work")
    assert_that(candidate.context).contains("focus and habits")
    assert_that(candidate.context).contains("best productivity books")

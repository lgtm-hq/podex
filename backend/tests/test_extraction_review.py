"""Tests for replay-safe persistence of extraction results."""

from typing import cast

from assertpy import assert_that
from sqlalchemy import select
from sqlalchemy.orm import Session

from podex.models import (
    Episode,
    Media,
    Mention,
    MentionCandidate,
    MentionCandidateProvenance,
    MentionCandidateState,
    Podcast,
    ReviewItem,
    ReviewPriority,
)
from podex.models.media import MediaType
from podex.services.extraction_review import (
    _find_segment_context,
    persist_extracted_candidates,
)
from podex.services.llm_extraction import ExtractedMedia
from tests.conftest import seed_catalog_graph

_SEGMENTS = [
    {"text": "Today we talk about Dune by Frank Herbert.", "start": 30},
    {"text": "Completely unrelated chatter.", "start": 90},
]


def _episode(db_session: Session) -> Episode:
    podcast = Podcast(name="Example Show", slug="example-show-review")
    db_session.add(podcast)
    db_session.commit()
    episode = Episode(podcast_id=podcast.id, title="Pilot")
    db_session.add(episode)
    db_session.commit()
    return episode


def test_persist_creates_candidates_review_items_and_provenance(
    db_session: Session,
) -> None:
    """New extractions create candidate + review item + provenance rows."""
    episode = _episode(db_session)
    items = [
        ExtractedMedia(title="Dune", media_type=MediaType.BOOK, confidence=0.95),
        ExtractedMedia(title="Vague", media_type=MediaType.BOOK, confidence=0.2),
    ]

    result = persist_extracted_candidates(
        db=db_session,
        episode=episode,
        items=items,
        segments=_SEGMENTS,
        min_confidence=0.5,
        extraction_source="llm",
    )
    db_session.commit()

    assert_that(result.candidates_created).is_equal_to(1)
    assert_that(result.review_items_created).is_equal_to(1)
    assert_that(result.skipped_low_confidence).is_equal_to(1)
    candidate = db_session.execute(select(MentionCandidate)).scalar_one()
    assert_that(candidate.state).is_equal_to(
        MentionCandidateState.PENDING_REVIEW.value,
    )
    assert_that(candidate.timestamp_seconds).is_equal_to(30)
    assert_that(candidate.context).contains("Dune")
    review_item = db_session.execute(select(ReviewItem)).scalar_one()
    assert_that(review_item.priority).is_equal_to(ReviewPriority.LOW.value)
    events = (
        db_session.execute(
            select(MentionCandidateProvenance),
        )
        .scalars()
        .all()
    )
    assert_that(events).is_not_empty()


def test_persist_is_replay_safe_and_records_update_provenance(
    db_session: Session,
) -> None:
    """Re-running updates the same candidate instead of duplicating it."""
    episode = _episode(db_session)
    first = ExtractedMedia(
        title="Dune",
        media_type=MediaType.BOOK,
        confidence=0.7,
    )
    richer = ExtractedMedia(
        title="Dune",
        media_type=MediaType.BOOK,
        creator="Frank Herbert",
        year=1965,
        confidence=0.9,
    )

    persist_extracted_candidates(
        db=db_session,
        episode=episode,
        items=[first],
        segments=_SEGMENTS,
        min_confidence=0.5,
        extraction_source="llm",
    )
    db_session.commit()
    result = persist_extracted_candidates(
        db=db_session,
        episode=episode,
        items=[richer],
        segments=_SEGMENTS,
        min_confidence=0.5,
        extraction_source="llm",
    )
    db_session.commit()

    assert_that(result.candidates_created).is_equal_to(0)
    assert_that(result.candidates_updated).is_equal_to(1)
    candidates = db_session.execute(select(MentionCandidate)).scalars().all()
    assert_that(candidates).is_length(1)
    assert_that(candidates[0].suggested_author).is_equal_to("Frank Herbert")
    review_items = db_session.execute(select(ReviewItem)).scalars().all()
    assert_that(review_items).is_length(1)
    events = (
        db_session.execute(
            select(MentionCandidateProvenance),
        )
        .scalars()
        .all()
    )
    assert_that(len(events)).is_greater_than(1)


def test_segment_context_rejects_substring_title_matches() -> None:
    """A short title must not match inside an unrelated longer word."""
    item = ExtractedMedia(title="It", media_type=MediaType.BOOK, confidence=0.9)
    segments = [{"text": "italy is beautiful", "start": 10}]

    match = _find_segment_context(item=item, segments=segments)

    assert_that(match.timestamp_seconds).is_none()
    assert_that(match.context).is_none()


def test_segment_context_matches_standalone_token_title() -> None:
    """A short title matches when it appears as a standalone token."""
    item = ExtractedMedia(title="It", media_type=MediaType.BOOK, confidence=0.9)
    segments = [{"text": "we read it last night", "start": 12}]

    match = _find_segment_context(item=item, segments=segments)

    assert_that(match.timestamp_seconds).is_equal_to(12)
    assert_that(match.context).contains("we read it last night")


def test_segment_context_requires_contiguous_ordered_title_tokens() -> None:
    """Multi-word titles match only contiguous, in-order token runs."""
    item = ExtractedMedia(
        title="War and Peace",
        media_type=MediaType.BOOK,
        confidence=0.9,
    )
    scattered = [{"text": "peace talks and the war effort", "start": 5}]
    contiguous = [{"text": "reading war and peace tonight", "start": 7}]

    scattered_match = _find_segment_context(item=item, segments=scattered)
    contiguous_match = _find_segment_context(item=item, segments=contiguous)

    assert_that(scattered_match.timestamp_seconds).is_none()
    assert_that(contiguous_match.timestamp_seconds).is_equal_to(7)


def test_persist_repeated_match_in_one_batch_creates_single_review_item(
    db_session: Session,
) -> None:
    """Two batch items resolving to one candidate create one review item."""
    episode = _episode(db_session)
    items = [
        ExtractedMedia(title="Dune", media_type=MediaType.BOOK, confidence=0.7),
        ExtractedMedia(
            title="Dune",
            media_type=MediaType.BOOK,
            creator="Frank Herbert",
            confidence=0.9,
        ),
    ]

    result = persist_extracted_candidates(
        db=db_session,
        episode=episode,
        items=items,
        segments=_SEGMENTS,
        min_confidence=0.5,
        extraction_source="llm",
    )
    db_session.commit()

    assert_that(result.candidates_created).is_equal_to(1)
    assert_that(result.review_items_created).is_equal_to(1)
    candidate = db_session.execute(select(MentionCandidate)).scalar_one()
    review_items = db_session.execute(select(ReviewItem)).scalars().all()
    assert_that(review_items).is_length(1)
    assert_that(review_items[0].mention_candidate_id).is_equal_to(candidate.id)


def _duplicate_dune_media(db_session: Session) -> tuple[Media, Media]:
    frank = Media(
        type=MediaType.BOOK,
        title="Dune",
        author="Frank Herbert",
        year=1965,
    )
    brian = Media(type=MediaType.BOOK, title="Dune", author="Brian Herbert")
    db_session.add_all([frank, brian])
    db_session.commit()
    return frank, brian


def test_persist_disambiguates_duplicate_titles_by_creator(
    db_session: Session,
) -> None:
    """A matching creator links the right record among duplicate titles."""
    frank, _ = _duplicate_dune_media(db_session)
    episode = _episode(db_session)

    persist_extracted_candidates(
        db=db_session,
        episode=episode,
        items=[
            ExtractedMedia(
                title="Dune",
                media_type=MediaType.BOOK,
                creator="Frank Herbert",
                confidence=0.9,
            ),
        ],
        segments=None,
        min_confidence=0.5,
        extraction_source="llm",
    )
    db_session.commit()

    candidate = db_session.execute(select(MentionCandidate)).scalar_one()
    assert_that(candidate.media_id).is_equal_to(frank.id)


def test_persist_leaves_ambiguous_duplicate_titles_unlinked(
    db_session: Session,
) -> None:
    """Duplicate titles without a discriminating creator stay unlinked."""
    frank, _ = _duplicate_dune_media(db_session)
    episode = _episode(db_session)
    mention = Mention(episode_id=episode.id, media_id=frank.id, confidence=0.9)
    db_session.add(mention)
    db_session.commit()

    result = persist_extracted_candidates(
        db=db_session,
        episode=episode,
        items=[
            ExtractedMedia(
                title="Dune",
                media_type=MediaType.BOOK,
                confidence=0.9,
            ),
        ],
        segments=None,
        min_confidence=0.5,
        extraction_source="llm",
    )
    db_session.commit()

    assert_that(result.skipped_existing_mentions).is_equal_to(0)
    assert_that(result.candidates_created).is_equal_to(1)
    candidate = db_session.execute(select(MentionCandidate)).scalar_one()
    assert_that(candidate.media_id).is_none()


def test_persist_preserves_known_author_on_sparse_replay(
    db_session: Session,
) -> None:
    """A replay without a creator keeps the previously known author."""
    episode = _episode(db_session)
    rich = ExtractedMedia(
        title="Dune",
        media_type=MediaType.BOOK,
        creator="Frank Herbert",
        confidence=0.9,
    )
    sparse = ExtractedMedia(title="Dune", media_type=MediaType.BOOK, confidence=0.7)

    persist_extracted_candidates(
        db=db_session,
        episode=episode,
        items=[rich],
        segments=None,
        min_confidence=0.5,
        extraction_source="llm",
    )
    db_session.commit()
    persist_extracted_candidates(
        db=db_session,
        episode=episode,
        items=[sparse],
        segments=None,
        min_confidence=0.5,
        extraction_source="llm",
    )
    db_session.commit()

    candidate = db_session.execute(select(MentionCandidate)).scalar_one()
    assert_that(candidate.suggested_author).is_equal_to("Frank Herbert")
    events = db_session.execute(select(MentionCandidateProvenance)).scalars().all()
    for event in events:
        changed_fields = (event.metadata_json or {}).get("changed_fields", [])
        assert_that(changed_fields).does_not_contain("suggested_author")


def test_persist_updates_author_on_replay_with_new_creator(
    db_session: Session,
) -> None:
    """A replay with a new non-empty creator still updates the author."""
    episode = _episode(db_session)
    first = ExtractedMedia(title="Dune", media_type=MediaType.BOOK, confidence=0.9)
    corrected = ExtractedMedia(
        title="Dune",
        media_type=MediaType.BOOK,
        creator="Frank Herbert",
        confidence=0.9,
    )

    persist_extracted_candidates(
        db=db_session,
        episode=episode,
        items=[first],
        segments=None,
        min_confidence=0.5,
        extraction_source="llm",
    )
    db_session.commit()
    persist_extracted_candidates(
        db=db_session,
        episode=episode,
        items=[corrected],
        segments=None,
        min_confidence=0.5,
        extraction_source="llm",
    )
    db_session.commit()

    candidate = db_session.execute(select(MentionCandidate)).scalar_one()
    assert_that(candidate.suggested_author).is_equal_to("Frank Herbert")


def test_persist_skips_items_with_existing_published_mentions(
    db_session: Session,
) -> None:
    """Items already published as mentions are skipped, not re-queued."""
    graph = seed_catalog_graph(db_session)
    episode = db_session.get(Episode, graph.episode_id)
    assert_that(episode).is_not_none()
    episode = cast("Episode", episode)

    result = persist_extracted_candidates(
        db=db_session,
        episode=episode,
        items=[
            ExtractedMedia(
                title="Dune",
                media_type=MediaType.BOOK,
                confidence=0.9,
            ),
        ],
        segments=None,
        min_confidence=0.5,
        extraction_source="llm",
    )
    db_session.commit()

    assert_that(result.skipped_existing_mentions).is_equal_to(1)
    assert_that(result.candidates_created).is_equal_to(0)


def test_persist_caps_candidates_per_episode(db_session: Session) -> None:
    """A hostile flood of items stops at the per-episode candidate cap."""
    from podex.models.media import MediaType as _MediaType

    episode = _episode(db_session)
    flood = [
        ExtractedMedia(
            title=f"Injected title {index}",
            media_type=_MediaType.BOOK,
            confidence=0.9,
        )
        for index in range(5)
    ]

    result = persist_extracted_candidates(
        db=db_session,
        episode=episode,
        items=flood,
        segments=None,
        min_confidence=0.5,
        extraction_source="llm",
        max_candidates_per_episode=3,
    )
    db_session.commit()

    assert_that(result.candidates_created).is_equal_to(3)
    assert_that(result.skipped_over_limit).is_equal_to(2)

"""Tests for the heuristic mention-extraction service."""

from assertpy import assert_that
from sqlalchemy.orm import Session

from podex.models import Media, MediaType
from podex.services.extraction import (
    extract_mentions_from_segments,
    materialize_mentions,
    normalize_text,
)
from tests.conftest import seed_catalog_graph


def _media(media_id: int, title: str) -> Media:
    media = Media(type=MediaType.BOOK, title=title)
    media.id = media_id
    return media


def test_normalize_text_strips_punctuation_and_case() -> None:
    """Normalization lowercases and removes punctuation."""
    assert_that(normalize_text("Dune: Part Two!")).is_equal_to("dune part two")


def test_extract_matches_known_media_and_dedups() -> None:
    """Known titles match per segment with timestamps, deduplicated."""
    segments = [
        {"text": "We discussed Dune at length.", "start": 12.5},
        {"text": "Dune again, same segment topic.", "start": 12.5},
        {"text": "Dune reprise later on.", "start": 40},
        {"text": "Nothing relevant here.", "start": 55},
    ]

    mentions = extract_mentions_from_segments(segments, [_media(7, "Dune")])

    assert_that(mentions).is_length(2)
    assert_that(mentions[0].media_id).is_equal_to(7)
    assert_that(mentions[0].timestamp_seconds).is_equal_to(12)
    assert_that(mentions[0].confidence).is_equal_to(0.9)


def test_extract_heuristic_flags_title_cased_sequences() -> None:
    """With the heuristic enabled, unknown title-cased runs are flagged."""
    segments = [{"text": "Have you seen Blade Runner recently?", "start": 3}]

    mentions = extract_mentions_from_segments(
        segments,
        [],
        enable_heuristic=True,
    )

    assert_that(mentions).is_length(1)
    assert_that(mentions[0].media_id).is_equal_to(-1)
    assert_that(mentions[0].confidence).is_equal_to(0.4)


def test_materialize_mentions_builds_mention_models(db_session: Session) -> None:
    """Extracted matches materialize as Mention rows for the episode."""
    graph = seed_catalog_graph(db_session)
    media = db_session.get(Media, graph.media_id)
    assert_that(media).is_not_none()

    mentions = materialize_mentions(
        episode_id=graph.episode_id,
        segments=[{"text": "Talking about Dune today.", "start": 5}],
        media_items=[media] if media else [],
        create_media_fn=lambda title: _media(999, title),
    )

    assert_that(mentions).is_length(1)
    assert_that(mentions[0].episode_id).is_equal_to(graph.episode_id)
    assert_that(mentions[0].media_id).is_equal_to(graph.media_id)
    assert_that(mentions[0].context).contains("Dune")

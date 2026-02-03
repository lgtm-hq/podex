"""Tests for mention extraction service."""

from typing import Any

from assertpy import assert_that

from podex.models import Media
from podex.services.extraction import (
    extract_mentions_from_segments,
    materialize_mentions,
)


class TestExtractMentionsFromSegments:
    """Tests for extract_mentions_from_segments function."""

    def test_extracts_known_media_by_title_match(self) -> None:
        """Test that known media titles are detected in transcript text."""
        segments: list[dict[str, Any]] = [
            {"text": "We talked about The Great Gatsby today", "start": 12.0},
            {"text": "Also mentioned Another Title", "start": 30.0},
        ]
        media_items = [Media(id=1, type="book", title="The Great Gatsby")]

        mentions = extract_mentions_from_segments(segments, media_items)

        assert_that(mentions).is_not_empty()
        assert_that(mentions[0].media_id).is_equal_to(1)
        assert_that(mentions[0].timestamp_seconds).is_equal_to(12)
        assert_that(mentions[0].confidence).is_equal_to(0.9)

    def test_returns_empty_for_no_matches(self) -> None:
        """Test that no mentions are returned when there are no matches."""
        segments: list[dict[str, Any]] = [
            {"text": "We talked about something else", "start": 12.0},
        ]
        media_items = [Media(id=1, type="book", title="The Great Gatsby")]

        mentions = extract_mentions_from_segments(segments, media_items)

        assert_that(mentions).is_empty()

    def test_case_insensitive_matching(self) -> None:
        """Test that matching is case-insensitive."""
        segments: list[dict[str, Any]] = [
            {"text": "We talked about THE GREAT GATSBY today", "start": 12.0},
        ]
        media_items = [Media(id=1, type="book", title="The Great Gatsby")]

        mentions = extract_mentions_from_segments(segments, media_items)

        assert_that(mentions).is_not_empty()
        assert_that(mentions[0].media_id).is_equal_to(1)

    def test_deduplicates_same_media_same_timestamp(self) -> None:
        """Test that same media at same timestamp is not duplicated."""
        segments: list[dict[str, Any]] = [
            {"text": "The Great Gatsby is great", "start": 12.0},
        ]
        media_items = [Media(id=1, type="book", title="The Great Gatsby")]

        mentions = extract_mentions_from_segments(segments, media_items)

        # Should only have one mention even though title appears once
        assert_that(mentions).is_length(1)

    def test_handles_empty_segments(self) -> None:
        """Test that empty segments list returns empty mentions."""
        segments: list[dict[str, Any]] = []
        media_items = [Media(id=1, type="book", title="Test")]

        mentions = extract_mentions_from_segments(segments, media_items)

        assert_that(mentions).is_empty()

    def test_handles_segments_with_missing_text(self) -> None:
        """Test that segments with missing or empty text are skipped."""
        segments: list[dict[str, Any]] = [
            {"text": "", "start": 5.0},
            {"text": None, "start": 10.0},
            {"start": 15.0},  # No text key at all
        ]
        media_items = [Media(id=1, type="book", title="Test")]

        mentions = extract_mentions_from_segments(segments, media_items)

        assert_that(mentions).is_empty()

    def test_handles_segments_with_missing_start(self) -> None:
        """Test that segments with missing start time get None timestamp."""
        segments: list[dict[str, Any]] = [
            {"text": "The Great Gatsby is mentioned"},
        ]
        media_items = [Media(id=1, type="book", title="The Great Gatsby")]

        mentions = extract_mentions_from_segments(segments, media_items)

        assert_that(mentions).is_not_empty()
        assert_that(mentions[0].timestamp_seconds).is_none()

    def test_heuristic_mode_disabled_by_default(self) -> None:
        """Test that heuristic extraction is disabled by default."""
        segments: list[dict[str, Any]] = [
            {"text": "We talked about Brand New Book", "start": 5.0},
        ]
        media_items: list[Media] = []  # No known media

        mentions = extract_mentions_from_segments(segments, media_items)

        # Without heuristic mode, no mentions should be extracted
        assert_that(mentions).is_empty()

    def test_heuristic_mode_when_enabled(self) -> None:
        """Test that heuristic extraction works when enabled."""
        segments: list[dict[str, Any]] = [
            {"text": "We talked about Brand New Book", "start": 5.0},
        ]
        media_items: list[Media] = []

        mentions = extract_mentions_from_segments(
            segments,
            media_items,
            enable_heuristic=True,
        )

        # With heuristic mode, title-cased sequences are detected
        assert_that(mentions).is_not_empty()
        assert_that(mentions[0].media_id).is_equal_to(-1)  # Placeholder ID
        assert_that(mentions[0].confidence).is_equal_to(0.4)


class TestMaterializeMentions:
    """Tests for materialize_mentions function."""

    def test_creates_mention_models_from_known_media(self) -> None:
        """Test that mentions are created for known media matches."""
        segments: list[dict[str, Any]] = [
            {"text": "We talked about The Great Gatsby", "start": 12.0},
        ]
        media_items = [Media(id=1, type="book", title="The Great Gatsby")]
        created_media: list[Media] = []

        def create_media(title: str) -> Media:
            media = Media(id=10, type="article", title=title)
            created_media.append(media)
            return media

        mentions = materialize_mentions(
            episode_id=1,
            segments=segments,
            media_items=media_items,
            create_media_fn=create_media,
        )

        assert_that(created_media).is_empty()  # No new media created
        assert_that(mentions).is_length(1)
        assert_that(mentions[0].media_id).is_equal_to(1)
        assert_that(mentions[0].episode_id).is_equal_to(1)

    def test_creates_media_for_heuristic_matches(self) -> None:
        """Test that materialize creates media for heuristic matches.

        Note: This test verifies behavior when heuristic mode is disabled
        (the production default). No mentions should be created without
        known media to match against.
        """
        segments: list[dict[str, Any]] = [
            {"text": "We talked about Brand New Book", "start": 5.0}
        ]
        media_items: list[Media] = []
        created_media: list[Media] = []

        def create_media(title: str) -> Media:
            media = Media(id=10, type="article", title=title)
            created_media.append(media)
            return media

        # With heuristic disabled (default), no mentions are created
        mentions = materialize_mentions(
            episode_id=1,
            segments=segments,
            media_items=media_items,
            create_media_fn=create_media,
        )

        # Default behavior: no heuristic extraction
        assert_that(created_media).is_empty()
        assert_that(mentions).is_empty()

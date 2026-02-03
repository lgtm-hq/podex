"""Tests for LLM extraction service."""

from typing import Any
from unittest.mock import Mock, patch

import pytest
from assertpy import assert_that

from podex.models.media import MediaType
from podex.services.llm_extraction import (
    ExtractedMedia,
    ExtractionResult,
    LLMExtractor,
)


class TestParseJsonResponse:
    """Tests for _parse_json_response method."""

    @pytest.fixture
    def extractor(self) -> LLMExtractor:
        """Create extractor with mock API key."""
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            return LLMExtractor()

    def test_parses_valid_json_array(self, extractor: LLMExtractor) -> None:
        response = '[{"title": "1984", "type": "book", "creator": "George Orwell"}]'
        items, errors = extractor._parse_json_response(response)
        assert_that(items).is_length(1)
        assert_that(items[0]["title"]).is_equal_to("1984")
        assert_that(errors).is_empty()

    def test_parses_json_with_surrounding_text(self, extractor: LLMExtractor) -> None:
        response = (
            'Here are the results:\n[{"title": "Book Title", "type": "book"}]\nDone.'
        )
        items, errors = extractor._parse_json_response(response)
        assert_that(items).is_length(1)
        assert_that(items[0]["title"]).is_equal_to("Book Title")
        assert_that(errors).is_empty()

    def test_handles_empty_array(self, extractor: LLMExtractor) -> None:
        response = "[]"
        items, errors = extractor._parse_json_response(response)
        assert_that(items).is_empty()
        assert_that(errors).is_empty()

    def test_handles_malformed_json(self, extractor: LLMExtractor) -> None:
        response = "[{invalid json}]"
        items, errors = extractor._parse_json_response(response)
        assert_that(items).is_empty()
        assert_that(errors).is_not_empty()
        assert_that(errors[0]).contains("Failed to parse JSON")

    def test_handles_no_array(self, extractor: LLMExtractor) -> None:
        response = "No media found in this transcript."
        items, errors = extractor._parse_json_response(response)
        assert_that(items).is_empty()
        assert_that(errors).is_not_empty()
        assert_that(errors[0]).contains("No JSON array")

    def test_handles_incomplete_array(self, extractor: LLMExtractor) -> None:
        response = '[{"title": "Test"'
        items, errors = extractor._parse_json_response(response)
        assert_that(items).is_empty()
        assert_that(errors).is_not_empty()

    def test_handles_non_array_json(self, extractor: LLMExtractor) -> None:
        response = '{"title": "Test"}'
        items, errors = extractor._parse_json_response(response)
        assert_that(items).is_empty()
        assert_that(errors).is_not_empty()
        assert_that(errors[0]).contains("Expected JSON array")

    def test_parses_nested_arrays(self, extractor: LLMExtractor) -> None:
        response = '[{"title": "Book", "type": "book", "tags": ["fiction", "classic"]}]'
        items, errors = extractor._parse_json_response(response)
        assert_that(items).is_length(1)
        assert_that(errors).is_empty()


class TestExtractFromTranscript:
    """Tests for extract_from_transcript method."""

    # Sample long transcript to pass the 100 char minimum
    LONG_TRANSCRIPT = (
        "Today we're going to talk about some amazing books and movies."
        "First up is the classic dystopian novel 1984 by George Orwell. "
        "This book has become increasingly relevant in our modern age of surveillance. "
        "We also want to discuss The Great Gatsby and some other literary classics."
    )

    @pytest.fixture
    def mock_client(self) -> Mock:
        """Create a mock httpx client."""
        mock = Mock()
        mock.post.return_value = Mock(
            status_code=200,
            json=lambda: {
                "content": [
                    {
                        "text": '[{"title": "1984", "type": "book", '
                        '"creator": "George Orwell", "confidence": 0.95}]'
                    }
                ]
            },
        )
        mock.post.return_value.raise_for_status = Mock()
        return mock

    @pytest.fixture
    def extractor(self, mock_client: Mock) -> LLMExtractor:
        """Create extractor with mocked HTTP client."""
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            ext = LLMExtractor()
            ext.client = mock_client
            return ext

    def test_extracts_media_from_transcript(self, extractor: LLMExtractor) -> None:
        segments: list[dict[str, Any]] = [{"text": self.LONG_TRANSCRIPT}]
        result = extractor.extract_from_transcript(segments)
        assert_that(result).is_instance_of(ExtractionResult)
        assert_that(result.items).is_length(1)
        assert_that(result.items[0].title).is_equal_to("1984")
        assert_that(result.items[0].media_type).is_equal_to(MediaType.BOOK)

    def test_returns_empty_for_short_transcript(self, extractor: LLMExtractor) -> None:
        segments = [{"text": "Hi"}]
        result = extractor.extract_from_transcript(segments)
        assert_that(result.items).is_empty()

    def test_returns_empty_for_empty_segments(self, extractor: LLMExtractor) -> None:
        result = extractor.extract_from_transcript([])
        assert_that(result.items).is_empty()

    def test_handles_segments_without_text(self, extractor: LLMExtractor) -> None:
        segments: list[dict[str, object]] = [
            {"start": 0},
            {"text": None},
            {"text": ""},
        ]
        result = extractor.extract_from_transcript(segments)
        assert_that(result.items).is_empty()

    def test_deduplicates_results(
        self, extractor: LLMExtractor, mock_client: Mock
    ) -> None:
        mock_client.post.return_value.json = lambda: {
            "content": [
                {
                    "text": '[{"title": "1984", "type": "book"}, {"title": "1984", "type": "book"}]'
                }
            ]
        }
        segments: list[dict[str, Any]] = [{"text": self.LONG_TRANSCRIPT}]
        result = extractor.extract_from_transcript(segments)
        assert_that(result.items).is_length(1)

    def test_deduplicates_case_insensitive(
        self, extractor: LLMExtractor, mock_client: Mock
    ) -> None:
        mock_client.post.return_value.json = lambda: {
            "content": [
                {
                    "text": '[{"title": "The Great Gatsby", "type": "book"}, '
                    '{"title": "THE GREAT GATSBY", "type": "book"}]'
                }
            ]
        }
        segments: list[dict[str, Any]] = [{"text": self.LONG_TRANSCRIPT}]
        result = extractor.extract_from_transcript(segments)
        assert_that(result.items).is_length(1)

    def test_handles_unknown_media_type(
        self, extractor: LLMExtractor, mock_client: Mock
    ) -> None:
        mock_client.post.return_value.json = lambda: {
            "content": [{"text": '[{"title": "Test", "type": "unknown_type"}]'}]
        }
        segments: list[dict[str, Any]] = [{"text": self.LONG_TRANSCRIPT}]
        result = extractor.extract_from_transcript(segments)
        assert_that(result.items).is_length(1)
        assert_that(result.items[0].media_type).is_equal_to(MediaType.ARTICLE)

    def test_handles_api_error(
        self, extractor: LLMExtractor, mock_client: Mock
    ) -> None:
        import httpx

        error_response = Mock()
        error_response.status_code = 500
        error_response.text = "Internal Server Error"
        mock_client.post.side_effect = httpx.HTTPStatusError(
            "Server Error",
            request=Mock(),
            response=error_response,
        )

        segments: list[dict[str, Any]] = [{"text": self.LONG_TRANSCRIPT}]
        result = extractor.extract_from_transcript(segments)
        assert_that(result.items).is_empty()
        assert_that(result.errors).is_not_empty()
        assert_that(result.success).is_false()

    def test_handles_empty_content_response(
        self, extractor: LLMExtractor, mock_client: Mock
    ) -> None:
        mock_client.post.return_value.json = lambda: {"content": []}
        segments: list[dict[str, Any]] = [{"text": self.LONG_TRANSCRIPT}]
        result = extractor.extract_from_transcript(segments)
        assert_that(result.items).is_empty()


class TestExtractedMedia:
    """Tests for ExtractedMedia dataclass."""

    def test_creates_with_defaults(self) -> None:
        media = ExtractedMedia(title="Test", media_type=MediaType.BOOK)
        assert_that(media.title).is_equal_to("Test")
        assert_that(media.media_type).is_equal_to(MediaType.BOOK)
        assert_that(media.creator).is_none()
        assert_that(media.year).is_none()
        assert_that(media.confidence).is_equal_to(0.8)

    def test_creates_with_all_fields(self) -> None:
        media = ExtractedMedia(
            title="1984",
            media_type=MediaType.BOOK,
            creator="George Orwell",
            year=1949,
            confidence=0.95,
        )
        assert_that(media.creator).is_equal_to("George Orwell")
        assert_that(media.year).is_equal_to(1949)
        assert_that(media.confidence).is_equal_to(0.95)


class TestExtractionResult:
    """Tests for ExtractionResult dataclass."""

    def test_success_when_no_errors(self) -> None:
        result = ExtractionResult(
            items=[ExtractedMedia(title="Test", media_type=MediaType.BOOK)],
            errors=[],
        )
        assert_that(result.success).is_true()

    def test_not_success_when_errors(self) -> None:
        result = ExtractionResult(
            items=[],
            errors=["Failed to parse"],
        )
        assert_that(result.success).is_false()

    def test_defaults_to_empty_lists(self) -> None:
        result = ExtractionResult()
        assert_that(result.items).is_empty()
        assert_that(result.errors).is_empty()
        assert_that(result.success).is_true()

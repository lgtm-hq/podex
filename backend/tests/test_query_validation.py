"""Tests for query param validation."""

from collections.abc import Generator

import pytest
from assertpy import assert_that
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from podex.models import Podcast


class TestPaginationValidation:
    """Tests for pagination parameter validation."""

    @pytest.mark.parametrize(
        "endpoint,params,expected_status",
        [
            # Invalid page values
            ("/api/v1/episodes", "page=0&per_page=10", 422),
            ("/api/v1/episodes", "page=-1&per_page=10", 422),
            # Invalid per_page values
            ("/api/v1/episodes", "page=1&per_page=0", 422),
            ("/api/v1/episodes", "page=1&per_page=-1", 422),
            ("/api/v1/episodes", "page=1&per_page=101", 422),
            # Valid values
            ("/api/v1/episodes", "page=1&per_page=20", 200),
            ("/api/v1/episodes", "page=1&per_page=100", 200),
            # Media endpoint
            ("/api/v1/media", "page=0&per_page=10", 422),
            ("/api/v1/media", "page=1&per_page=0", 422),
            ("/api/v1/media", "page=1&per_page=20", 200),
        ],
    )
    def test_pagination_validation(
        self, client: TestClient, endpoint: str, params: str, expected_status: int
    ) -> None:
        response = client.get(f"{endpoint}?{params}")
        assert_that(response.status_code).is_equal_to(expected_status)


class TestPodcastEpisodesPagination:
    """Tests for podcast episodes pagination."""

    @pytest.fixture
    def podcast(self, db_session: Session) -> Generator[Podcast, None, None]:
        podcast = Podcast(name="Test Podcast", slug="test")
        db_session.add(podcast)
        db_session.commit()
        yield podcast

    @pytest.mark.parametrize(
        "params,expected_status",
        [
            ("page=0&per_page=10", 422),
            ("page=1&per_page=0", 422),
            ("page=1&per_page=20", 200),
        ],
    )
    def test_podcast_episodes_pagination(
        self, client: TestClient, podcast: Podcast, params: str, expected_status: int
    ) -> None:
        response = client.get(f"/api/v1/podcasts/test/episodes?{params}")
        assert_that(response.status_code).is_equal_to(expected_status)


class TestMediaSortAndOrderValidation:
    """Tests for media sort and order validation."""

    @pytest.mark.parametrize(
        "params,expected_status",
        [
            # Invalid sort values
            ("sort=bad", 422),
            ("sort=invalid_field", 422),
            # Invalid order values
            ("order=up", 422),
            ("order=down", 422),
            # Valid sort values
            ("sort=mention_count", 200),
            ("sort=title", 200),
            ("sort=created_at", 200),
            # Valid order values
            ("order=asc", 200),
            ("order=desc", 200),
            # Valid combinations
            ("sort=title&order=asc", 200),
            ("sort=mention_count&order=desc", 200),
        ],
    )
    def test_sort_and_order_validation(
        self, client: TestClient, params: str, expected_status: int
    ) -> None:
        response = client.get(f"/api/v1/media?{params}")
        assert_that(response.status_code).is_equal_to(expected_status)


class TestMediaTypeValidation:
    """Tests for media type validation."""

    @pytest.mark.parametrize(
        "type_value,expected_status",
        [
            # Invalid types
            ("unknown", 422),
            ("invalid", 422),
            ("film", 422),  # Should be "movie"
            # Valid types
            ("book", 200),
            ("movie", 200),
            ("documentary", 200),
            ("tv_show", 200),
            ("podcast", 200),
            ("article", 200),
            ("study", 200),
            ("standup_special", 200),
        ],
    )
    def test_media_type_validation(
        self, client: TestClient, type_value: str, expected_status: int
    ) -> None:
        response = client.get(f"/api/v1/media?type={type_value}")
        assert_that(response.status_code).is_equal_to(expected_status)

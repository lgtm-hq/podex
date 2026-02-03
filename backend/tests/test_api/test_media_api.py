"""Tests for media API endpoints."""

from typing import Any

import pytest
from assertpy import assert_that
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from podex.models import Episode, Media, Mention, Podcast


@pytest.fixture
def sample_data(db_session: Session) -> dict[str, Any]:
    """Create sample data for media tests."""
    podcast = Podcast(name="Test Podcast", slug="test-podcast")
    db_session.add(podcast)
    db_session.flush()

    episode = Episode(
        podcast_id=podcast.id,
        title="Episode 1",
        episode_number=1,
        youtube_id="abc123",
    )
    db_session.add(episode)
    db_session.flush()

    media1 = Media(
        type="book",
        title="The Great Gatsby",
        author="F. Scott Fitzgerald",
        year=1925,
    )
    media2 = Media(
        type="movie",
        title="Inception",
        author="Christopher Nolan",
        year=2010,
    )
    media3 = Media(
        type="book",
        title="100% Pure_Test",
        author="Test Author",
    )
    db_session.add_all([media1, media2, media3])
    db_session.flush()

    mention1 = Mention(
        episode_id=episode.id,
        media_id=media1.id,
        timestamp_seconds=60,
        context="They talked about The Great Gatsby",
        confidence=0.9,
    )
    mention2 = Mention(
        episode_id=episode.id,
        media_id=media1.id,
        timestamp_seconds=120,
        context="More about The Great Gatsby",
        confidence=0.85,
    )
    mention3 = Mention(
        episode_id=episode.id,
        media_id=media2.id,
        timestamp_seconds=180,
        context="Talking about Inception",
        confidence=0.95,
    )
    db_session.add_all([mention1, mention2, mention3])
    db_session.commit()

    return {
        "podcast": podcast,
        "episode": episode,
        "media": [media1, media2, media3],
        "mentions": [mention1, mention2, mention3],
    }


class TestListMedia:
    """Tests for GET /api/v1/media endpoint."""

    def test_list_media_empty(self, client: TestClient) -> None:
        response = client.get("/api/v1/media")
        assert_that(response.status_code).is_equal_to(200)
        data = response.json()
        assert_that(data["items"]).is_empty()
        assert_that(data["total"]).is_equal_to(0)

    def test_list_media_with_data(
        self, client: TestClient, sample_data: dict[str, Any]
    ) -> None:
        response = client.get("/api/v1/media")
        assert_that(response.status_code).is_equal_to(200)
        data = response.json()
        assert_that(data["items"]).is_length(3)
        assert_that(data["total"]).is_equal_to(3)

    def test_list_media_pagination(
        self, client: TestClient, sample_data: dict[str, Any]
    ) -> None:
        response = client.get("/api/v1/media?page=1&per_page=2")
        assert_that(response.status_code).is_equal_to(200)
        data = response.json()
        assert_that(data["items"]).is_length(2)
        assert_that(data["total"]).is_equal_to(3)
        assert_that(data["page"]).is_equal_to(1)
        assert_that(data["per_page"]).is_equal_to(2)

    def test_list_media_filter_by_type(
        self, client: TestClient, sample_data: dict[str, Any]
    ) -> None:
        response = client.get("/api/v1/media?type=book")
        assert_that(response.status_code).is_equal_to(200)
        data = response.json()
        assert_that(data["items"]).is_length(2)
        for item in data["items"]:
            assert_that(item["type"]).is_equal_to("book")

    def test_list_media_sort_by_title(
        self, client: TestClient, sample_data: dict[str, Any]
    ) -> None:
        response = client.get("/api/v1/media?sort=title&order=asc")
        assert_that(response.status_code).is_equal_to(200)
        data = response.json()
        titles = [item["title"] for item in data["items"]]
        assert_that(titles).is_equal_to(sorted(titles))

    def test_list_media_sort_by_mention_count(
        self, client: TestClient, sample_data: dict[str, Any]
    ) -> None:
        response = client.get("/api/v1/media?sort=mention_count&order=desc")
        assert_that(response.status_code).is_equal_to(200)
        data = response.json()
        # The Great Gatsby has 2 mentions, Inception has 1
        assert_that(data["items"][0]["title"]).is_equal_to("The Great Gatsby")
        assert_that(data["items"][0]["mention_count"]).is_equal_to(2)

    def test_list_media_invalid_page(self, client: TestClient) -> None:
        response = client.get("/api/v1/media?page=0")
        assert_that(response.status_code).is_equal_to(422)


class TestSearchMedia:
    """Tests for GET /api/v1/media/search endpoint."""

    def test_search_by_title(
        self, client: TestClient, sample_data: dict[str, Any]
    ) -> None:
        response = client.get("/api/v1/media/search?q=gatsby")
        assert_that(response.status_code).is_equal_to(200)
        data = response.json()
        assert_that(data["items"]).is_length(1)
        assert_that(data["items"][0]["title"]).is_equal_to("The Great Gatsby")

    def test_search_by_author(
        self, client: TestClient, sample_data: dict[str, Any]
    ) -> None:
        response = client.get("/api/v1/media/search?q=fitzgerald")
        assert_that(response.status_code).is_equal_to(200)
        data = response.json()
        assert_that(data["items"]).is_length(1)
        assert_that(data["items"][0]["author"]).is_equal_to("F. Scott Fitzgerald")

    def test_search_no_results(
        self, client: TestClient, sample_data: dict[str, Any]
    ) -> None:
        response = client.get("/api/v1/media/search?q=nonexistent")
        assert_that(response.status_code).is_equal_to(200)
        data = response.json()
        assert_that(data["items"]).is_empty()

    def test_search_with_type_filter(
        self, client: TestClient, sample_data: dict[str, Any]
    ) -> None:
        response = client.get("/api/v1/media/search?q=great&type=movie")
        assert_that(response.status_code).is_equal_to(200)
        data = response.json()
        assert_that(data["items"]).is_empty()

    def test_search_sql_injection_prevention_percent(
        self, client: TestClient, sample_data: dict[str, Any]
    ) -> None:
        """Test that % wildcard in search is escaped properly."""
        response = client.get("/api/v1/media/search?q=%25")
        assert_that(response.status_code).is_equal_to(200)
        data = response.json()
        # Should only match media with literal % in title/author
        # "100% Pure_Test" contains a literal %
        assert_that(data["items"]).is_length(1)
        assert_that(data["items"][0]["title"]).is_equal_to("100% Pure_Test")

    def test_search_sql_injection_prevention_underscore(
        self, client: TestClient, sample_data: dict[str, Any]
    ) -> None:
        """Test that _ wildcard in search is escaped properly."""
        response = client.get("/api/v1/media/search?q=_")
        assert_that(response.status_code).is_equal_to(200)
        data = response.json()
        # Should only match media with literal _ in title/author
        assert_that(data["items"]).is_length(1)
        assert_that(data["items"][0]["title"]).is_equal_to("100% Pure_Test")

    def test_search_case_insensitive(
        self, client: TestClient, sample_data: dict[str, Any]
    ) -> None:
        response = client.get("/api/v1/media/search?q=GATSBY")
        assert_that(response.status_code).is_equal_to(200)
        data = response.json()
        assert_that(data["items"]).is_length(1)


class TestTopMedia:
    """Tests for GET /api/v1/media/top endpoint."""

    def test_get_top_media(
        self, client: TestClient, sample_data: dict[str, Any]
    ) -> None:
        response = client.get("/api/v1/media/top")
        assert_that(response.status_code).is_equal_to(200)
        data = response.json()
        # Only media with mentions are returned (2 out of 3)
        assert_that(data).is_length(2)
        # The Great Gatsby should be first (2 mentions)
        assert_that(data[0]["title"]).is_equal_to("The Great Gatsby")
        assert_that(data[0]["mention_count"]).is_equal_to(2)

    def test_get_top_media_with_limit(
        self, client: TestClient, sample_data: dict[str, Any]
    ) -> None:
        response = client.get("/api/v1/media/top?limit=1")
        assert_that(response.status_code).is_equal_to(200)
        data = response.json()
        assert_that(data).is_length(1)

    def test_get_top_media_filter_by_type(
        self, client: TestClient, sample_data: dict[str, Any]
    ) -> None:
        response = client.get("/api/v1/media/top?type=movie")
        assert_that(response.status_code).is_equal_to(200)
        data = response.json()
        assert_that(data).is_length(1)
        assert_that(data[0]["title"]).is_equal_to("Inception")


class TestMediaDetail:
    """Tests for GET /api/v1/media/{media_id} endpoint."""

    def test_get_media_detail(
        self, client: TestClient, sample_data: dict[str, Any]
    ) -> None:
        media_id = sample_data["media"][0].id
        response = client.get(f"/api/v1/media/{media_id}")
        assert_that(response.status_code).is_equal_to(200)
        data = response.json()
        assert_that(data["id"]).is_equal_to(media_id)
        assert_that(data["title"]).is_equal_to("The Great Gatsby")
        assert_that(data["mentions"]).is_length(2)

    def test_get_media_detail_not_found(self, client: TestClient) -> None:
        response = client.get("/api/v1/media/99999")
        assert_that(response.status_code).is_equal_to(404)
        assert_that(response.json()["detail"]).is_equal_to("Media not found")

    def test_get_media_detail_mentions_have_youtube_url(
        self, client: TestClient, sample_data: dict[str, Any]
    ) -> None:
        media_id = sample_data["media"][0].id
        response = client.get(f"/api/v1/media/{media_id}")
        assert_that(response.status_code).is_equal_to(200)
        data = response.json()
        for mention in data["mentions"]:
            assert_that(mention["youtube_timestamp_url"]).contains("youtube.com")
            assert_that(mention["youtube_timestamp_url"]).contains("abc123")

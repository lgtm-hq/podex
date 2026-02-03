"""Tests for stats API endpoints."""

from typing import Any

import pytest
from assertpy import assert_that
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from podex.models import Episode, Media, Mention, Podcast


@pytest.fixture
def sample_data(db_session: Session) -> dict[str, Any]:
    """Create sample data for stats tests."""
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

    book1 = Media(type="book", title="Book One", author="Author A")
    book2 = Media(type="book", title="Book Two", author="Author B")
    movie = Media(type="movie", title="Movie One", author="Director A")
    documentary = Media(type="documentary", title="Documentary One")
    db_session.add_all([book1, book2, movie, documentary])
    db_session.flush()

    mention1 = Mention(
        episode_id=episode.id,
        media_id=book1.id,
        timestamp_seconds=60,
        confidence=0.9,
    )
    mention2 = Mention(
        episode_id=episode.id,
        media_id=book1.id,
        timestamp_seconds=120,
        confidence=0.9,
    )
    mention3 = Mention(
        episode_id=episode.id,
        media_id=book1.id,
        timestamp_seconds=180,
        confidence=0.9,
    )
    mention4 = Mention(
        episode_id=episode.id,
        media_id=movie.id,
        timestamp_seconds=240,
        confidence=0.9,
    )
    db_session.add_all([mention1, mention2, mention3, mention4])
    db_session.commit()

    return {
        "podcast": podcast,
        "episode": episode,
        "media": [book1, book2, movie, documentary],
        "mentions": [mention1, mention2, mention3, mention4],
    }


class TestOverviewStats:
    """Tests for GET /api/v1/stats/overview endpoint."""

    def test_overview_stats_empty(self, client: TestClient) -> None:
        response = client.get("/api/v1/stats/overview")
        assert_that(response.status_code).is_equal_to(200)
        data = response.json()
        assert_that(data["total_podcasts"]).is_equal_to(0)
        assert_that(data["total_episodes"]).is_equal_to(0)
        assert_that(data["total_media"]).is_equal_to(0)
        assert_that(data["total_mentions"]).is_equal_to(0)

    def test_overview_stats_with_data(
        self, client: TestClient, sample_data: dict[str, Any]
    ) -> None:
        response = client.get("/api/v1/stats/overview")
        assert_that(response.status_code).is_equal_to(200)
        data = response.json()
        assert_that(data["total_podcasts"]).is_equal_to(1)
        assert_that(data["total_episodes"]).is_equal_to(1)
        assert_that(data["total_media"]).is_equal_to(4)
        assert_that(data["total_mentions"]).is_equal_to(4)
        assert_that(data["total_books"]).is_equal_to(2)
        # movie + documentary
        assert_that(data["total_movies"]).is_equal_to(2)


class TestStatsByType:
    """Tests for GET /api/v1/stats/by-type endpoint."""

    def test_stats_by_type_empty(self, client: TestClient) -> None:
        response = client.get("/api/v1/stats/by-type")
        assert_that(response.status_code).is_equal_to(200)
        data = response.json()
        assert_that(data).is_empty()

    def test_stats_by_type_with_data(
        self, client: TestClient, sample_data: dict[str, Any]
    ) -> None:
        response = client.get("/api/v1/stats/by-type")
        assert_that(response.status_code).is_equal_to(200)
        data = response.json()
        assert_that(data).is_length(3)  # book, movie, documentary

        # Results should be sorted by mention_count descending
        # Book has 3 mentions, movie has 1, documentary has 0
        book_stats = next(s for s in data if s["type"] == "book")
        assert_that(book_stats["mention_count"]).is_equal_to(3)
        # Verify the first item has highest mention count
        assert_that(data[0]["mention_count"]).is_greater_than_or_equal_to(
            data[1]["mention_count"]
        )

    def test_stats_by_type_includes_zero_mention_types(
        self, client: TestClient, sample_data: dict[str, Any]
    ) -> None:
        """Documentary has no mentions but should still appear."""
        response = client.get("/api/v1/stats/by-type")
        assert_that(response.status_code).is_equal_to(200)
        data: list[dict[str, Any]] = response.json()
        doc_stats = next((s for s in data if s["type"] == "documentary"), None)
        assert_that(doc_stats).is_not_none()
        assert doc_stats is not None
        assert_that(doc_stats["mention_count"]).is_equal_to(0)


class TestTopMentioned:
    """Tests for GET /api/v1/stats/top-mentioned endpoint."""

    def test_top_mentioned_empty(self, client: TestClient) -> None:
        response = client.get("/api/v1/stats/top-mentioned")
        assert_that(response.status_code).is_equal_to(200)
        data = response.json()
        assert_that(data).is_empty()

    def test_top_mentioned_with_data(
        self, client: TestClient, sample_data: dict[str, Any]
    ) -> None:
        response = client.get("/api/v1/stats/top-mentioned")
        assert_that(response.status_code).is_equal_to(200)
        data = response.json()
        # Only media with mentions are returned (2 out of 4)
        assert_that(data).is_length(2)
        # Book One should be first (3 mentions)
        assert_that(data[0]["title"]).is_equal_to("Book One")
        assert_that(data[0]["mention_count"]).is_equal_to(3)

    def test_top_mentioned_with_limit(
        self, client: TestClient, sample_data: dict[str, Any]
    ) -> None:
        response = client.get("/api/v1/stats/top-mentioned?limit=1")
        assert_that(response.status_code).is_equal_to(200)
        data = response.json()
        assert_that(data).is_length(1)

    def test_top_mentioned_filter_by_type(
        self, client: TestClient, sample_data: dict[str, Any]
    ) -> None:
        response = client.get("/api/v1/stats/top-mentioned?type=movie")
        assert_that(response.status_code).is_equal_to(200)
        data = response.json()
        assert_that(data).is_length(1)
        assert_that(data[0]["title"]).is_equal_to("Movie One")
        assert_that(data[0]["type"]).is_equal_to("movie")

    def test_top_mentioned_includes_author(
        self, client: TestClient, sample_data: dict[str, Any]
    ) -> None:
        response = client.get("/api/v1/stats/top-mentioned")
        assert_that(response.status_code).is_equal_to(200)
        data = response.json()
        book = next(m for m in data if m["title"] == "Book One")
        assert_that(book["author"]).is_equal_to("Author A")

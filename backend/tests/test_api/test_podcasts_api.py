"""Tests for podcasts API endpoints."""

from datetime import UTC, datetime
from typing import Any

import pytest
from assertpy import assert_that
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from podex.models import Episode, Media, Mention, Podcast


@pytest.fixture
def sample_data(db_session: Session) -> dict[str, Any]:
    """Create sample data for podcast tests."""
    podcast1 = Podcast(
        name="Test Podcast",
        slug="test-podcast",
        description="A test podcast",
    )
    podcast2 = Podcast(
        name="Another Podcast",
        slug="another-podcast",
        description="Another test podcast",
    )
    db_session.add_all([podcast1, podcast2])
    db_session.flush()

    episode1 = Episode(
        podcast_id=podcast1.id,
        title="Episode 1",
        episode_number=1,
        youtube_id="abc123",
        published_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    episode2 = Episode(
        podcast_id=podcast1.id,
        title="Episode 2",
        episode_number=2,
        youtube_id="def456",
        published_at=datetime(2024, 1, 15, tzinfo=UTC),
    )
    episode3 = Episode(
        podcast_id=podcast2.id,
        title="Another Episode 1",
        episode_number=1,
        youtube_id="ghi789",
        published_at=datetime(2024, 2, 1, tzinfo=UTC),
    )
    db_session.add_all([episode1, episode2, episode3])
    db_session.flush()

    media = Media(type="book", title="Test Book", author="Test Author")
    db_session.add(media)
    db_session.flush()

    mention = Mention(
        episode_id=episode1.id,
        media_id=media.id,
        timestamp_seconds=60,
        context="Mentioned Test Book",
        confidence=0.9,
    )
    db_session.add(mention)
    db_session.commit()

    return {
        "podcasts": [podcast1, podcast2],
        "episodes": [episode1, episode2, episode3],
        "media": media,
        "mention": mention,
    }


class TestListPodcasts:
    """Tests for GET /api/v1/podcasts endpoint."""

    def test_list_podcasts_empty(self, client: TestClient) -> None:
        response = client.get("/api/v1/podcasts")
        assert_that(response.status_code).is_equal_to(200)
        data = response.json()
        assert_that(data).is_empty()

    def test_list_podcasts_with_data(
        self, client: TestClient, sample_data: dict[str, Any]
    ) -> None:
        response = client.get("/api/v1/podcasts")
        assert_that(response.status_code).is_equal_to(200)
        data = response.json()
        assert_that(data).is_length(2)

    def test_list_podcasts_includes_stats(
        self, client: TestClient, sample_data: dict[str, Any]
    ) -> None:
        response = client.get("/api/v1/podcasts")
        assert_that(response.status_code).is_equal_to(200)
        data = response.json()

        test_podcast = next(p for p in data if p["slug"] == "test-podcast")
        assert_that(test_podcast["episode_count"]).is_equal_to(2)
        assert_that(test_podcast["mention_count"]).is_equal_to(1)

        another_podcast = next(p for p in data if p["slug"] == "another-podcast")
        assert_that(another_podcast["episode_count"]).is_equal_to(1)
        assert_that(another_podcast["mention_count"]).is_equal_to(0)


class TestPodcastDetail:
    """Tests for GET /api/v1/podcasts/{slug} endpoint."""

    def test_get_podcast_by_slug(
        self, client: TestClient, sample_data: dict[str, Any]
    ) -> None:
        response = client.get("/api/v1/podcasts/test-podcast")
        assert_that(response.status_code).is_equal_to(200)
        data = response.json()
        assert_that(data["name"]).is_equal_to("Test Podcast")
        assert_that(data["slug"]).is_equal_to("test-podcast")
        assert_that(data["episode_count"]).is_equal_to(2)
        assert_that(data["mention_count"]).is_equal_to(1)

    def test_get_podcast_not_found(self, client: TestClient) -> None:
        response = client.get("/api/v1/podcasts/nonexistent")
        assert_that(response.status_code).is_equal_to(404)
        assert_that(response.json()["detail"]).is_equal_to("Podcast not found")


class TestPodcastEpisodes:
    """Tests for GET /api/v1/podcasts/{slug}/episodes endpoint."""

    def test_get_podcast_episodes(
        self, client: TestClient, sample_data: dict[str, Any]
    ) -> None:
        response = client.get("/api/v1/podcasts/test-podcast/episodes")
        assert_that(response.status_code).is_equal_to(200)
        data = response.json()
        assert_that(data["items"]).is_length(2)
        assert_that(data["total"]).is_equal_to(2)

    def test_get_podcast_episodes_pagination(
        self, client: TestClient, sample_data: dict[str, Any]
    ) -> None:
        response = client.get(
            "/api/v1/podcasts/test-podcast/episodes?page=1&per_page=1"
        )
        assert_that(response.status_code).is_equal_to(200)
        data = response.json()
        assert_that(data["items"]).is_length(1)
        assert_that(data["total"]).is_equal_to(2)

    def test_get_podcast_episodes_sorted_by_episode_number(
        self, client: TestClient, sample_data: dict[str, Any]
    ) -> None:
        """Episodes should be sorted by episode_number descending."""
        response = client.get("/api/v1/podcasts/test-podcast/episodes")
        assert_that(response.status_code).is_equal_to(200)
        data = response.json()
        # Episode 2 should be first
        assert_that(data["items"][0]["episode_number"]).is_equal_to(2)

    def test_get_podcast_episodes_not_found(self, client: TestClient) -> None:
        response = client.get("/api/v1/podcasts/nonexistent/episodes")
        assert_that(response.status_code).is_equal_to(404)

    def test_get_podcast_episodes_includes_mention_count(
        self, client: TestClient, sample_data: dict[str, Any]
    ) -> None:
        response = client.get("/api/v1/podcasts/test-podcast/episodes")
        assert_that(response.status_code).is_equal_to(200)
        data = response.json()
        ep1 = next(e for e in data["items"] if e["episode_number"] == 1)
        assert_that(ep1["mention_count"]).is_equal_to(1)

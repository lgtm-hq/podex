"""Tests for episodes API endpoints."""

from datetime import UTC, datetime
from typing import Any

import pytest
from assertpy import assert_that
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from podex.models import Episode, Media, Mention, Podcast


@pytest.fixture
def sample_data(db_session: Session) -> dict[str, Any]:
    """Create sample data for episode tests."""
    podcast = Podcast(name="Test Podcast", slug="test-podcast")
    db_session.add(podcast)
    db_session.flush()

    episode1 = Episode(
        podcast_id=podcast.id,
        title="Episode 1",
        episode_number=1,
        youtube_id="abc123",
        published_at=datetime(2024, 1, 1, tzinfo=UTC),
        duration_seconds=3600,
    )
    episode2 = Episode(
        podcast_id=podcast.id,
        title="Episode 2",
        episode_number=2,
        youtube_id="def456",
        published_at=datetime(2024, 1, 15, tzinfo=UTC),
        duration_seconds=4200,
    )
    db_session.add_all([episode1, episode2])
    db_session.flush()

    media = Media(type="book", title="Test Book", author="Test Author")
    db_session.add(media)
    db_session.flush()

    mention1 = Mention(
        episode_id=episode1.id,
        media_id=media.id,
        timestamp_seconds=60,
        context="Talked about Test Book",
        confidence=0.9,
    )
    mention2 = Mention(
        episode_id=episode1.id,
        media_id=media.id,
        timestamp_seconds=120,
        context="More about Test Book",
        confidence=0.85,
    )
    db_session.add_all([mention1, mention2])
    db_session.commit()

    return {
        "podcast": podcast,
        "episodes": [episode1, episode2],
        "media": media,
        "mentions": [mention1, mention2],
    }


class TestListEpisodes:
    """Tests for GET /api/v1/episodes endpoint."""

    def test_list_episodes_empty(self, client: TestClient) -> None:
        response = client.get("/api/v1/episodes")
        assert_that(response.status_code).is_equal_to(200)
        data = response.json()
        assert_that(data["items"]).is_empty()
        assert_that(data["total"]).is_equal_to(0)

    def test_list_episodes_with_data(
        self, client: TestClient, sample_data: dict[str, Any]
    ) -> None:
        response = client.get("/api/v1/episodes")
        assert_that(response.status_code).is_equal_to(200)
        data = response.json()
        assert_that(data["items"]).is_length(2)
        assert_that(data["total"]).is_equal_to(2)

    def test_list_episodes_pagination(
        self, client: TestClient, sample_data: dict[str, Any]
    ) -> None:
        response = client.get("/api/v1/episodes?page=1&per_page=1")
        assert_that(response.status_code).is_equal_to(200)
        data = response.json()
        assert_that(data["items"]).is_length(1)
        assert_that(data["total"]).is_equal_to(2)

    def test_list_episodes_filter_by_podcast(
        self, client: TestClient, sample_data: dict[str, Any]
    ) -> None:
        podcast_id = sample_data["podcast"].id
        response = client.get(f"/api/v1/episodes?podcast_id={podcast_id}")
        assert_that(response.status_code).is_equal_to(200)
        data = response.json()
        assert_that(data["items"]).is_length(2)

    def test_list_episodes_sorted_by_date(
        self, client: TestClient, sample_data: dict[str, Any]
    ) -> None:
        """Episodes should be sorted by published_at descending."""
        response = client.get("/api/v1/episodes")
        assert_that(response.status_code).is_equal_to(200)
        data = response.json()
        # Episode 2 was published later, so it should be first
        assert_that(data["items"][0]["title"]).is_equal_to("Episode 2")

    def test_list_episodes_includes_mention_count(
        self, client: TestClient, sample_data: dict[str, Any]
    ) -> None:
        response = client.get("/api/v1/episodes")
        assert_that(response.status_code).is_equal_to(200)
        data = response.json()
        # Find Episode 1 which has 2 mentions
        ep1 = next(e for e in data["items"] if e["title"] == "Episode 1")
        assert_that(ep1["mention_count"]).is_equal_to(2)
        # Episode 2 has 0 mentions
        ep2 = next(e for e in data["items"] if e["title"] == "Episode 2")
        assert_that(ep2["mention_count"]).is_equal_to(0)


class TestEpisodeDetail:
    """Tests for GET /api/v1/episodes/{episode_id} endpoint."""

    def test_get_episode_detail(
        self, client: TestClient, sample_data: dict[str, Any]
    ) -> None:
        episode_id = sample_data["episodes"][0].id
        response = client.get(f"/api/v1/episodes/{episode_id}")
        assert_that(response.status_code).is_equal_to(200)
        data = response.json()
        assert_that(data["id"]).is_equal_to(episode_id)
        assert_that(data["title"]).is_equal_to("Episode 1")
        assert_that(data["mention_count"]).is_equal_to(2)

    def test_get_episode_detail_not_found(self, client: TestClient) -> None:
        response = client.get("/api/v1/episodes/99999")
        assert_that(response.status_code).is_equal_to(404)
        assert_that(response.json()["detail"]).is_equal_to("Episode not found")


class TestEpisodeMentions:
    """Tests for GET /api/v1/episodes/{episode_id}/mentions endpoint."""

    def test_get_episode_mentions(
        self, client: TestClient, sample_data: dict[str, Any]
    ) -> None:
        episode_id = sample_data["episodes"][0].id
        response = client.get(f"/api/v1/episodes/{episode_id}/mentions")
        assert_that(response.status_code).is_equal_to(200)
        data = response.json()
        assert_that(data).is_length(2)

    def test_get_episode_mentions_includes_media_info(
        self, client: TestClient, sample_data: dict[str, Any]
    ) -> None:
        episode_id = sample_data["episodes"][0].id
        response = client.get(f"/api/v1/episodes/{episode_id}/mentions")
        assert_that(response.status_code).is_equal_to(200)
        data = response.json()
        for mention in data:
            assert_that(mention).contains_key("media_title")
            assert_that(mention).contains_key("media_type")
            assert_that(mention["media_title"]).is_equal_to("Test Book")

    def test_get_episode_mentions_includes_youtube_url(
        self, client: TestClient, sample_data: dict[str, Any]
    ) -> None:
        episode_id = sample_data["episodes"][0].id
        response = client.get(f"/api/v1/episodes/{episode_id}/mentions")
        assert_that(response.status_code).is_equal_to(200)
        data = response.json()
        for mention in data:
            assert_that(mention["youtube_timestamp_url"]).contains("youtube.com")
            assert_that(mention["youtube_timestamp_url"]).contains("abc123")

    def test_get_episode_mentions_not_found(self, client: TestClient) -> None:
        response = client.get("/api/v1/episodes/99999/mentions")
        assert_that(response.status_code).is_equal_to(404)

    def test_get_episode_mentions_empty(
        self, client: TestClient, sample_data: dict[str, Any]
    ) -> None:
        """Episode 2 has no mentions."""
        episode_id = sample_data["episodes"][1].id
        response = client.get(f"/api/v1/episodes/{episode_id}/mentions")
        assert_that(response.status_code).is_equal_to(200)
        data = response.json()
        assert_that(data).is_empty()

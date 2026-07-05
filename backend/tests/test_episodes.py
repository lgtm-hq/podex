"""Tests for the public episode endpoints."""

from assertpy import assert_that
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from podex.models import Episode, Podcast


def _seed_podcast(db: Session) -> int:
    podcast = Podcast(name="The Example Show", slug="example-show")
    db.add(podcast)
    db.commit()
    return podcast.id


def test_list_episodes_empty(client: TestClient) -> None:
    """An empty catalog returns an empty list."""
    response = client.get("/api/v2/episodes")

    assert_that(response.status_code).is_equal_to(200)
    assert_that(response.json()).is_equal_to([])


def test_list_and_get_episode(client: TestClient, db_session: Session) -> None:
    """A stored episode is listed and retrievable by id."""
    podcast_id = _seed_podcast(db_session)
    db_session.add(Episode(podcast_id=podcast_id, title="Pilot", episode_number=1))
    db_session.commit()

    listed = client.get("/api/v2/episodes")
    assert_that(listed.status_code).is_equal_to(200)
    body = listed.json()
    assert_that(body).is_length(1)
    assert_that(body[0]["title"]).is_equal_to("Pilot")

    episode_id = body[0]["id"]
    fetched = client.get(f"/api/v2/episodes/{episode_id}")
    assert_that(fetched.status_code).is_equal_to(200)
    assert_that(fetched.json()["podcast_id"]).is_equal_to(podcast_id)


def test_list_episodes_filtered_by_podcast(
    client: TestClient,
    db_session: Session,
) -> None:
    """The podcast_id query filter narrows the results."""
    first = _seed_podcast(db_session)
    other = Podcast(name="Other Show", slug="other-show")
    db_session.add(other)
    db_session.commit()
    db_session.add(Episode(podcast_id=first, title="A"))
    db_session.add(Episode(podcast_id=other.id, title="B"))
    db_session.commit()

    response = client.get("/api/v2/episodes", params={"podcast_id": first})

    assert_that(response.status_code).is_equal_to(200)
    body = response.json()
    assert_that(body).is_length(1)
    assert_that(body[0]["title"]).is_equal_to("A")


def test_get_episode_not_found(client: TestClient) -> None:
    """An unknown episode id returns 404."""
    response = client.get("/api/v2/episodes/999")

    assert_that(response.status_code).is_equal_to(404)

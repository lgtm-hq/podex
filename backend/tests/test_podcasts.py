"""Tests for the public podcast endpoints."""

from assertpy import assert_that
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from podex.models import Podcast


def test_list_podcasts_empty(client: TestClient) -> None:
    """An empty catalog returns an empty list."""
    response = client.get("/api/v2/podcasts")

    assert_that(response.status_code).is_equal_to(200)
    assert_that(response.json()).is_equal_to([])


def test_list_and_get_podcast(client: TestClient, db_session: Session) -> None:
    """A stored podcast is listed and retrievable by id."""
    db_session.add(
        Podcast(name="The Example Show", slug="example-show", description="A show."),
    )
    db_session.commit()

    listed = client.get("/api/v2/podcasts")
    assert_that(listed.status_code).is_equal_to(200)
    body = listed.json()
    assert_that(body).is_length(1)
    assert_that(body[0]["slug"]).is_equal_to("example-show")

    podcast_id = body[0]["id"]
    fetched = client.get(f"/api/v2/podcasts/{podcast_id}")
    assert_that(fetched.status_code).is_equal_to(200)
    assert_that(fetched.json()["name"]).is_equal_to("The Example Show")


def test_get_podcast_not_found(client: TestClient) -> None:
    """An unknown podcast id returns 404."""
    response = client.get("/api/v2/podcasts/999")

    assert_that(response.status_code).is_equal_to(404)

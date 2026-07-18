"""Tests for the public podcast endpoints."""

from assertpy import assert_that
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from podex.api.v2.identifiers import IdentifierKind, encode
from podex.models import Podcast


def test_list_podcasts_empty(client: TestClient) -> None:
    """An empty catalog returns an empty page envelope."""
    response = client.get("/api/v2/podcasts")

    assert_that(response.status_code).is_equal_to(200)
    body = response.json()
    assert_that(body["items"]).is_equal_to([])
    assert_that(body["total"]).is_equal_to(0)
    assert_that(body["limit"]).is_equal_to(50)
    assert_that(body["offset"]).is_equal_to(0)


def test_list_and_get_podcast(client: TestClient, db_session: Session) -> None:
    """A stored podcast is listed and retrievable by id."""
    db_session.add(
        Podcast(name="The Example Show", slug="example-show", description="A show."),
    )
    db_session.commit()

    listed = client.get("/api/v2/podcasts")
    assert_that(listed.status_code).is_equal_to(200)
    body = listed.json()
    assert_that(body["items"]).is_length(1)
    assert_that(body["items"][0]["slug"]).is_equal_to("example-show")
    assert_that(body["total"]).is_equal_to(1)

    podcast_id = body["items"][0]["id"]
    assert_that(body["items"][0]["public_id"]).is_equal_to(
        encode(IdentifierKind.PODCAST, podcast_id),
    )
    fetched = client.get(f"/api/v2/podcasts/{podcast_id}")
    assert_that(fetched.status_code).is_equal_to(200)
    assert_that(fetched.json()["name"]).is_equal_to("The Example Show")


def test_get_podcast_not_found(client: TestClient) -> None:
    """An unknown podcast id returns the v2 error envelope."""
    response = client.get("/api/v2/podcasts/999")

    assert_that(response.status_code).is_equal_to(404)
    body = response.json()
    assert_that(body["code"]).is_equal_to("not_found")
    assert_that(body["detail"]).is_equal_to("Podcast not found")
    assert_that(body["request_id"]).is_not_empty()

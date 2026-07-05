"""Tests for the public media endpoints."""

from assertpy import assert_that
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from podex.models import Media, MediaType


def test_list_media_empty(client: TestClient) -> None:
    """An empty catalog returns an empty list."""
    response = client.get("/api/v2/media")

    assert_that(response.status_code).is_equal_to(200)
    assert_that(response.json()).is_equal_to([])


def test_list_and_get_media(client: TestClient, db_session: Session) -> None:
    """A stored media item is listed and retrievable by id."""
    db_session.add(Media(type=MediaType.BOOK, title="Dune", author="Herbert"))
    db_session.commit()

    listed = client.get("/api/v2/media")
    assert_that(listed.status_code).is_equal_to(200)
    body = listed.json()
    assert_that(body).is_length(1)
    assert_that(body[0]["type"]).is_equal_to("book")
    assert_that(body[0]["title"]).is_equal_to("Dune")

    media_id = body[0]["id"]
    fetched = client.get(f"/api/v2/media/{media_id}")
    assert_that(fetched.status_code).is_equal_to(200)
    assert_that(fetched.json()["author"]).is_equal_to("Herbert")


def test_list_media_filtered_by_type(client: TestClient, db_session: Session) -> None:
    """The media_type query filter narrows the results."""
    db_session.add(Media(type=MediaType.BOOK, title="Dune"))
    db_session.add(Media(type=MediaType.MOVIE, title="Arrival"))
    db_session.commit()

    response = client.get("/api/v2/media", params={"media_type": "movie"})

    assert_that(response.status_code).is_equal_to(200)
    body = response.json()
    assert_that(body).is_length(1)
    assert_that(body[0]["title"]).is_equal_to("Arrival")


def test_get_media_not_found(client: TestClient) -> None:
    """An unknown media id returns 404."""
    response = client.get("/api/v2/media/999")

    assert_that(response.status_code).is_equal_to(404)

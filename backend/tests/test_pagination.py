"""Tests for the shared v2 pagination convention."""

from assertpy import assert_that
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from podex.api.v2.schemas import DEFAULT_LIMIT, MAX_LIMIT
from podex.models import Episode, Media, MediaType, Mention, Podcast


def _seed_podcasts(db: Session, count: int) -> None:
    """Insert ``count`` podcast rows with predictable, sortable names.

    Args:
        db: Active database session.
        count: Number of podcast rows to insert.
    """
    for index in range(count):
        db.add(Podcast(name=f"Show {index:03d}", slug=f"show-{index:03d}"))
    db.commit()


def test_default_pagination_returns_default_limit(
    client: TestClient,
    db_session: Session,
) -> None:
    """Without query params, list endpoints use the default limit."""
    _seed_podcasts(db_session, count=DEFAULT_LIMIT + 5)

    response = client.get("/api/v2/podcasts")

    assert_that(response.status_code).is_equal_to(200)
    body = response.json()
    assert_that(body["items"]).is_length(DEFAULT_LIMIT)
    assert_that(body["total"]).is_equal_to(DEFAULT_LIMIT + 5)
    assert_that(body["limit"]).is_equal_to(DEFAULT_LIMIT)
    assert_that(body["offset"]).is_equal_to(0)


def test_pagination_offset_slices_results(
    client: TestClient,
    db_session: Session,
) -> None:
    """The ``offset`` parameter skips the requested number of items."""
    _seed_podcasts(db_session, count=5)

    first = client.get("/api/v2/podcasts", params={"limit": 2, "offset": 0}).json()
    second = client.get("/api/v2/podcasts", params={"limit": 2, "offset": 2}).json()

    first_ids = [item["id"] for item in first["items"]]
    second_ids = [item["id"] for item in second["items"]]
    assert_that(first_ids).is_length(2)
    assert_that(second_ids).is_length(2)
    assert_that(set(first_ids).intersection(second_ids)).is_empty()
    assert_that(first["total"]).is_equal_to(5)
    assert_that(first["limit"]).is_equal_to(2)
    assert_that(second["offset"]).is_equal_to(2)


def test_pagination_rejects_out_of_range_limit(client: TestClient) -> None:
    """A ``limit`` above the maximum yields a validation-error envelope."""
    response = client.get("/api/v2/podcasts", params={"limit": MAX_LIMIT + 1})

    assert_that(response.status_code).is_equal_to(422)
    body = response.json()
    assert_that(body["error"]["code"]).is_equal_to("unprocessable_entity")
    loc_paths = [tuple(item["loc"]) for item in body["error"]["details"]]
    assert_that(loc_paths).contains(("query", "limit"))


def test_pagination_rejects_negative_offset(client: TestClient) -> None:
    """A negative ``offset`` yields a validation-error envelope."""
    response = client.get("/api/v2/podcasts", params={"offset": -1})

    assert_that(response.status_code).is_equal_to(422)
    body = response.json()
    assert_that(body["error"]["code"]).is_equal_to("unprocessable_entity")


def test_pagination_applies_to_episode_and_media_lists(
    client: TestClient,
    db_session: Session,
) -> None:
    """The shared pagination shape is honoured across every list endpoint."""
    podcast = Podcast(name="Show", slug="show")
    db_session.add(podcast)
    db_session.commit()
    for index in range(3):
        db_session.add(Episode(podcast_id=podcast.id, title=f"Ep {index}"))
    for index in range(3):
        db_session.add(Media(type=MediaType.BOOK, title=f"Book {index}"))
    db_session.commit()

    episodes = client.get("/api/v2/episodes", params={"limit": 2}).json()
    media = client.get("/api/v2/media", params={"limit": 2}).json()

    assert_that(episodes["items"]).is_length(2)
    assert_that(episodes["total"]).is_equal_to(3)
    assert_that(episodes["limit"]).is_equal_to(2)
    assert_that(media["items"]).is_length(2)
    assert_that(media["total"]).is_equal_to(3)
    assert_that(media["limit"]).is_equal_to(2)


def test_pagination_applies_to_mention_lists(
    client: TestClient,
    db_session: Session,
) -> None:
    """Nested mention lists share the same pagination envelope."""
    podcast = Podcast(name="Show", slug="show")
    db_session.add(podcast)
    db_session.commit()
    episode = Episode(podcast_id=podcast.id, title="Pilot")
    media = Media(type=MediaType.BOOK, title="Dune")
    db_session.add_all([episode, media])
    db_session.commit()
    for index in range(4):
        db_session.add(
            Mention(
                episode_id=episode.id,
                media_id=media.id,
                timestamp_seconds=index,
            ),
        )
    db_session.commit()

    response = client.get(
        f"/api/v2/episodes/{episode.id}/mentions",
        params={"limit": 2, "offset": 1},
    )

    assert_that(response.status_code).is_equal_to(200)
    body = response.json()
    assert_that(body["items"]).is_length(2)
    assert_that(body["total"]).is_equal_to(4)
    assert_that(body["offset"]).is_equal_to(1)

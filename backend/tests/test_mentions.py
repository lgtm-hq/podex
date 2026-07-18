"""Tests for the nested mention endpoints."""

from assertpy import assert_that
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from podex.models import Episode, Media, MediaType, Mention, Podcast


def _seed_mention(db: Session) -> Mention:
    """Insert and return a complete mention fixture.

    Builds a podcast, episode, media item, and the mention that links them.

    Args:
        db: Active SQLAlchemy session.

    Returns:
        The newly created and committed Mention instance.
    """
    podcast = Podcast(name="The Show", slug="the-show")
    db.add(podcast)
    db.commit()
    episode = Episode(podcast_id=podcast.id, title="Pilot")
    media = Media(type=MediaType.BOOK, title="Dune")
    db.add_all([episode, media])
    db.commit()
    mention = Mention(
        episode_id=episode.id,
        media_id=media.id,
        timestamp_seconds=42,
        context="a great book",
    )
    db.add(mention)
    db.commit()
    return mention


def test_episode_mentions(client: TestClient, db_session: Session) -> None:
    """An episode's mentions are listed inside a page envelope."""
    mention = _seed_mention(db_session)

    response = client.get(f"/api/v2/episodes/{mention.episode_id}/mentions")

    assert_that(response.status_code).is_equal_to(200)
    body = response.json()
    assert_that(body["items"]).is_length(1)
    assert_that(body["items"][0]["media_id"]).is_equal_to(mention.media_id)
    assert_that(body["items"][0]["timestamp_seconds"]).is_equal_to(42)
    assert_that(body["items"][0]).contains_key(
        "public_id",
        "media_public_id",
        "episode_public_id",
    )
    assert_that(body["total"]).is_equal_to(1)


def test_media_mentions(client: TestClient, db_session: Session) -> None:
    """A media item's mentions are listed inside a page envelope."""
    mention = _seed_mention(db_session)

    response = client.get(f"/api/v2/media/{mention.media_id}/mentions")

    assert_that(response.status_code).is_equal_to(200)
    body = response.json()
    assert_that(body["items"]).is_length(1)
    assert_that(body["items"][0]["episode_id"]).is_equal_to(mention.episode_id)


def test_episode_mentions_empty(client: TestClient, db_session: Session) -> None:
    """An episode with no mentions returns an empty page envelope."""
    podcast = Podcast(name="The Show", slug="the-show")
    db_session.add(podcast)
    db_session.commit()
    episode = Episode(podcast_id=podcast.id, title="Pilot")
    db_session.add(episode)
    db_session.commit()

    response = client.get(f"/api/v2/episodes/{episode.id}/mentions")

    assert_that(response.status_code).is_equal_to(200)
    body = response.json()
    assert_that(body["items"]).is_equal_to([])
    assert_that(body["total"]).is_equal_to(0)


def test_episode_mentions_not_found(client: TestClient) -> None:
    """Mentions for an unknown episode return a 404 envelope."""
    response = client.get("/api/v2/episodes/999/mentions")

    assert_that(response.status_code).is_equal_to(404)
    assert_that(response.json()["code"]).is_equal_to("not_found")


def test_media_mentions_not_found(client: TestClient) -> None:
    """Mentions for an unknown media item return a 404 envelope."""
    response = client.get("/api/v2/media/999/mentions")

    assert_that(response.status_code).is_equal_to(404)
    assert_that(response.json()["code"]).is_equal_to("not_found")

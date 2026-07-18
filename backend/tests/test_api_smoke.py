"""Cross-resource smoke tests for the live v2 API surface.

Unlike the per-resource unit tests, these exercise the whole read surface
through the running FastAPI application so that a regression in wiring
(routing, serialization, or the persisted ORM graph) is caught as a failing
end-to-end walk across podcasts, episodes, media, and mentions.
"""

from dataclasses import dataclass

from assertpy import assert_that
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from podex.models import Episode, Media, MediaType, Mention, Podcast


@dataclass
class SeededGraph:
    """Identifiers for a linked podcast, episode, media, and mention graph.

    Attributes:
        podcast_id: Identifier of the seeded podcast source.
        episode_id: Identifier of the seeded episode belonging to the podcast.
        media_id: Identifier of the seeded canonical media item.
        mention_id: Identifier of the mention linking the episode to the media.
    """

    podcast_id: int
    episode_id: int
    media_id: int
    mention_id: int


def _seed_graph(db: Session) -> SeededGraph:
    """Persist a fully connected catalog graph and return its identifiers.

    Args:
        db: Database session used to persist the catalog rows.

    Returns:
        The identifiers of the seeded podcast, episode, media, and mention.
    """
    podcast = Podcast(
        name="The Example Show",
        slug="example-show",
        description="A show about examples.",
    )
    db.add(podcast)
    db.commit()

    episode = Episode(podcast_id=podcast.id, title="Pilot", episode_number=1)
    media = Media(type=MediaType.BOOK, title="Dune", author="Herbert", year=1965)
    db.add_all([episode, media])
    db.commit()

    mention = Mention(
        episode_id=episode.id,
        media_id=media.id,
        timestamp_seconds=42,
        context="a great book",
        confidence=0.9,
    )
    db.add(mention)
    db.commit()

    return SeededGraph(
        podcast_id=podcast.id,
        episode_id=episode.id,
        media_id=media.id,
        mention_id=mention.id,
    )


def test_health_and_status_smoke(client: TestClient) -> None:
    """Both the liveness probe and the v2 status endpoint respond healthy."""
    health = client.get("/health")
    assert_that(health.status_code).is_equal_to(200)
    assert_that(health.json()).is_equal_to({"status": "ok"})

    status = client.get("/api/v2/status")
    assert_that(status.status_code).is_equal_to(200)
    assert_that(status.json()).is_equal_to({"status": "ok", "api": "v2"})


def test_podcast_resource_smoke(client: TestClient, db_session: Session) -> None:
    """Podcasts are listed, individually fetchable, and 404 when unknown."""
    graph = _seed_graph(db_session)

    listed = client.get("/api/v2/podcasts")
    assert_that(listed.status_code).is_equal_to(200)
    listed_items = listed.json()["items"]
    assert_that([item["id"] for item in listed_items]).contains(graph.podcast_id)

    fetched = client.get(f"/api/v2/podcasts/{graph.podcast_id}")
    assert_that(fetched.status_code).is_equal_to(200)
    assert_that(fetched.json()["slug"]).is_equal_to("example-show")

    missing = client.get("/api/v2/podcasts/999")
    assert_that(missing.status_code).is_equal_to(404)


def test_episode_resource_smoke(client: TestClient, db_session: Session) -> None:
    """Episodes list, filter by podcast, fetch, expose mentions, and 404."""
    graph = _seed_graph(db_session)

    listed = client.get("/api/v2/episodes")
    assert_that(listed.status_code).is_equal_to(200)
    listed_items = listed.json()["items"]
    assert_that([item["id"] for item in listed_items]).contains(graph.episode_id)

    filtered = client.get("/api/v2/episodes", params={"podcast_id": graph.podcast_id})
    assert_that(filtered.status_code).is_equal_to(200)
    filtered_items = filtered.json()["items"]
    assert_that([item["id"] for item in filtered_items]).is_equal_to(
        [graph.episode_id],
    )

    fetched = client.get(f"/api/v2/episodes/{graph.episode_id}")
    assert_that(fetched.status_code).is_equal_to(200)
    assert_that(fetched.json()["title"]).is_equal_to("Pilot")

    mentions = client.get(f"/api/v2/episodes/{graph.episode_id}/mentions")
    assert_that(mentions.status_code).is_equal_to(200)
    mention_items = mentions.json()["items"]
    assert_that([item["id"] for item in mention_items]).is_equal_to(
        [graph.mention_id],
    )

    missing = client.get("/api/v2/episodes/999")
    assert_that(missing.status_code).is_equal_to(404)


def test_media_resource_smoke(client: TestClient, db_session: Session) -> None:
    """Media list, filter by type, fetch, expose mentions, and 404."""
    graph = _seed_graph(db_session)

    listed = client.get("/api/v2/media")
    assert_that(listed.status_code).is_equal_to(200)
    listed_items = listed.json()["items"]
    assert_that([item["id"] for item in listed_items]).contains(graph.media_id)

    filtered = client.get("/api/v2/media", params={"media_type": "book"})
    assert_that(filtered.status_code).is_equal_to(200)
    filtered_items = filtered.json()["items"]
    assert_that([item["id"] for item in filtered_items]).is_equal_to([graph.media_id])

    empty = client.get("/api/v2/media", params={"media_type": "movie"})
    assert_that(empty.status_code).is_equal_to(200)
    assert_that(empty.json()["items"]).is_equal_to([])

    fetched = client.get(f"/api/v2/media/{graph.media_id}")
    assert_that(fetched.status_code).is_equal_to(200)
    assert_that(fetched.json()["author"]).is_equal_to("Herbert")

    mentions = client.get(f"/api/v2/media/{graph.media_id}/mentions")
    assert_that(mentions.status_code).is_equal_to(200)
    mention_items = mentions.json()["items"]
    assert_that([item["id"] for item in mention_items]).is_equal_to(
        [graph.mention_id],
    )

    missing = client.get("/api/v2/media/999")
    assert_that(missing.status_code).is_equal_to(404)


def test_full_graph_walk_via_http(client: TestClient, db_session: Session) -> None:
    """A seeded graph is fully traversable through the public HTTP surface.

    Starting from the podcast, the walk follows episodes to their mentions and
    then resolves each mention back to its canonical media item, asserting that
    the foreign-key relationships survive serialization end to end.
    """
    graph = _seed_graph(db_session)

    podcast = client.get(f"/api/v2/podcasts/{graph.podcast_id}").json()
    assert_that(podcast["id"]).is_equal_to(graph.podcast_id)

    episodes = client.get(
        "/api/v2/episodes",
        params={"podcast_id": graph.podcast_id},
    ).json()["items"]
    assert_that(episodes).is_length(1)
    episode = episodes[0]
    assert_that(episode["podcast_id"]).is_equal_to(graph.podcast_id)

    mentions = client.get(f"/api/v2/episodes/{episode['id']}/mentions").json()["items"]
    assert_that(mentions).is_length(1)
    mention = mentions[0]
    assert_that(mention["episode_id"]).is_equal_to(episode["id"])
    assert_that(mention["media_id"]).is_equal_to(graph.media_id)
    assert_that(mention["timestamp_seconds"]).is_equal_to(42)

    media = client.get(f"/api/v2/media/{mention['media_id']}").json()
    assert_that(media["id"]).is_equal_to(graph.media_id)
    assert_that(media["title"]).is_equal_to("Dune")

    back_reference = client.get(f"/api/v2/media/{media['id']}/mentions").json()["items"]
    assert_that([item["id"] for item in back_reference]).is_equal_to(
        [graph.mention_id],
    )

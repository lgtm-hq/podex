"""Cross-resource smoke tests for the live v2 API surface.

Unlike the per-resource unit tests, these exercise the whole read surface
through the running FastAPI application so that a regression in wiring
(routing, serialization, or the persisted ORM graph) is caught as a failing
end-to-end walk across podcasts, episodes, media, and mentions.
"""

from assertpy import assert_that
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from podex.models import Episode
from tests.conftest import SeededGraph, seed_catalog_graph


def _assert_empty_page(body: dict[str, object]) -> None:
    """Assert that a list response is an empty page envelope."""
    assert_that(body["items"]).is_equal_to([])
    assert_that(body["total"]).is_equal_to(0)


def test_health_and_status_smoke(client: TestClient) -> None:
    """Both the liveness probe and the v2 status endpoint respond healthy."""
    health = client.get("/health")
    assert_that(health.status_code).is_equal_to(200)
    assert_that(health.json()).is_equal_to({"status": "ok"})

    status = client.get("/api/v2/status")
    assert_that(status.status_code).is_equal_to(200)
    assert_that(status.json()).is_equal_to({"status": "ok", "api": "v2"})


def test_empty_catalog_smoke(client: TestClient) -> None:
    """An unseeded database returns empty page envelopes across the catalog."""
    _assert_empty_page(client.get("/api/v2/podcasts").json())
    _assert_empty_page(client.get("/api/v2/episodes").json())
    _assert_empty_page(client.get("/api/v2/media").json())


def test_podcast_resource_smoke(client: TestClient, seeded_graph: SeededGraph) -> None:
    """Podcasts are listed, individually fetchable, and 404 when unknown."""
    listed = client.get("/api/v2/podcasts")
    assert_that(listed.status_code).is_equal_to(200)
    listed_items = listed.json()["items"]
    assert_that([item["id"] for item in listed_items]).contains(
        seeded_graph.podcast_id,
    )

    fetched = client.get(f"/api/v2/podcasts/{seeded_graph.podcast_id}")
    assert_that(fetched.status_code).is_equal_to(200)
    assert_that(fetched.json()["slug"]).is_equal_to("example-show")

    missing = client.get("/api/v2/podcasts/999")
    assert_that(missing.status_code).is_equal_to(404)
    error = missing.json()
    assert_that(error["code"]).is_equal_to("not_found")
    assert_that(error["detail"]).is_equal_to("Podcast not found")


def test_episode_resource_smoke(client: TestClient, seeded_graph: SeededGraph) -> None:
    """Episodes list, filter by podcast, fetch, expose mentions, and 404."""
    listed = client.get("/api/v2/episodes")
    assert_that(listed.status_code).is_equal_to(200)
    listed_items = listed.json()["items"]
    assert_that([item["id"] for item in listed_items]).contains(
        seeded_graph.episode_id,
    )

    filtered = client.get(
        "/api/v2/episodes",
        params={"podcast_id": seeded_graph.podcast_id},
    )
    assert_that(filtered.status_code).is_equal_to(200)
    filtered_items = filtered.json()["items"]
    assert_that([item["id"] for item in filtered_items]).is_equal_to(
        [seeded_graph.episode_id],
    )

    no_matches = client.get("/api/v2/episodes", params={"podcast_id": 999})
    assert_that(no_matches.status_code).is_equal_to(200)
    _assert_empty_page(no_matches.json())

    fetched = client.get(f"/api/v2/episodes/{seeded_graph.episode_id}")
    assert_that(fetched.status_code).is_equal_to(200)
    assert_that(fetched.json()["title"]).is_equal_to("Pilot")

    mentions = client.get(f"/api/v2/episodes/{seeded_graph.episode_id}/mentions")
    assert_that(mentions.status_code).is_equal_to(200)
    mention_items = mentions.json()["items"]
    assert_that([item["id"] for item in mention_items]).is_equal_to(
        [seeded_graph.mention_id],
    )

    missing = client.get("/api/v2/episodes/999")
    assert_that(missing.status_code).is_equal_to(404)
    error = missing.json()
    assert_that(error["code"]).is_equal_to("not_found")
    assert_that(error["detail"]).is_equal_to("Episode not found")

    missing_mentions = client.get("/api/v2/episodes/999/mentions")
    assert_that(missing_mentions.status_code).is_equal_to(404)
    assert_that(missing_mentions.json()["code"]).is_equal_to("not_found")


def test_media_resource_smoke(client: TestClient, seeded_graph: SeededGraph) -> None:
    """Media list, filter by type, fetch, expose mentions, and 404."""
    listed = client.get("/api/v2/media")
    assert_that(listed.status_code).is_equal_to(200)
    listed_items = listed.json()["items"]
    assert_that([item["id"] for item in listed_items]).contains(seeded_graph.media_id)

    filtered = client.get("/api/v2/media", params={"media_type": "book"})
    assert_that(filtered.status_code).is_equal_to(200)
    filtered_items = filtered.json()["items"]
    assert_that([item["id"] for item in filtered_items]).is_equal_to(
        [seeded_graph.media_id],
    )

    empty = client.get("/api/v2/media", params={"media_type": "movie"})
    assert_that(empty.status_code).is_equal_to(200)
    assert_that(empty.json()["items"]).is_equal_to([])

    fetched = client.get(f"/api/v2/media/{seeded_graph.media_id}")
    assert_that(fetched.status_code).is_equal_to(200)
    assert_that(fetched.json()["author"]).is_equal_to("Herbert")

    mentions = client.get(f"/api/v2/media/{seeded_graph.media_id}/mentions")
    assert_that(mentions.status_code).is_equal_to(200)
    mention_items = mentions.json()["items"]
    assert_that([item["id"] for item in mention_items]).is_equal_to(
        [seeded_graph.mention_id],
    )

    missing = client.get("/api/v2/media/999")
    assert_that(missing.status_code).is_equal_to(404)
    error = missing.json()
    assert_that(error["code"]).is_equal_to("not_found")
    assert_that(error["detail"]).is_equal_to("Media not found")

    missing_mentions = client.get("/api/v2/media/999/mentions")
    assert_that(missing_mentions.status_code).is_equal_to(404)
    assert_that(missing_mentions.json()["code"]).is_equal_to("not_found")


def test_full_graph_walk_via_http(
    client: TestClient, seeded_graph: SeededGraph
) -> None:
    """A seeded graph is fully traversable through the public HTTP surface.

    Starting from the podcast, the walk follows episodes to their mentions and
    then resolves each mention back to its canonical media item, asserting that
    the foreign-key relationships survive serialization end to end.
    """
    podcast = client.get(f"/api/v2/podcasts/{seeded_graph.podcast_id}").json()
    assert_that(podcast["id"]).is_equal_to(seeded_graph.podcast_id)

    episodes = client.get(
        "/api/v2/episodes",
        params={"podcast_id": seeded_graph.podcast_id},
    ).json()["items"]
    assert_that(episodes).is_length(1)
    episode = episodes[0]
    assert_that(episode["podcast_id"]).is_equal_to(seeded_graph.podcast_id)

    mentions = client.get(f"/api/v2/episodes/{episode['id']}/mentions").json()["items"]
    assert_that(mentions).is_length(1)
    mention = mentions[0]
    assert_that(mention["episode_id"]).is_equal_to(episode["id"])
    assert_that(mention["media_id"]).is_equal_to(seeded_graph.media_id)
    assert_that(mention["timestamp_seconds"]).is_equal_to(42)

    media = client.get(f"/api/v2/media/{mention['media_id']}").json()
    assert_that(media["id"]).is_equal_to(seeded_graph.media_id)
    assert_that(media["title"]).is_equal_to("Dune")

    back_reference = client.get(f"/api/v2/media/{media['id']}/mentions").json()["items"]
    assert_that([item["id"] for item in back_reference]).is_equal_to(
        [seeded_graph.mention_id],
    )


def test_episode_without_mentions_returns_empty_page(
    client: TestClient,
    db_session: Session,
) -> None:
    """An episode with no linked mentions still returns a 200 empty page."""
    graph = seed_catalog_graph(db_session)
    second = Episode(podcast_id=graph.podcast_id, title="Solo")
    db_session.add(second)
    db_session.commit()

    response = client.get(f"/api/v2/episodes/{second.id}/mentions")
    assert_that(response.status_code).is_equal_to(200)
    _assert_empty_page(response.json())

"""Acceptance-style guards for the read-only catalog API contract.

Phase-2 scenarios from plan §10 (transcript ingestion, extraction pipelines)
are tracked under https://github.com/lgtm-hq/podex/issues/20. Those flows have
no implementation on main yet, so this module only covers the current GET
catalog surface that the OpenAPI client depends on.
"""

import json
from datetime import UTC, datetime

from assertpy import assert_that
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from podex.models import Episode, Media, MediaType, Mention, Podcast
from podex.openapi import build_openapi_schema
from tests.conftest import SeededGraph, seed_catalog_graph

READ_DTO_SCHEMAS = ("PodcastRead", "EpisodeRead", "MediaRead", "MentionRead")
PAGE_SCHEMA_PREFIX = "Page_"

# Aggregate endpoints that intentionally return a singleton DTO rather than a
# paginated collection or a per-resource Read DTO.
SINGLETON_SCHEMAS_BY_PATH = {"/api/v2/stats": "CatalogStats"}


def _openapi_required_fields(schema_name: str) -> set[str]:
    """Return the required property names for an OpenAPI component schema."""
    schema = build_openapi_schema()["components"]["schemas"][schema_name]
    return set(schema["required"])


def _openapi_media_type_values() -> list[str]:
    """Return the MediaType enum values declared in the OpenAPI artifact."""
    schema = build_openapi_schema()["components"]["schemas"]["MediaType"]
    return list(schema["enum"])


def test_media_type_enum_matches_openapi() -> None:
    """Python MediaType values stay aligned with the committed OpenAPI enum."""
    python_values = sorted(member.value for member in MediaType)
    openapi_values = sorted(_openapi_media_type_values())

    assert_that(python_values).is_equal_to(openapi_values)


def test_all_media_types_round_trip_through_api(
    client: TestClient,
    db_session: Session,
) -> None:
    """Every MediaType serializes as its wire value and filters correctly."""
    for index, media_type in enumerate(MediaType):
        db_session.add(
            Media(
                type=media_type,
                title=f"Item {index}",
                author="Author",
                year=2020,
            ),
        )
    db_session.commit()

    listed = client.get("/api/v2/media")
    assert_that(listed.status_code).is_equal_to(200)
    listed_types = {item["type"] for item in listed.json()["items"]}
    assert_that(listed_types).is_equal_to({member.value for member in MediaType})

    for media_type in MediaType:
        filtered = client.get(
            "/api/v2/media",
            params={"media_type": media_type.value},
        )
        assert_that(filtered.status_code).is_equal_to(200)
        body = filtered.json()
        assert_that(body["items"]).is_length(1)
        assert_that(body["items"][0]["type"]).is_equal_to(media_type.value)
        assert_that(body["total"]).is_equal_to(1)


def test_read_dto_fields_match_openapi_contract(
    client: TestClient,
    seeded_graph: SeededGraph,
) -> None:
    """List/detail responses expose every field the generated client expects."""
    podcast = client.get(f"/api/v2/podcasts/{seeded_graph.podcast_id}").json()
    assert_that(set(podcast)).is_equal_to(_openapi_required_fields("PodcastRead"))

    episode = client.get(f"/api/v2/episodes/{seeded_graph.episode_id}").json()
    assert_that(set(episode)).is_equal_to(_openapi_required_fields("EpisodeRead"))

    media = client.get(f"/api/v2/media/{seeded_graph.media_id}").json()
    assert_that(set(media)).is_equal_to(_openapi_required_fields("MediaRead"))

    mentions = client.get(
        f"/api/v2/episodes/{seeded_graph.episode_id}/mentions",
    ).json()["items"]
    assert_that(mentions).is_length(1)
    assert_that(set(mentions[0])).is_equal_to(_openapi_required_fields("MentionRead"))


def test_list_endpoints_return_page_envelope(
    client: TestClient,
    seeded_graph: SeededGraph,
) -> None:
    """Every catalog list route returns the shared Page envelope shape."""
    for path in (
        "/api/v2/podcasts",
        "/api/v2/episodes",
        "/api/v2/media",
        f"/api/v2/episodes/{seeded_graph.episode_id}/mentions",
        f"/api/v2/media/{seeded_graph.media_id}/mentions",
    ):
        body = client.get(path).json()
        assert_that(set(body)).is_equal_to({"items", "total", "limit", "offset"})
        assert_that(body["limit"]).is_equal_to(50)
        assert_that(body["offset"]).is_equal_to(0)


def test_datetime_fields_serialize_as_iso8601(
    client: TestClient,
    db_session: Session,
) -> None:
    """Timestamp fields are JSON strings the frontend client can parse."""
    published_at = datetime(2024, 6, 15, 12, 30, tzinfo=UTC)
    podcast = Podcast(name="Timed Show", slug="timed-show")
    db_session.add(podcast)
    db_session.commit()
    db_session.add(
        Episode(
            podcast_id=podcast.id,
            title="Release Day",
            published_at=published_at,
        ),
    )
    db_session.commit()

    episode = client.get("/api/v2/episodes").json()["items"][0]

    assert_that(episode["published_at"]).is_in(
        "2024-06-15T12:30:00",
        "2024-06-15T12:30:00+00:00",
        "2024-06-15T12:30:00Z",
    )
    assert_that(episode["created_at"]).matches(
        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}",
    )


def test_nullable_dto_fields_are_present(
    client: TestClient,
    db_session: Session,
) -> None:
    """Optional schema fields are returned as JSON null, not omitted."""
    podcast = Podcast(name="Sparse", slug="sparse")
    db_session.add(podcast)
    db_session.commit()
    db_session.add(Episode(podcast_id=podcast.id, title="Bare"))
    db_session.add(Media(type=MediaType.ARTICLE, title="Untitled"))
    db_session.commit()
    episode = db_session.execute(select(Episode)).scalar_one()
    media = db_session.execute(select(Media)).scalar_one()
    db_session.add(
        Mention(episode_id=episode.id, media_id=media.id),
    )
    db_session.commit()

    episode_body = client.get(f"/api/v2/episodes/{episode.id}").json()
    assert_that(episode_body["episode_number"]).is_none()
    assert_that(episode_body["published_at"]).is_none()

    media_body = client.get(f"/api/v2/media/{media.id}").json()
    assert_that(media_body["author"]).is_none()
    assert_that(media_body["year"]).is_none()
    assert_that(media_body["description"]).is_none()
    assert_that(media_body["cover_url"]).is_none()

    mention_body = client.get(f"/api/v2/episodes/{episode.id}/mentions").json()[
        "items"
    ][0]
    assert_that(mention_body["timestamp_seconds"]).is_none()
    assert_that(mention_body["context"]).is_none()
    assert_that(mention_body["confidence"]).is_none()


def test_not_found_responses_use_error_envelope(client: TestClient) -> None:
    """404 bodies use the v2 error envelope the OpenAPI client depends on."""
    for path in (
        "/api/v2/podcasts/999",
        "/api/v2/episodes/999",
        "/api/v2/media/999",
        "/api/v2/episodes/999/mentions",
        "/api/v2/media/999/mentions",
    ):
        response = client.get(path)
        assert_that(response.status_code).is_equal_to(404)
        body = response.json()
        assert_that(body).contains_key("title")
        error = body
        assert_that(error["code"]).is_equal_to("not_found")
        assert_that(error["detail"]).is_instance_of(str)
        assert_that(error["request_id"]).is_not_empty()


def test_openapi_surface_is_read_only_get() -> None:
    """The v2 public catalog contract exposes only GET handlers.

    Account endpoints (``/api/v2/auth/*`` and ``/api/v2/me*``) are the
    authenticated write surface and are exempt from this invariant.
    """
    schema = build_openapi_schema()
    catalog_prefix = "/api/v2/"
    account_prefixes = (
        "/api/v2/auth",
        "/api/v2/billing",
        "/api/v2/me",
        "/api/v2/ops",
        "/api/v2/takedowns",
    )

    for path, operations in schema["paths"].items():
        if not path.startswith(catalog_prefix):
            continue
        if path.startswith(account_prefixes):
            continue
        assert_that(set(operations)).is_equal_to({"get"})


def test_openapi_response_schemas_reference_read_dtos() -> None:
    """Catalog routes resolve to Page envelopes or Read DTOs in OpenAPI."""
    schema = build_openapi_schema()
    components = schema["components"]["schemas"]
    detail_paths = {
        "/api/v2/podcasts/{podcast_id}",
        "/api/v2/episodes/{episode_id}",
        "/api/v2/media/{media_id}",
    }

    for path, operations in schema["paths"].items():
        if not path.startswith("/api/v2/") or path.endswith("/status"):
            continue
        if path.startswith(
            (
                "/api/v2/auth",
                "/api/v2/billing",
                "/api/v2/me",
                "/api/v2/ops",
                "/api/v2/takedowns",
            ),
        ):
            continue
        success = operations["get"]["responses"]["200"]["content"]["application/json"][
            "schema"
        ]
        ref = success["$ref"]
        schema_name = ref.rsplit("/", 1)[-1]

        if path in SINGLETON_SCHEMAS_BY_PATH:
            assert_that(schema_name).is_equal_to(SINGLETON_SCHEMAS_BY_PATH[path])
        elif path in detail_paths:
            assert_that(schema_name).is_in(*READ_DTO_SCHEMAS)
        else:
            assert_that(schema_name).starts_with(PAGE_SCHEMA_PREFIX)
            items_ref = components[schema_name]["properties"]["items"]["items"]["$ref"]
            item_schema_name = items_ref.rsplit("/", 1)[-1]
            assert_that(item_schema_name).is_in(*READ_DTO_SCHEMAS)

        assert_that(components).contains_key(schema_name)


def test_live_openapi_json_is_valid_and_stable() -> None:
    """The emitted schema is parseable JSON with the expected catalog metadata."""
    schema = build_openapi_schema()

    assert_that(schema["openapi"]).starts_with("3.")
    assert_that(schema["info"]["title"]).is_equal_to("podex")
    assert_that(schema["paths"]).contains_key("/api/v2/podcasts")
    assert_that(schema["paths"]).contains_key("/api/v2/status")
    assert_that(schema["components"]["schemas"]).contains_key("Page_PodcastRead_")
    assert_that(schema["components"]["schemas"]).contains_key("MediaType")

    payload = json.dumps(schema, sort_keys=True)
    assert_that(json.loads(payload)["paths"]).contains_key("/api/v2/media")


def test_episode_mentions_ordered_by_timestamp(
    client: TestClient,
    db_session: Session,
) -> None:
    """Mentions within an episode are returned in timestamp order."""
    graph = seed_catalog_graph(db_session)
    db_session.add(
        Mention(
            episode_id=graph.episode_id,
            media_id=graph.media_id,
            timestamp_seconds=120,
            context="later",
        ),
    )
    db_session.commit()

    body = client.get(f"/api/v2/episodes/{graph.episode_id}/mentions").json()["items"]
    timestamps = [item["timestamp_seconds"] for item in body]

    assert_that(timestamps).is_equal_to([42, 120])

"""Tests for the ops console API surface."""

from assertpy import assert_that
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from podex.api.deps import get_app_settings
from podex.config import Settings
from podex.models import IngestionRun, IngestionRunStatus, Transcript
from tests.conftest import seed_catalog_graph

_OPS_KEY = "test-ops-key"
_HEADERS = {"X-Ops-Key": _OPS_KEY}


def _enable_ops(client: TestClient) -> None:
    """Configure the ops surface for a test app."""
    app = client.app
    if not isinstance(app, FastAPI):  # pragma: no cover - narrowed
        raise AssertionError
    app.dependency_overrides[get_app_settings] = lambda: Settings(
        ops_api_key=_OPS_KEY,
    )


def test_ops_surface_requires_configuration_and_key(client: TestClient) -> None:
    """Unconfigured ops reports 503; wrong key reports 401."""
    assert_that(client.get("/api/v2/ops/metrics").status_code).is_equal_to(503)

    _enable_ops(client)
    unauthenticated = client.get("/api/v2/ops/metrics")
    assert_that(unauthenticated.status_code).is_equal_to(401)
    wrong = client.get("/api/v2/ops/metrics", headers={"X-Ops-Key": "wrong"})
    assert_that(wrong.status_code).is_equal_to(401)
    ok = client.get("/api/v2/ops/metrics", headers=_HEADERS)
    assert_that(ok.status_code).is_equal_to(200)
    assert_that(ok.json()["review"]["pending_items"]).is_equal_to(0)


def test_ops_podcast_management_round_trip(
    client: TestClient,
    db_session: Session,
) -> None:
    """Podcasts create, list, update, and archive with audit records."""
    _enable_ops(client)

    created = client.post(
        "/api/v2/ops/podcasts",
        json={
            "name": "The Example Show",
            "slug": "example-show",
            "status": "active",
            "sources": {"rss_url": "https://example.com/feed.xml"},
        },
        headers=_HEADERS,
    )
    assert_that(created.status_code).is_equal_to(201)
    podcast_id = created.json()["id"]
    assert_that(created.json()["sources"]["rss_url"]).is_equal_to(
        "https://example.com/feed.xml",
    )

    duplicate = client.post(
        "/api/v2/ops/podcasts",
        json={"name": "Duplicate", "slug": "example-show"},
        headers=_HEADERS,
    )
    assert_that(duplicate.status_code).is_equal_to(409)

    listed = client.get(
        "/api/v2/ops/podcasts",
        params={"status": "active", "source": "rss"},
        headers=_HEADERS,
    )
    assert_that(listed.json()["total"]).is_equal_to(1)

    updated = client.patch(
        f"/api/v2/ops/podcasts/{podcast_id}",
        json={"description": "An updated description."},
        headers=_HEADERS,
    )
    assert_that(updated.status_code).is_equal_to(200)
    assert_that(updated.json()["description"]).is_equal_to(
        "An updated description.",
    )
    assert_that(updated.json()["name"]).is_equal_to("The Example Show")

    archived = client.post(
        f"/api/v2/ops/podcasts/{podcast_id}/archive",
        headers=_HEADERS,
    )
    assert_that(archived.json()["status"]).is_equal_to("paused")
    assert_that(
        client.patch(
            "/api/v2/ops/podcasts/999999",
            json={"name": "Nope"},
            headers=_HEADERS,
        ).status_code,
    ).is_equal_to(404)

    audit = client.get("/api/v2/ops/audit-log", headers=_HEADERS)
    assert_that(audit.json()["total"]).is_equal_to(3)
    assert_that(audit.json()["items"][0]["action"]).is_equal_to("archive_podcast")


def test_ops_pipelines_lists_recent_runs(
    client: TestClient,
    db_session: Session,
) -> None:
    """Pipeline activity returns recent ingestion runs."""
    _enable_ops(client)
    db_session.add(IngestionRun(status=IngestionRunStatus.COMPLETED))
    db_session.commit()

    response = client.get("/api/v2/ops/pipelines", headers=_HEADERS)

    assert_that(response.status_code).is_equal_to(200)
    assert_that(response.json()["runs"]).is_length(1)


def test_ops_retention_preview_apply_and_purge_gate(
    client: TestClient,
    db_session: Session,
) -> None:
    """Retention endpoints preview, apply, and refuse unsafe purges."""
    _enable_ops(client)
    graph = seed_catalog_graph(db_session)
    transcript = Transcript(
        episode_id=graph.episode_id,
        provider="podscripts",
        raw_text="raw text",
    )
    db_session.add(transcript)
    db_session.commit()

    listed = client.get("/api/v2/ops/retention", headers=_HEADERS)
    assert_that(listed.json()).is_length(1)

    preview = client.get(
        f"/api/v2/ops/retention/{transcript.id}/preview",
        headers=_HEADERS,
    )
    assert_that(preview.status_code).is_equal_to(200)
    assert_that(preview.json()["derivative_coverage_ready"]).is_false()
    assert_that(
        client.get(
            "/api/v2/ops/retention/999999/preview", headers=_HEADERS
        ).status_code,
    ).is_equal_to(404)

    applied = client.post(
        f"/api/v2/ops/retention/{transcript.id}/apply",
        headers=_HEADERS,
    )
    assert_that(applied.status_code).is_equal_to(200)

    refused = client.post(
        f"/api/v2/ops/retention/{transcript.id}/purge",
        headers=_HEADERS,
    )
    assert_that(refused.status_code).is_equal_to(409)

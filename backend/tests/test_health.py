"""Tests for the health and v2 status endpoints."""

from typing import NoReturn

from assertpy import assert_that
from fastapi.testclient import TestClient

from podex.database import get_db
from podex.main import create_app


def test_health_returns_ok(client: TestClient) -> None:
    """The liveness probe reports a healthy status."""
    response = client.get("/health")

    assert_that(response.status_code).is_equal_to(200)
    assert_that(response.json()).is_equal_to({"status": "ok"})


def test_v2_status_returns_ok(client: TestClient) -> None:
    """The v2 surface reports that it is reachable."""
    response = client.get("/api/v2/status")

    assert_that(response.status_code).is_equal_to(200)
    assert_that(response.json()).is_equal_to(
        {"status": "ok", "api": "v2", "workos_enabled": False}
    )


def test_health_is_live_when_catalog_database_is_unavailable() -> None:
    """Liveness stays independent of database-backed catalog readiness."""
    application = create_app()

    def unavailable_database() -> NoReturn:
        """Model a database outage before a session can be opened."""
        raise RuntimeError("Database unavailable")

    application.dependency_overrides[get_db] = unavailable_database
    with TestClient(application, raise_server_exceptions=False) as client:
        assert_that(client.get("/health").status_code).is_equal_to(200)
        assert_that(client.get("/api/v2/podcasts").status_code).is_equal_to(500)

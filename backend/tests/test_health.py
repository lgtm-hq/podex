"""Tests for the health and v2 status endpoints."""

from assertpy import assert_that
from fastapi.testclient import TestClient


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

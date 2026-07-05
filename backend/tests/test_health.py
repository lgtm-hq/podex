"""Tests for the health and v2 status endpoints."""

from fastapi.testclient import TestClient


def test_health_returns_ok(client: TestClient) -> None:
    """The liveness probe reports a healthy status."""
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_v2_status_returns_ok(client: TestClient) -> None:
    """The v2 surface reports that it is reachable."""
    response = client.get("/api/v2/status")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "api": "v2"}

"""Shared pytest fixtures."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from podex.main import create_app


@pytest.fixture
def client() -> Iterator[TestClient]:
    """Return a test client bound to a fresh application instance."""
    with TestClient(create_app()) as test_client:
        yield test_client

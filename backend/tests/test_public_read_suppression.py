"""Every public read of mentions applies the suppression predicate.

The test calls each public ``GET`` route in the OpenAPI schema and records the
SQL it runs. A statement that reads the ``mentions`` table without the
``content_suppressions`` predicate fails the test, so a new public route or
query cannot forget it. Routes are discovered from the schema, so a new public
route is covered without editing this file; a route whose path parameters this
file cannot fill fails until it is taught them.
"""

import re
from collections.abc import Iterator
from typing import Any

import pytest
from assertpy import assert_that
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event
from sqlalchemy.orm import Session

from tests.conftest import SeededGraph, seed_catalog_graph

_PRIVATE_PREFIXES = (
    "/api/v2/ops",
    "/api/v2/me",
    "/api/v2/auth",
    "/api/v2/billing",
)
_READS_MENTIONS = re.compile(r"\b(?:FROM|JOIN)\s+mentions\b", re.IGNORECASE)
_SUPPRESSION_PREDICATE = "content_suppressions"


def _public_get_paths(client: TestClient) -> list[str]:
    """Return the schema's public ``GET`` paths."""
    app = client.app
    if not isinstance(app, FastAPI):
        raise TypeError(type(app))
    paths: dict[str, dict[str, Any]] = app.openapi()["paths"]
    return sorted(
        path
        for path, operations in paths.items()
        if "get" in operations and not _is_private(path)
    )


def _is_private(path: str) -> bool:
    """Return whether a path belongs to an authenticated or operator area."""
    return any(
        path == prefix or path.startswith(f"{prefix}/") for prefix in _PRIVATE_PREFIXES
    )


def _fill(path: str, graph: SeededGraph) -> str:
    """Substitute seeded ids for a path's parameters."""
    ids = {
        "podcast_id": graph.podcast_id,
        "episode_id": graph.episode_id,
        "media_id": graph.media_id,
    }
    filled = path
    for name, value in ids.items():
        filled = filled.replace(f"{{{name}}}", str(value))
    if "{" in filled:
        msg = f"teach this test the path parameters of {path}"
        raise AssertionError(msg)
    return filled


@pytest.fixture(name="statements")
def statements_fixture(db_session: Session) -> Iterator[list[str]]:
    """Record every SQL statement the test database runs."""
    engine = db_session.get_bind()
    if not isinstance(engine, Engine):
        raise TypeError(type(engine))
    recorded: list[str] = []

    def record(*args: Any) -> None:
        recorded.append(str(args[2]))

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield recorded
    finally:
        event.remove(engine, "before_cursor_execute", record)


def test_public_mention_reads_apply_the_suppression_predicate(
    client: TestClient,
    db_session: Session,
    statements: list[str],
) -> None:
    """No public route reads ``mentions`` without excluding suppressed rows."""
    graph = seed_catalog_graph(db_session)
    unguarded: dict[str, list[str]] = {}
    mention_readers: list[str] = []

    for path in _public_get_paths(client):
        statements.clear()
        response = client.get(_fill(path, graph))
        assert_that(response.status_code).described_as(path).is_equal_to(200)
        reads = [sql for sql in statements if _READS_MENTIONS.search(sql)]
        if reads:
            mention_readers.append(path)
        missing = [sql for sql in reads if _SUPPRESSION_PREDICATE not in sql]
        if missing:
            unguarded[path] = missing

    assert_that(unguarded).is_empty()
    assert_that(mention_readers).contains(
        "/api/v2/episodes/{episode_id}/mentions",
        "/api/v2/media/{media_id}/mentions",
        "/api/v2/stats",
    )

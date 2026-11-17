"""Every public and account read of mentions applies the suppression predicate.

The first test calls each public ``GET`` route in the OpenAPI schema, the
second every signed-in account route plus alert evaluation and the digest
email, and both record the SQL they run. A statement that reads the
``mentions`` table without the ``content_suppressions`` predicate fails the
test, so a new route or query cannot forget it. Routes are discovered from
the schema, so a new route is covered without editing this file; a route
whose path parameters this file cannot fill fails until it is taught them.
"""

import re
from collections.abc import Iterator
from typing import Any

import pytest
from assertpy import assert_that
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from podex.models import Mention, Transcript
from tests.conftest import SeededGraph, enable_ops, seed_catalog_graph
from tests.takedown_support import recorded_statements, sign_in_reader_with_alert

_PRIVATE_PREFIXES = (
    "/api/v2/ops",
    "/api/v2/me",
    "/api/v2/auth",
    "/api/v2/billing",
)
# Any reference to the ``mentions`` table: ``FROM mentions``, ``JOIN mentions``,
# ``FROM media, mentions`` or a ``mentions.`` column. Bound values never appear
# in the statement text, and ``mention_candidates`` or ``ix_mentions_...`` do
# not match.
_READS_MENTIONS = re.compile(r"\bmentions\b", re.IGNORECASE)
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
    with recorded_statements(engine) as recorded:
        yield recorded


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


def _unguarded_reads(statements: list[str]) -> list[str]:
    """Return statements that read ``mentions`` without the predicate."""
    return [
        sql
        for sql in statements
        if _READS_MENTIONS.search(sql) and _SUPPRESSION_PREDICATE not in sql
    ]


def test_account_reads_and_notifications_apply_the_suppression_predicate(
    client: TestClient,
    db_session: Session,
    statements: list[str],
) -> None:
    """Signed-in reads, alert evaluation, digest email and export are filtered.

    Every ``GET`` under ``/api/v2/me`` is called, plus the two operations that
    count mentions and render the digest email. The scheduler drives the same
    alert and digest services.
    """
    graph = seed_catalog_graph(db_session)
    reader = sign_in_reader_with_alert(client, media_id=graph.media_id)
    headers = reader.headers
    client.put(f"/api/v2/me/follows/{graph.podcast_id}", headers=headers)
    app = client.app
    if not isinstance(app, FastAPI):
        raise TypeError(type(app))
    db_session.add(Mention(episode_id=graph.episode_id, media_id=graph.media_id))
    db_session.commit()
    paths: dict[str, dict[str, Any]] = app.openapi()["paths"]
    calls = [
        ("get", path.replace("{rule_id}", str(reader.rule_id)))
        for path, operations in paths.items()
        if path.startswith("/api/v2/me") and "get" in operations
    ]
    calls += [
        ("post", "/api/v2/me/alerts/evaluate"),
        ("post", "/api/v2/me/digests/send"),
    ]
    unguarded: dict[str, list[str]] = {}
    mention_readers: list[str] = []

    for method, path in calls:
        statements.clear()
        response = client.request(method, _fill(path, graph), headers=headers)
        assert_that(response.status_code).described_as(path).is_less_than(400)
        if any(_READS_MENTIONS.search(sql) for sql in statements):
            mention_readers.append(path)
        if _unguarded_reads(statements):
            unguarded[path] = _unguarded_reads(statements)

    assert_that(unguarded).is_empty()
    assert_that(mention_readers).contains(
        "/api/v2/me/alerts/evaluate",
        "/api/v2/me/digests/send",
    )


def test_ops_reads_apply_the_suppression_predicate(
    client: TestClient,
    db_session: Session,
    statements: list[str],
) -> None:
    """Operator views count and list only what public reads show.

    Every ``GET`` under ``/api/v2/ops`` and the root ``/metrics`` is called
    with the ops key; a statement that reads ``mentions`` must carry the
    predicate, so ops counts cannot disagree with public counts.
    """
    graph = seed_catalog_graph(db_session)
    transcript = Transcript(
        episode_id=graph.episode_id,
        provider="podscripts",
        raw_text="one two three",
    )
    db_session.add(transcript)
    db_session.commit()
    enable_ops(client, api_key="test-ops-key")
    app = client.app
    if not isinstance(app, FastAPI):
        raise TypeError(type(app))
    paths: dict[str, dict[str, Any]] = app.openapi()["paths"]
    calls = [
        path.replace("{transcript_id}", str(transcript.id))
        for path, operations in paths.items()
        if "get" in operations
        and (path == "/metrics" or path.startswith("/api/v2/ops/"))
    ]
    unguarded: dict[str, list[str]] = {}
    mention_readers: list[str] = []

    for path in calls:
        statements.clear()
        response = client.get(_fill(path, graph), headers={"X-Ops-Key": "test-ops-key"})
        assert_that(response.status_code).described_as(path).is_equal_to(200)
        if any(_READS_MENTIONS.search(sql) for sql in statements):
            mention_readers.append(path)
        if _unguarded_reads(statements):
            unguarded[path] = _unguarded_reads(statements)

    assert_that(unguarded).is_empty()
    assert_that(mention_readers).contains(
        "/api/v2/ops/podcasts",
        "/api/v2/ops/podcasts/{podcast_id}",
    )

"""Ops podcast update errors on real PostgreSQL.

SQLite ignores column lengths and stores NUL, so only PostgreSQL shows that an
overlong or NUL value would fail at flush. These tests check that such values
are rejected by request validation before any statement reaches the database,
and that a taken slug answers 409 after PostgreSQL's unique violation. They run
in the PostgreSQL CI lane and are skipped elsewhere.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from assertpy import assert_that
from fastapi.testclient import TestClient
from sqlalchemy import TextClause, event, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DataError
from sqlalchemy.orm import Session

from podex.api.deps import get_app_settings
from podex.config import RateLimitSettings, Settings
from podex.database import get_db
from podex.main import create_app
from podex.models import Podcast
from tests.postgres.conftest import PostgresDatabase

pytestmark = pytest.mark.postgres

_OPS_KEY = "ops-podcasts-postgres-test-key"
_HEADERS = {"X-Ops-Key": _OPS_KEY}
_DIRECT_WRITES: dict[str, TextClause] = {
    "description": text("UPDATE podcasts SET description = :value WHERE id = :id"),
    "cover_url": text("UPDATE podcasts SET cover_url = :value WHERE id = :id"),
}


def _add_podcast(postgres: PostgresDatabase, *, slug: str) -> int:
    """Commit a podcast with ``slug`` and return its id."""
    with postgres.sessions() as db:
        podcast = Podcast(name=f"Show {slug}", slug=slug, description="Original.")
        db.add(podcast)
        db.commit()
        podcast_id: int = podcast.id
    return podcast_id


def _stored(postgres: PostgresDatabase) -> tuple[list[dict[str, Any]], int]:
    """Read every podcast row and the number of audit log entries."""
    with postgres.engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT id, name, slug, description, cover_url"
                " FROM podcasts ORDER BY id",
            ),
        ).mappings()
        podcasts = [dict(row) for row in rows]
        audits: int = connection.execute(
            text("SELECT count(*) FROM audit_logs"),
        ).scalar_one()
    return podcasts, audits


def _ops_client(postgres: PostgresDatabase) -> TestClient:
    """Return a client whose ops routes use the test's PostgreSQL database."""
    app = create_app(Settings(rate_limit=RateLimitSettings(enabled=False)))

    def override_get_db() -> Iterator[Session]:
        """Yield a session on the test's PostgreSQL database."""
        with postgres.sessions() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_app_settings] = lambda: Settings(
        ops_api_key=_OPS_KEY,
    )
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("description", "x" * 2001),
        ("cover_url", "x" * 501),
        ("description", "nul \x00 byte"),
    ],
    ids=["description-2001", "cover-url-501", "description-nul"],
)
def test_update_rejects_unstorable_text_before_it_reaches_postgres(
    postgres: PostgresDatabase,
    field: str,
    value: str,
) -> None:
    """The value PostgreSQL would reject answers 422 with no statement sent."""
    podcast_id = _add_podcast(postgres, slug="pg-show")
    # Control: written directly, PostgreSQL itself rejects the value.
    with postgres.sessions() as db:
        with pytest.raises(DataError):
            db.execute(_DIRECT_WRITES[field], {"value": value, "id": podcast_id})
        db.rollback()
    before = _stored(postgres)
    statements: list[str] = []

    def record(
        _connection: Connection,
        _cursor: object,
        statement: str,
        *_: object,
    ) -> None:
        """Record each statement the application sends."""
        statements.append(statement)

    event.listen(postgres.engine, "before_cursor_execute", record)
    try:
        with _ops_client(postgres) as client:
            response = client.patch(
                f"/api/v2/ops/podcasts/{podcast_id}",
                json={field: value},
                headers=_HEADERS,
            )
    finally:
        event.remove(postgres.engine, "before_cursor_execute", record)

    assert_that(response.status_code).is_equal_to(422)
    assert_that(response.json()["errors"][0]["loc"]).is_equal_to(["body", field])
    assert_that(statements).is_empty()
    assert_that(_stored(postgres)).is_equal_to(before)


def test_update_to_a_taken_slug_answers_409_on_postgres(
    postgres: PostgresDatabase,
) -> None:
    """PostgreSQL's unique violation on update is answered as a slug conflict."""
    podcast_id = _add_podcast(postgres, slug="pg-first")
    _add_podcast(postgres, slug="pg-second")
    podcasts_before, _ = _stored(postgres)

    with _ops_client(postgres) as client:
        conflict = client.patch(
            f"/api/v2/ops/podcasts/{podcast_id}",
            json={"slug": "pg-second", "name": "Renamed"},
            headers=_HEADERS,
        )
        own_slug = client.patch(
            f"/api/v2/ops/podcasts/{podcast_id}",
            json={"slug": "pg-first"},
            headers=_HEADERS,
        )

    assert_that(conflict.status_code).is_equal_to(409)
    assert_that(conflict.json()["detail"]).is_equal_to("Podcast slug already exists")
    assert_that(own_slug.status_code).is_equal_to(200)
    # Only the own-slug update was applied and audited; it changed nothing.
    assert_that(_stored(postgres)).is_equal_to((podcasts_before, 1))

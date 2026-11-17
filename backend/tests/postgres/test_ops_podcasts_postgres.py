"""Ops podcast create and update errors on real PostgreSQL.

SQLite ignores column lengths and stores NUL, so only PostgreSQL shows that an
overlong or NUL value would fail (or be truncated) at flush. These tests check
that such values are rejected by request validation before any statement
reaches the database, that text exactly at each limit is stored unchanged (a
slug at its limit is also recorded whole in the audit log), and that only the
slug's unique violation answers 409. They run in the PostgreSQL CI lane and are
skipped elsewhere.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from assertpy import assert_that
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import Engine, TextClause, event, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from podex.api.deps import get_app_settings
from podex.config import RateLimitSettings, Settings
from podex.database import get_db
from podex.main import create_app
from podex.models import Podcast
from podex.schemas.ops import OpsPodcastSourcesInput, OpsPodcastUpdateRequest
from podex.services.ops_podcast_commands import SLUG_UNIQUE_INDEX, is_slug_conflict
from tests.postgres.conftest import PostgresDatabase

pytestmark = pytest.mark.postgres

_OPS_KEY = "ops-podcasts-postgres-test-key"
_HEADERS = {"X-Ops-Key": _OPS_KEY}
_PODCASTS = "/api/v2/ops/podcasts"
_DIRECT_WRITES: dict[str, TextClause] = {
    "description": text("UPDATE podcasts SET description = :value WHERE id = :id"),
    "cover_url": text("UPDATE podcasts SET cover_url = :value WHERE id = :id"),
}
_ROW_SQL = text("SELECT * FROM podcasts WHERE id = :id")


def _limited_fields() -> tuple[tuple[tuple[str, ...], int], ...]:
    """Return each free-text request field as a JSON path with its column length.

    ``status`` and ``discovery_source`` hold enum values, so no free text of
    their full length is valid.
    """
    profile: dict[str, Any] = OpsPodcastUpdateRequest.model_json_schema()["properties"]
    sources: dict[str, Any] = OpsPodcastSourcesInput.model_json_schema()["properties"]
    paths: list[tuple[str, ...]] = [
        (name,)
        for name in profile
        if name not in {"status", "discovery_source", "sources"}
    ]
    paths += [("sources", name) for name in sources]
    columns = Podcast.__table__.columns
    return tuple((path, int(columns[path[-1]].type.length)) for path in paths)


_LIMITED_FIELDS = _limited_fields()
_LIMITED_FIELD_IDS = [".".join(path) for path, _ in _LIMITED_FIELDS]
_ROUTES = ("POST", "PATCH")
# Two-, three- and four-byte UTF-8 characters: limits count characters.
_MULTIBYTE = "é漢🎧ß"
_SLUG_LIMIT = 255
_AUDIT_SQL = text(
    "SELECT action, resource_id, resource_identifier FROM audit_logs ORDER BY id",
)


def _multibyte(length: int) -> str:
    """Return ``length`` characters of mixed multi-byte text."""
    return (_MULTIBYTE * length)[:length]


def _add_podcast(postgres: PostgresDatabase, *, slug: str) -> int:
    """Commit a podcast with ``slug`` and return its id."""
    with postgres.sessions() as db:
        podcast = Podcast(
            name=f"Show {slug}",
            slug=slug,
            description="Original.",
            rss_url=f"https://example.com/{slug}.xml",
        )
        db.add(podcast)
        db.commit()
        podcast_id: int = podcast.id
    return podcast_id


def _stored(postgres: PostgresDatabase) -> tuple[list[dict[str, Any]], int]:
    """Read every podcast row and the number of audit log entries."""
    with postgres.engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT id, name, slug, description, cover_url, rss_url,"
                " spotify_id, apple_id, youtube_channel_id, podscripts_slug,"
                " discovery_source FROM podcasts ORDER BY id",
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


@contextmanager
def _recorded_statements(engine: Engine) -> Iterator[list[str]]:
    """Collect every statement sent through ``engine`` inside the block."""
    statements: list[str] = []

    def record(
        _connection: Connection,
        _cursor: object,
        statement: str,
        *_: object,
    ) -> None:
        """Record each statement the application sends."""
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


def _body_with(path: tuple[str, ...], value: object) -> dict[str, Any]:
    """Return a body setting the field at ``path`` to ``value``."""
    if len(path) == 1:
        return {path[0]: value}
    return {path[0]: {path[1]: value}}


def _send(
    postgres: PostgresDatabase,
    *,
    method: str,
    podcast_id: int,
    body: dict[str, Any],
) -> Response:
    """Create a podcast (``POST``) or update ``podcast_id`` (``PATCH``)."""
    with _ops_client(postgres) as client:
        if method == "POST":
            response: Response = client.post(
                _PODCASTS,
                json={"name": "PG New Show", "slug": "pg-new-show", **body},
                headers=_HEADERS,
            )
        else:
            response = client.patch(
                f"{_PODCASTS}/{podcast_id}",
                json=body,
                headers=_HEADERS,
            )
    return response


def _assert_rejected_unsent(
    postgres: PostgresDatabase,
    *,
    method: str,
    body: dict[str, Any],
    loc: list[str],
) -> None:
    """Assert ``body`` answers 422 at ``loc`` without any statement or change."""
    podcast_id = _add_podcast(postgres, slug="pg-show")
    before = _stored(postgres)

    with _recorded_statements(postgres.engine) as statements:
        response = _send(postgres, method=method, podcast_id=podcast_id, body=body)

    assert_that(response.status_code).is_equal_to(422)
    assert_that(response.json()["errors"][0]["loc"]).is_equal_to(["body", *loc])
    assert_that(statements).is_empty()
    assert_that(_stored(postgres)).is_equal_to(before)


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
    podcast_id = _add_podcast(postgres, slug="pg-control")
    # Control: written directly, PostgreSQL itself rejects the value.
    with postgres.sessions() as db:
        with pytest.raises(DataError):
            db.execute(_DIRECT_WRITES[field], {"value": value, "id": podcast_id})
        db.rollback()

    _assert_rejected_unsent(
        postgres,
        method="PATCH",
        body={field: value},
        loc=[field],
    )


def test_update_rejects_trailing_spaces_postgres_would_truncate(
    postgres: PostgresDatabase,
) -> None:
    """Text over the limit only by trailing spaces is rejected, not truncated."""
    value = "x" * 2000 + "   "
    podcast_id = _add_podcast(postgres, slug="pg-control")
    # Control: PostgreSQL silently drops the excess spaces on a direct write.
    with postgres.sessions() as db:
        db.execute(_DIRECT_WRITES["description"], {"value": value, "id": podcast_id})
        stored = db.execute(_ROW_SQL, {"id": podcast_id}).mappings().one()
        db.rollback()
    assert_that(stored["description"]).is_equal_to("x" * 2000)

    _assert_rejected_unsent(
        postgres,
        method="PATCH",
        body={"description": value},
        loc=["description"],
    )


@pytest.mark.parametrize("method", _ROUTES)
@pytest.mark.parametrize(("path", "limit"), _LIMITED_FIELDS, ids=_LIMITED_FIELD_IDS)
def test_routes_store_multibyte_text_at_the_limit_and_reject_one_more(
    postgres: PostgresDatabase,
    method: str,
    path: tuple[str, ...],
    limit: int,
) -> None:
    """Limits count characters as PostgreSQL does, whatever their byte length."""
    at_limit = _multibyte(limit)
    podcast_id = _add_podcast(postgres, slug="pg-show")

    accepted = _send(
        postgres,
        method=method,
        podcast_id=podcast_id,
        body=_body_with(path, at_limit),
    )

    assert_that(accepted.status_code).is_equal_to(201 if method == "POST" else 200)
    with postgres.engine.connect() as connection:
        row = connection.execute(_ROW_SQL, {"id": accepted.json()["id"]}).mappings()
        assert_that(row.one()[path[-1]]).is_equal_to(at_limit)

    over_limit = _multibyte(limit + 1)
    before = _stored(postgres)
    with _recorded_statements(postgres.engine) as statements:
        rejected = _send(
            postgres,
            method=method,
            podcast_id=podcast_id,
            body=_body_with(path, over_limit),
        )

    assert_that(rejected.status_code).is_equal_to(422)
    assert_that(rejected.json()["errors"][0]["loc"]).is_equal_to(["body", *path])
    assert_that(statements).is_empty()
    assert_that(_stored(postgres)).is_equal_to(before)


@pytest.mark.parametrize("method", _ROUTES)
def test_routes_reject_a_discovery_source_over_its_limit(
    postgres: PostgresDatabase,
    method: str,
) -> None:
    """A discovery source longer than its column answers 422 unsent."""
    _assert_rejected_unsent(
        postgres,
        method=method,
        body={"discovery_source": "x" * 51},
        loc=["discovery_source"],
    )


@pytest.mark.parametrize("method", _ROUTES)
@pytest.mark.parametrize(
    "path",
    [("name",), ("cover_url",), ("sources", "rss_url"), ("sources", "podscripts_slug")],
    ids=["name", "cover_url", "sources.rss_url", "sources.podscripts_slug"],
)
def test_routes_reject_nul_in_any_text_field(
    postgres: PostgresDatabase,
    method: str,
    path: tuple[str, ...],
) -> None:
    """NUL, which PostgreSQL cannot store, answers 422 unsent in every field."""
    _assert_rejected_unsent(
        postgres,
        method=method,
        body=_body_with(path, "nul \x00 byte"),
        loc=list(path),
    )


def test_slug_unique_violation_is_reported_by_sqlstate_and_index_name(
    postgres: PostgresDatabase,
) -> None:
    """The driver names the migration's slug index; renaming it fails here."""
    podcast_id = _add_podcast(postgres, slug="pg-first")
    _add_podcast(postgres, slug="pg-second")

    with postgres.sessions() as db:
        podcast = db.get(Podcast, podcast_id)
        if podcast is None:
            raise AssertionError("expected the podcast to exist")
        podcast.slug = "pg-second"
        with pytest.raises(IntegrityError) as raised:
            db.flush()
        db.rollback()

    driver_error: Any = raised.value.orig
    assert_that(driver_error.sqlstate).is_equal_to("23505")
    assert_that(driver_error.diag.constraint_name).is_equal_to(SLUG_UNIQUE_INDEX)
    assert_that(SLUG_UNIQUE_INDEX).is_equal_to("ix_podcasts_slug")
    assert_that(is_slug_conflict(raised.value)).is_true()


def test_update_to_a_taken_slug_answers_409_on_postgres(
    postgres: PostgresDatabase,
) -> None:
    """PostgreSQL's slug unique violation on update is answered as a conflict."""
    podcast_id = _add_podcast(postgres, slug="pg-first")
    _add_podcast(postgres, slug="pg-second")
    podcasts_before, _ = _stored(postgres)

    with _ops_client(postgres) as client:
        conflict = client.patch(
            f"{_PODCASTS}/{podcast_id}",
            json={"slug": "pg-second", "name": "Renamed"},
            headers=_HEADERS,
        )
        own_slug = client.patch(
            f"{_PODCASTS}/{podcast_id}",
            json={"slug": "pg-first"},
            headers=_HEADERS,
        )

    assert_that(conflict.status_code).is_equal_to(409)
    assert_that(conflict.json()["detail"]).is_equal_to("Podcast slug already exists")
    assert_that(own_slug.status_code).is_equal_to(200)
    # Only the own-slug update was applied and audited; it changed nothing.
    assert_that(_stored(postgres)).is_equal_to((podcasts_before, 1))


@pytest.mark.parametrize(
    ("constraint", "body"),
    [
        (
            "ALTER TABLE podcasts ADD CONSTRAINT ck_test_name CHECK (name <> 'boom')",
            {"name": "boom", "slug": "pg-second"},
        ),
        (
            "CREATE UNIQUE INDEX uq_test_podcasts_rss_url ON podcasts (rss_url)",
            {"sources": {"rss_url": "https://example.com/pg-second.xml"}},
        ),
    ],
    ids=["check-with-taken-slug", "other-unique-index"],
)
def test_update_reports_other_integrity_errors_as_500_on_postgres(
    postgres: PostgresDatabase,
    constraint: str,
    body: dict[str, Any],
) -> None:
    """A check or another unique index is never answered as a slug conflict."""
    podcast_id = _add_podcast(postgres, slug="pg-first")
    _add_podcast(postgres, slug="pg-second")
    with postgres.engine.begin() as connection:
        connection.execute(text(constraint))
    before = _stored(postgres)

    with _ops_client(postgres) as client:
        response = client.patch(
            f"{_PODCASTS}/{podcast_id}",
            json=body,
            headers=_HEADERS,
        )

    assert_that(response.status_code).is_equal_to(500)
    assert_that(response.json()["detail"]).is_equal_to("Internal server error.")
    assert_that(_stored(postgres)).is_equal_to(before)


@pytest.mark.parametrize(
    ("created_slug", "updated_slug"),
    [
        ("c" * _SLUG_LIMIT, "u" * _SLUG_LIMIT),
        (_multibyte(_SLUG_LIMIT), "漢" * _SLUG_LIMIT),
    ],
    ids=["ascii", "multibyte"],
)
def test_routes_audit_a_slug_at_its_limit_on_postgres(
    postgres: PostgresDatabase,
    created_slug: str,
    updated_slug: str,
) -> None:
    """Create, update and archive record a slug at its limit in full."""
    assert_that(Podcast.__table__.columns["slug"].type.length).is_equal_to(
        _SLUG_LIMIT,
    )

    with _ops_client(postgres) as client:
        created = client.post(
            _PODCASTS,
            json={"name": "PG Long Slug", "slug": created_slug},
            headers=_HEADERS,
        )
        podcast_id = created.json()["id"]
        updated = client.patch(
            f"{_PODCASTS}/{podcast_id}",
            json={"slug": updated_slug},
            headers=_HEADERS,
        )
        archived = client.post(
            f"{_PODCASTS}/{podcast_id}/archive",
            headers=_HEADERS,
        )

    assert_that(
        [created.status_code, updated.status_code, archived.status_code],
    ).is_equal_to([201, 200, 200])
    with postgres.engine.connect() as connection:
        audits = [tuple(row) for row in connection.execute(_AUDIT_SQL)]
        row = connection.execute(_ROW_SQL, {"id": podcast_id}).mappings().one()
    assert_that(row["slug"]).is_equal_to(updated_slug)
    assert_that(audits).is_equal_to(
        [
            ("create_podcast", podcast_id, created_slug),
            ("update_podcast", podcast_id, updated_slug),
            ("archive_podcast", podcast_id, updated_slug),
        ],
    )

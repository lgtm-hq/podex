"""Error answers of the ops podcast create and update routes.

Covers the slug conflict on update, the request length limits, text the
database cannot store, and which service errors are reported as 422.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, NoReturn

import pytest
from assertpy import assert_that
from fastapi.testclient import TestClient
from httpx2 import Response
from pydantic import BaseModel
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from podex.api.deps import get_app_settings
from podex.config import RateLimitSettings, Settings
from podex.database import enable_sqlite_foreign_keys, get_db
from podex.main import create_app
from podex.models import AuditLog, Base, Episode, Podcast, PodcastStatus
from podex.schemas.ops import (
    OpsPodcastCreateRequest,
    OpsPodcastSourcesInput,
    OpsPodcastUpdateRequest,
)
from podex.services.ops_podcast_commands import (
    DISCOVERY_SOURCE_ECHO_LIMIT,
    CreateOpsPodcastInputData,
    OpsPodcastValidationError,
    UpdateOpsPodcastInputData,
    create_ops_podcast,
    update_ops_podcast,
)
from tests.conftest import enable_ops

_OPS_KEY = "ops-podcast-errors-test-key"
_HEADERS = {"X-Ops-Key": _OPS_KEY}
_PODCASTS = "/api/v2/ops/podcasts"
_ROWS_SQL = text(
    "SELECT name, slug, status, description, discovery_source, cover_url, "
    "rss_url, spotify_id, apple_id, youtube_channel_id, podscripts_slug "
    "FROM podcasts ORDER BY id",
)
# Text fields of each request body, as JSON paths, with their column lengths.
_TEXT_FIELDS: tuple[tuple[tuple[str, ...], int], ...] = (
    (("name",), 255),
    (("slug",), 255),
    (("description",), 2000),
    (("cover_url",), 500),
    (("discovery_source",), 50),
    (("sources", "rss_url"), 500),
    (("sources", "spotify_id"), 50),
    (("sources", "apple_id"), 50),
    (("sources", "youtube_channel_id"), 30),
    (("sources", "podscripts_slug"), 100),
)
_TEXT_FIELD_IDS = [".".join(path) for path, _ in _TEXT_FIELDS]
_TEXT_PATHS = [path for path, _ in _TEXT_FIELDS]


def _add_podcast(db: Session, *, slug: str, **fields: Any) -> int:
    """Commit a podcast with ``slug`` and return its id."""
    podcast = Podcast(name=f"Show {slug}", slug=slug, **fields)
    db.add(podcast)
    db.commit()
    podcast_id: int = podcast.id
    return podcast_id


def _rows(db: Session) -> list[dict[str, Any]]:
    """Read every podcast row with SQL, bypassing the session's identity map."""
    result = db.execute(_ROWS_SQL)
    return [dict(row) for row in result.mappings()]


def _audit_count(db: Session) -> int:
    """Return the number of audit log entries."""
    count: int | None = db.scalar(select(func.count()).select_from(AuditLog))
    return count or 0


def _error_body(response: Response) -> dict[str, Any]:
    """Return an error body without its per-request correlation id."""
    body: dict[str, Any] = response.json()
    return {key: value for key, value in body.items() if key != "request_id"}


def _body_with(path: tuple[str, ...], value: object) -> dict[str, Any]:
    """Return an update body setting the field at ``path`` to ``value``."""
    if len(path) == 1:
        return {path[0]: value}
    return {path[0]: {path[1]: value}}


def _send_escaped(
    client: TestClient,
    method: str,
    path: str,
    body: dict[str, Any],
) -> Response:
    """Send ``body`` as ASCII JSON, escaping text a UTF-8 encoder rejects."""
    response: Response = client.request(
        method,
        path,
        content=json.dumps(body, ensure_ascii=True),
        headers={**_HEADERS, "Content-Type": "application/json"},
    )
    return response


def _create_body_with(path: tuple[str, ...], value: object) -> dict[str, Any]:
    """Return a valid create body with the field at ``path`` set to ``value``."""
    return {"name": "New Show", "slug": "new-show", **_body_with(path, value)}


# Slug conflicts on update


def test_api_update_to_another_podcasts_slug_answers_409_like_create(
    client: TestClient,
    db_session: Session,
) -> None:
    """Taking another podcast's slug answers create's 409 and changes nothing."""
    enable_ops(client, api_key=_OPS_KEY)
    podcast_id = _add_podcast(db_session, slug="first-show")
    _add_podcast(db_session, slug="second-show")
    before = _rows(db_session)

    updated = client.patch(
        f"{_PODCASTS}/{podcast_id}",
        json={"slug": "second-show", "name": "Renamed Show"},
        headers=_HEADERS,
    )
    created = client.post(
        _PODCASTS,
        json={"name": "Another Show", "slug": "second-show"},
        headers=_HEADERS,
    )

    assert_that(updated.status_code).is_equal_to(409)
    assert_that(_error_body(updated)).is_equal_to(_error_body(created))
    assert_that(updated.json()["detail"]).is_equal_to("Podcast slug already exists")
    db_session.rollback()
    assert_that(_rows(db_session)).is_equal_to(before)
    assert_that(_audit_count(db_session)).is_zero()


def test_api_update_keeps_its_own_slug(
    client: TestClient,
    db_session: Session,
) -> None:
    """Submitting a podcast's current slug is not a conflict."""
    enable_ops(client, api_key=_OPS_KEY)
    podcast_id = _add_podcast(db_session, slug="first-show")
    _add_podcast(db_session, slug="second-show")

    response = client.patch(
        f"{_PODCASTS}/{podcast_id}",
        json={"slug": "first-show", "name": "Renamed Show"},
        headers=_HEADERS,
    )

    assert_that(response.status_code).is_equal_to(200)
    assert_that(response.json()["slug"]).is_equal_to("first-show")
    assert_that(response.json()["name"]).is_equal_to("Renamed Show")
    assert_that(_audit_count(db_session)).is_equal_to(1)


def _update_then_violate_foreign_key(
    *,
    db: Session,
    podcast_id: int,
    payload: UpdateOpsPodcastInputData,
) -> NoReturn:
    """Change the podcast, then fail on a constraint unrelated to the slug."""
    podcast = db.get(Podcast, podcast_id)
    if podcast is None:
        raise AssertionError("expected the podcast to exist")
    podcast.name = payload.name or podcast.name
    db.add(Episode(podcast_id=999_999, title="Orphan"))
    db.flush()
    raise AssertionError("expected the flush to fail")


@pytest.mark.parametrize(
    "body",
    [
        {"name": "Renamed Show"},
        {"name": "Renamed Show", "slug": "first-show"},
        {"name": "Renamed Show", "slug": "unused-slug"},
        {"name": "Renamed Show", "slug": "second-show"},
        {"name": "Renamed Show", "slug": "SECOND-SHOW"},
    ],
    ids=["no-slug", "own-slug", "free-slug", "taken-slug", "taken-slug-other-case"],
)
def test_api_update_does_not_report_other_integrity_errors_as_a_conflict(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    body: dict[str, Any],
) -> None:
    """A constraint failure that is not a taken slug stays a server error."""
    enable_ops(client, api_key=_OPS_KEY)
    podcast_id = _add_podcast(db_session, slug="first-show")
    _add_podcast(db_session, slug="second-show")
    before = _rows(db_session)
    monkeypatch.setattr(
        "podex.api.v2.ops.update_ops_podcast",
        _update_then_violate_foreign_key,
    )

    with pytest.raises(IntegrityError, match="FOREIGN KEY"):
        client.patch(f"{_PODCASTS}/{podcast_id}", json=body, headers=_HEADERS)

    assert_that(_rows(db_session)).is_equal_to(before)
    assert_that(_audit_count(db_session)).is_zero()


# Length limits


@pytest.mark.parametrize(
    "model",
    [OpsPodcastCreateRequest, OpsPodcastUpdateRequest, OpsPodcastSourcesInput],
)
def test_request_text_limits_match_the_column_lengths(model: type[BaseModel]) -> None:
    """Every text field is limited to exactly its ``podcasts`` column length."""
    columns = Podcast.__table__.columns
    text_fields = {
        name: field
        for name, field in model.model_fields.items()
        if name not in {"status", "sources"}
    }
    assert_that(text_fields).is_not_empty()
    for name, field in text_fields.items():
        limits = [
            item.max_length for item in field.metadata if hasattr(item, "max_length")
        ]
        assert_that(limits).described_as(name).is_equal_to(
            [columns[name].type.length],
        )


@pytest.mark.parametrize(("path", "limit"), _TEXT_FIELDS, ids=_TEXT_FIELD_IDS)
def test_api_update_rejects_text_over_its_column_length(
    client: TestClient,
    db_session: Session,
    path: tuple[str, ...],
    limit: int,
) -> None:
    """One character over the column length answers 422 and changes nothing."""
    enable_ops(client, api_key=_OPS_KEY)
    podcast_id = _add_podcast(db_session, slug="first-show")
    before = _rows(db_session)

    response = client.patch(
        f"{_PODCASTS}/{podcast_id}",
        json=_body_with(path, "x" * (limit + 1)),
        headers=_HEADERS,
    )

    assert_that(response.status_code).is_equal_to(422)
    error = response.json()["errors"][0]
    assert_that(error["loc"]).is_equal_to(["body", *path])
    assert_that(error["type"]).is_equal_to("string_too_long")
    assert_that(_rows(db_session)).is_equal_to(before)
    assert_that(_audit_count(db_session)).is_zero()


@pytest.mark.parametrize(
    ("field", "limit"),
    [("description", 2000), ("cover_url", 500)],
)
def test_api_update_accepts_text_at_its_column_length(
    client: TestClient,
    db_session: Session,
    field: str,
    limit: int,
) -> None:
    """A value exactly at the column length is stored."""
    enable_ops(client, api_key=_OPS_KEY)
    podcast_id = _add_podcast(db_session, slug="first-show")

    response = client.patch(
        f"{_PODCASTS}/{podcast_id}",
        json={field: "x" * limit},
        headers=_HEADERS,
    )

    assert_that(response.status_code).is_equal_to(200)
    assert_that(_rows(db_session)[0][field]).is_equal_to("x" * limit)


@pytest.mark.parametrize(("path", "limit"), _TEXT_FIELDS, ids=_TEXT_FIELD_IDS)
def test_api_create_rejects_text_over_its_column_length(
    client: TestClient,
    db_session: Session,
    path: tuple[str, ...],
    limit: int,
) -> None:
    """Create applies the same column-length limits."""
    enable_ops(client, api_key=_OPS_KEY)

    response = client.post(
        _PODCASTS,
        json=_create_body_with(path, "x" * (limit + 1)),
        headers=_HEADERS,
    )

    assert_that(response.status_code).is_equal_to(422)
    assert_that(response.json()["errors"][0]["loc"]).is_equal_to(["body", *path])
    assert_that(_rows(db_session)).is_empty()


# Text the database cannot store


@pytest.mark.parametrize("path", _TEXT_PATHS, ids=_TEXT_FIELD_IDS)
@pytest.mark.parametrize(
    "case",
    [
        (
            "bad \ud800",
            "string_unicode",
            "Input should be a valid string, unable to parse raw data as a "
            "unicode string",
        ),
        (
            "bad \x00",
            "value_error",
            "Value error, text must not contain NUL characters",
        ),
    ],
    ids=["unpaired-surrogate", "nul"],
)
def test_api_routes_reject_unstorable_text_with_a_fixed_message(
    client: TestClient,
    db_session: Session,
    path: tuple[str, ...],
    case: tuple[str, str, str],
) -> None:
    """Unencodable text answers 422 on both routes without codec text."""
    value, error_type, message = case
    enable_ops(client, api_key=_OPS_KEY)
    podcast_id = _add_podcast(db_session, slug="first-show")
    before = _rows(db_session)

    updated = _send_escaped(
        client,
        "PATCH",
        f"{_PODCASTS}/{podcast_id}",
        _body_with(path, value),
    )
    created = _send_escaped(client, "POST", _PODCASTS, _create_body_with(path, value))

    for response in (updated, created):
        assert_that(response.status_code).is_equal_to(422)
        assert_that(response.json()["detail"]).is_equal_to("Request validation failed.")
        error = response.json()["errors"][0]
        assert_that(error["loc"]).is_equal_to(["body", *path])
        assert_that(error["type"]).is_equal_to(error_type)
        assert_that(error["msg"]).is_equal_to(message)
        assert_that(response.text).does_not_contain("codec")
    assert_that(_rows(db_session)).is_equal_to(before)
    assert_that(_audit_count(db_session)).is_zero()


# Validation errors raised by the service


def test_service_raises_the_dedicated_error_for_its_own_rules(
    db_session: Session,
) -> None:
    """Cleared fields and unknown sources raise the validation error type."""
    podcast_id = _add_podcast(db_session, slug="first-show")

    with pytest.raises(OpsPodcastValidationError, match="^slug cannot be cleared$"):
        update_ops_podcast(
            db=db_session,
            podcast_id=podcast_id,
            payload=UpdateOpsPodcastInputData(provided_fields=frozenset({"slug"})),
        )
    with pytest.raises(OpsPodcastValidationError, match="^'bogus' is not a valid"):
        create_ops_podcast(
            db=db_session,
            payload=CreateOpsPodcastInputData(
                name="New Show",
                slug="new-show",
                status=PodcastStatus.WATCHLIST,
                discovery_source="bogus",
            ),
        )


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (
            "x" * DISCOVERY_SOURCE_ECHO_LIMIT,
            f"'{'x' * 50}' is not a valid DiscoverySource",
        ),
        (
            "x" * (DISCOVERY_SOURCE_ECHO_LIMIT + 1),
            f"'{'x' * 50}'... is not a valid DiscoverySource",
        ),
        ("x" * 1_000_000, f"'{'x' * 50}'... is not a valid DiscoverySource"),
    ],
    ids=["at-limit", "one-over", "one-million"],
)
def test_unknown_discovery_source_message_is_bounded(
    db_session: Session,
    value: str,
    message: str,
) -> None:
    """The service echoes at most 50 characters of an unknown source."""
    with pytest.raises(OpsPodcastValidationError) as caught:
        create_ops_podcast(
            db=db_session,
            payload=CreateOpsPodcastInputData(
                name="New Show",
                slug="new-show",
                status=PodcastStatus.WATCHLIST,
                discovery_source=value,
            ),
        )

    assert_that(str(caught.value)).is_equal_to(message)


def test_api_answers_a_huge_discovery_source_with_a_small_body(
    client: TestClient,
    db_session: Session,
) -> None:
    """A 1,000,000-character source is not echoed by either route."""
    enable_ops(client, api_key=_OPS_KEY)
    podcast_id = _add_podcast(db_session, slug="first-show")
    huge = "x" * 1_000_000

    updated = client.patch(
        f"{_PODCASTS}/{podcast_id}",
        json={"discovery_source": huge},
        headers=_HEADERS,
    )
    created = client.post(
        _PODCASTS,
        json={"name": "New Show", "slug": "new-show", "discovery_source": huge},
        headers=_HEADERS,
    )

    for response in (updated, created):
        assert_that(response.status_code).is_equal_to(422)
        assert_that(len(response.content)).is_less_than(1_000)


def _raise_internal_value_error(**_: object) -> NoReturn:
    """Fail the way a codec or driver error inside the service would."""
    raise ValueError("internal detail")


@pytest.mark.parametrize(
    "route",
    [
        (
            "podex.api.v2.ops.create_ops_podcast",
            "POST",
            _PODCASTS,
            {"name": "New Show", "slug": "new-show"},
        ),
        (
            "podex.api.v2.ops.update_ops_podcast",
            "PATCH",
            f"{_PODCASTS}/1",
            {"name": "Renamed Show"},
        ),
    ],
    ids=["create", "update"],
)
def test_api_routes_do_not_report_other_value_errors_as_422(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    route: tuple[str, str, str, dict[str, Any]],
) -> None:
    """Only the dedicated validation error is answered with its message."""
    target, method, path, body = route
    enable_ops(client, api_key=_OPS_KEY)
    _add_podcast(db_session, slug="first-show")
    monkeypatch.setattr(target, _raise_internal_value_error)
    opaque = TestClient(client.app, raise_server_exceptions=False)

    with pytest.raises(ValueError, match="^internal detail$"):
        client.request(method, path, json=body, headers=_HEADERS)
    response = opaque.request(method, path, json=body, headers=_HEADERS)

    assert_that(response.status_code).is_equal_to(500)
    assert_that(response.text).does_not_contain("internal detail")


def _update_then_reject(
    *,
    db: Session,
    podcast_id: int,
    payload: UpdateOpsPodcastInputData,
) -> NoReturn:
    """Flush a change, then fail as an invalid payload would."""
    podcast = db.get(Podcast, podcast_id)
    if podcast is None:
        raise AssertionError("expected the podcast to exist")
    podcast.name = payload.name or podcast.name
    db.flush()
    raise OpsPodcastValidationError("slug cannot be cleared")


def test_api_update_rolls_back_writes_made_before_a_rejection(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rejected update discards what the service flushed before it raised."""
    enable_ops(client, api_key=_OPS_KEY)
    podcast_id = _add_podcast(db_session, slug="first-show")
    before = _rows(db_session)
    monkeypatch.setattr("podex.api.v2.ops.update_ops_podcast", _update_then_reject)

    response = client.patch(
        f"{_PODCASTS}/{podcast_id}",
        json={"name": "Renamed Show"},
        headers=_HEADERS,
    )

    assert_that(response.status_code).is_equal_to(422)
    assert_that(response.json()["detail"]).is_equal_to("slug cannot be cleared")
    assert_that(db_session.in_transaction()).is_false()
    assert_that(_rows(db_session)).is_equal_to(before)


def _create_write_then_fail(
    *,
    db: Session,
    payload: CreateOpsPodcastInputData,
) -> NoReturn:
    """Flush a new podcast, then fail with an internal ``ValueError``."""
    db.add(Podcast(name=payload.name, slug=payload.slug))
    db.flush()
    raise ValueError("internal detail")


def _update_write_then_fail(
    *,
    db: Session,
    podcast_id: int,
    payload: UpdateOpsPodcastInputData,
) -> NoReturn:
    """Flush a change to the podcast, then fail with an internal ``ValueError``."""
    podcast = db.get(Podcast, podcast_id)
    if podcast is None:
        raise AssertionError("expected the podcast to exist")
    podcast.name = payload.name or podcast.name
    db.flush()
    raise ValueError("internal detail")


@pytest.fixture(name="file_sessions")
def file_sessions_fixture(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    """Provide sessions on a file database that separate connections can read."""
    engine = create_engine(f"sqlite:///{tmp_path / 'ops-podcasts.db'}")
    enable_sqlite_foreign_keys(engine)
    Base.metadata.create_all(engine)
    try:
        yield sessionmaker(bind=engine, autoflush=False)
    finally:
        engine.dispose()


def _per_request_client(sessions: sessionmaker[Session]) -> TestClient:
    """Return a client that opens and closes one session per request, as in use."""
    app = create_app(Settings(rate_limit=RateLimitSettings(enabled=False)))

    def per_request_session() -> Iterator[Session]:
        """Yield a new session and close it after the request."""
        db = sessions()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = per_request_session
    app.dependency_overrides[get_app_settings] = lambda: Settings(
        ops_api_key=_OPS_KEY,
    )
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    "route",
    [
        (
            "podex.api.v2.ops.create_ops_podcast",
            _create_write_then_fail,
            "POST",
            _PODCASTS,
            {"name": "New Show", "slug": "new-show"},
        ),
        (
            "podex.api.v2.ops.update_ops_podcast",
            _update_write_then_fail,
            "PATCH",
            f"{_PODCASTS}/1",
            {"name": "Renamed Show"},
        ),
    ],
    ids=["create", "update"],
)
def test_api_internal_value_error_after_a_write_answers_500_and_keeps_nothing(
    file_sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    route: tuple[str, Callable[..., NoReturn], str, str, dict[str, Any]],
) -> None:
    """A service that flushes, then fails internally, leaves no committed change."""
    target, stand_in, method, path, body = route
    with file_sessions() as db:
        _add_podcast(db, slug="first-show")
        before = _rows(db)
    monkeypatch.setattr(target, stand_in)

    with _per_request_client(file_sessions) as client:
        response = client.request(method, path, json=body, headers=_HEADERS)

    assert_that(response.status_code).is_equal_to(500)
    assert_that(response.text).does_not_contain("internal detail")
    with file_sessions() as fresh:
        assert_that(_rows(fresh)).is_equal_to(before)
        assert_that(_audit_count(fresh)).is_zero()

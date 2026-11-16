"""Which integrity errors the ops podcast update route answers as a slug conflict.

The route decides from the database error alone: only the slug's unique
violation is a 409; every other integrity error stays a server error, even
when the request also names another podcast's slug.
"""

import sqlite3
from typing import Any, NoReturn

import pytest
from assertpy import assert_that
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from podex.models import Podcast
from podex.services.ops_podcast_commands import (
    UpdateOpsPodcastInputData,
    is_slug_conflict,
    update_ops_podcast,
)
from tests.conftest import enable_ops

_OPS_KEY = "ops-podcast-slug-conflicts-test-key"
_HEADERS = {"X-Ops-Key": _OPS_KEY}
_PODCASTS = "/api/v2/ops/podcasts"
_ROWS_SQL = text("SELECT id, name, slug, rss_url FROM podcasts ORDER BY id")


def _seed(db: Session) -> int:
    """Commit two podcasts and return the id of the first."""
    first = Podcast(name="First Show", slug="first-show")
    db.add(first)
    db.add(
        Podcast(
            name="Second Show",
            slug="second-show",
            rss_url="https://example.com/second.xml",
        ),
    )
    db.commit()
    podcast_id: int = first.id
    return podcast_id


def _rows(db: Session) -> list[dict[str, Any]]:
    """Read every podcast row with SQL, bypassing the session's identity map."""
    return [dict(row) for row in db.execute(_ROWS_SQL).mappings()]


def _driver_error(*statements: str) -> sqlite3.IntegrityError:
    """Return the error SQLite raises for the last of ``statements``."""
    connection = sqlite3.connect(":memory:")
    try:
        for statement in statements[:-1]:
            connection.execute(statement)
        with pytest.raises(sqlite3.IntegrityError) as raised:
            connection.execute(statements[-1])
    finally:
        connection.close()
    return raised.value


def _wrapped(driver_error: BaseException) -> IntegrityError:
    """Wrap ``driver_error`` the way SQLAlchemy reports a failed flush."""
    error: IntegrityError = IntegrityError("UPDATE podcasts", {}, driver_error)
    return error


def test_api_update_reports_a_trigger_failure_with_a_taken_slug_as_500(
    client: TestClient,
    db_session: Session,
) -> None:
    """A check failure is not a conflict even when the slug is also taken."""
    enable_ops(client, api_key=_OPS_KEY)
    podcast_id = _seed(db_session)
    db_session.execute(
        text(
            "CREATE TRIGGER reject_boom BEFORE UPDATE ON podcasts "
            "WHEN NEW.name = 'boom' BEGIN SELECT RAISE(ABORT, 'boom rejected'); END",
        ),
    )
    db_session.commit()
    before = _rows(db_session)

    with pytest.raises(IntegrityError, match="boom rejected"):
        client.patch(
            f"{_PODCASTS}/{podcast_id}",
            json={"name": "boom", "slug": "second-show"},
            headers=_HEADERS,
        )

    assert_that(_rows(db_session)).is_equal_to(before)


@pytest.mark.parametrize(
    "body",
    [
        {"sources": {"rss_url": "https://example.com/second.xml"}},
        {
            "slug": "first-show",
            "sources": {"rss_url": "https://example.com/second.xml"},
        },
        {"slug": "free-slug", "sources": {"rss_url": "https://example.com/second.xml"}},
    ],
    ids=["no-slug", "own-slug", "free-slug"],
)
def test_api_update_reports_another_unique_violation_as_500(
    client: TestClient,
    db_session: Session,
    body: dict[str, Any],
) -> None:
    """A unique index other than the slug's is not a slug conflict."""
    enable_ops(client, api_key=_OPS_KEY)
    podcast_id = _seed(db_session)
    db_session.execute(
        text("CREATE UNIQUE INDEX uq_test_podcasts_rss_url ON podcasts (rss_url)"),
    )
    db_session.commit()
    before = _rows(db_session)

    with pytest.raises(IntegrityError, match="podcasts.rss_url"):
        client.patch(f"{_PODCASTS}/{podcast_id}", json=body, headers=_HEADERS)

    assert_that(_rows(db_session)).is_equal_to(before)


def test_api_update_answers_409_after_the_other_podcast_is_gone(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A genuine conflict stays a 409 if the other row changes before handling."""
    enable_ops(client, api_key=_OPS_KEY)
    podcast_id = _seed(db_session)
    with pytest.raises(IntegrityError) as conflict:
        update_ops_podcast(
            db=db_session,
            podcast_id=podcast_id,
            payload=UpdateOpsPodcastInputData(
                provided_fields=frozenset({"slug"}),
                slug="second-show",
            ),
        )
    db_session.rollback()
    db_session.execute(text("DELETE FROM podcasts WHERE slug = 'second-show'"))
    db_session.commit()

    def _raise_the_earlier_conflict(**_: object) -> NoReturn:
        """Fail with the conflict raised before the other podcast was removed."""
        raise conflict.value

    monkeypatch.setattr(
        "podex.api.v2.ops.update_ops_podcast",
        _raise_the_earlier_conflict,
    )

    response = client.patch(
        f"{_PODCASTS}/{podcast_id}",
        json={"slug": "second-show"},
        headers=_HEADERS,
    )

    assert_that(response.status_code).is_equal_to(409)
    assert_that(response.json()["detail"]).is_equal_to("Podcast slug already exists")


def test_api_update_accepts_a_slug_differing_only_in_case(
    client: TestClient,
    db_session: Session,
) -> None:
    """Slugs are unique case-sensitively; another slug in capitals is free."""
    enable_ops(client, api_key=_OPS_KEY)
    podcast_id = _seed(db_session)

    response = client.patch(
        f"{_PODCASTS}/{podcast_id}",
        json={"slug": "SECOND-SHOW"},
        headers=_HEADERS,
    )

    assert_that(response.status_code).is_equal_to(200)
    assert_that(response.json()["slug"]).is_equal_to("SECOND-SHOW")


def test_slug_conflict_recognises_only_the_slug_unique_violation() -> None:
    """SQLite errors are classified by their extended code and exact message."""
    table = "CREATE TABLE podcasts (slug TEXT UNIQUE, rss_url TEXT UNIQUE, name TEXT)"
    slug_duplicate = _driver_error(
        table,
        "INSERT INTO podcasts VALUES ('show', 'a', 'x')",
        "INSERT INTO podcasts VALUES ('show', 'b', 'y')",
    )
    rss_duplicate = _driver_error(
        table,
        "INSERT INTO podcasts VALUES ('one', 'a', 'x')",
        "INSERT INTO podcasts VALUES ('two', 'a', 'y')",
    )
    other_table = _driver_error(
        "CREATE TABLE shows (slug TEXT UNIQUE)",
        "INSERT INTO shows VALUES ('show')",
        "INSERT INTO shows VALUES ('show')",
    )
    not_null = _driver_error(
        "CREATE TABLE podcasts (slug TEXT NOT NULL)",
        "INSERT INTO podcasts VALUES (NULL)",
    )
    # Messages that only contain ``podcasts.slug`` name another constraint.
    suffixed_table = _driver_error(
        "CREATE TABLE old_podcasts (slug TEXT UNIQUE)",
        "INSERT INTO old_podcasts VALUES ('show')",
        "INSERT INTO old_podcasts VALUES ('show')",
    )
    composite = _driver_error(
        "CREATE TABLE podcasts (slug TEXT, name TEXT, UNIQUE (slug, name))",
        "INSERT INTO podcasts VALUES ('show', 'x')",
        "INSERT INTO podcasts VALUES ('show', 'x')",
    )

    assert_that(is_slug_conflict(_wrapped(slug_duplicate))).is_true()
    assert_that(is_slug_conflict(_wrapped(rss_duplicate))).is_false()
    assert_that(is_slug_conflict(_wrapped(other_table))).is_false()
    assert_that(is_slug_conflict(_wrapped(not_null))).is_false()
    assert_that(str(suffixed_table)).contains("podcasts.slug")
    assert_that(is_slug_conflict(_wrapped(suffixed_table))).is_false()
    assert_that(str(composite)).contains("podcasts.slug")
    assert_that(is_slug_conflict(_wrapped(composite))).is_false()


def test_slug_conflict_rejects_unidentified_errors() -> None:
    """An error from an unknown driver, or without SQLite's code, is not a conflict."""
    unknown_driver = _wrapped(RuntimeError("UNIQUE constraint failed: podcasts.slug"))
    uncoded = _wrapped(
        sqlite3.IntegrityError("UNIQUE constraint failed: podcasts.slug")
    )

    assert_that(is_slug_conflict(unknown_driver)).is_false()
    assert_that(is_slug_conflict(uncoded)).is_false()

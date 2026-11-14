"""The shared PostgreSQL fixture only ever drops databases it created.

Name collisions are forced through ``new_database_name``; termination is
exercised on an inner pytest run against an isolated copy of the scaffolding.
"""

import signal
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Any

import pytest
from assertpy import assert_that
from sqlalchemy import Engine, create_engine, text

from tests.isolated_suite import (
    INNER_RUN_TIMEOUT_SECONDS,
    build_isolated_suite,
    launch_inner_pytest,
)
from tests.postgres import conftest as fixture_module
from tests.postgres.conftest import (
    NAME_ATTEMPTS,
    PostgresServer,
    migrated_template,
    private_database,
)

pytestmark = pytest.mark.postgres

OpenDatabase = Callable[[PostgresServer], AbstractContextManager[str]]


def _database_exists(server: PostgresServer, name: str) -> bool:
    """Return whether a database called ``name`` exists on the server."""
    with server.admin.connect() as connection:
        found = connection.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :name"),
            {"name": name},
        ).scalar_one_or_none()
    return found is not None


def _contents(server: PostgresServer, name: str) -> list[str]:
    """Return the rows of ``keep`` in database ``name``."""
    engine = create_engine(server.base_url.set(database=name))
    try:
        with engine.connect() as connection:
            return list(connection.execute(text("SELECT v FROM keep")).scalars())
    finally:
        engine.dispose()


@pytest.fixture(name="existing_database")
def existing_database_fixture(postgres_server: PostgresServer) -> Iterator[str]:
    """Create a database the fixture must not touch, with one row in it.

    Yields:
        The database's name.
    """
    name = f"podex_collision_{uuid.uuid4().hex}"
    with postgres_server.admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        engine = create_engine(postgres_server.base_url.set(database=name))
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE keep (v text)"))
            connection.execute(text("INSERT INTO keep VALUES ('precious')"))
        engine.dispose()
        yield name
    finally:
        with postgres_server.admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


@contextmanager
def _open_template(server: PostgresServer) -> Iterator[str]:
    """Open a fresh migrated template, as the session fixture does.

    Yields:
        The template database's name.
    """
    with migrated_template(server.admin, server.base_url) as name:
        yield name


@contextmanager
def _open_private(server: PostgresServer) -> Iterator[str]:
    """Open a per-test database, as the ``postgres`` fixture does.

    Yields:
        The per-test database's name.
    """
    with private_database(server) as database:
        yield str(database.url.database)


@pytest.mark.parametrize(
    "open_database",
    [_open_template, _open_private],
    ids=["template", "per-test"],
)
def test_name_collision_retries_and_keeps_the_existing_database(
    postgres_server: PostgresServer,
    existing_database: str,
    monkeypatch: pytest.MonkeyPatch,
    open_database: OpenDatabase,
) -> None:
    """A taken name is skipped, and the database holding it survives intact."""
    fresh = f"podex_test_{uuid.uuid4().hex}"
    names = iter([existing_database, fresh])
    monkeypatch.setattr(
        fixture_module, "new_database_name", lambda _prefix: next(names)
    )

    with open_database(postgres_server) as created:
        assert_that(_database_exists(postgres_server, created)).is_true()

    assert_that(created).is_equal_to(fresh)
    assert_that(_database_exists(postgres_server, fresh)).is_false()
    assert_that(_contents(postgres_server, existing_database)).is_equal_to(
        ["precious"],
    )


@pytest.mark.parametrize(
    "open_database",
    [_open_template, _open_private],
    ids=["template", "per-test"],
)
def test_exhausted_names_fail_without_dropping_anything(
    postgres_server: PostgresServer,
    existing_database: str,
    monkeypatch: pytest.MonkeyPatch,
    open_database: OpenDatabase,
) -> None:
    """If every attempt collides, creation fails and nothing is dropped."""
    attempts: list[str] = []

    def always_taken(_prefix: str) -> str:
        attempts.append(existing_database)
        return existing_database

    monkeypatch.setattr(fixture_module, "new_database_name", always_taken)

    with pytest.raises(RuntimeError, match="no free database name"):
        with open_database(postgres_server):
            pass

    assert_that(attempts).is_length(NAME_ATTEMPTS)
    assert_that(_contents(postgres_server, existing_database)).is_equal_to(
        ["precious"],
    )


def _drop_if_left(server: PostgresServer, names: list[str]) -> None:
    """Drop databases a failing boundary test may have leaked."""
    for name in names:
        fixture_module.drop_database(server.admin, name)


@pytest.mark.parametrize(
    "open_database",
    [_open_template, _open_private],
    ids=["template", "per-test"],
)
def test_signal_right_after_create_still_drops_the_database(
    postgres_server: PostgresServer,
    monkeypatch: pytest.MonkeyPatch,
    open_database: OpenDatabase,
) -> None:
    """A signal landing just after CREATE DATABASE arrives once it is owned."""
    created: list[str] = []
    create = fixture_module.create_unique_database

    def create_then_interrupt(*args: Any, **kwargs: Any) -> str:
        name = create(*args, **kwargs)
        created.append(name)
        signal.raise_signal(signal.SIGINT)
        return name

    monkeypatch.setattr(fixture_module, "create_unique_database", create_then_interrupt)
    try:
        with pytest.raises(KeyboardInterrupt):
            with open_database(postgres_server):
                pytest.fail("the deferred SIGINT must end the setup")

        assert_that(created).is_length(1)
        assert_that(_database_exists(postgres_server, created[0])).is_false()
    finally:
        _drop_if_left(postgres_server, created)


@pytest.mark.parametrize(
    "open_database",
    [_open_template, _open_private],
    ids=["template", "per-test"],
)
def test_signal_during_teardown_waits_for_the_drop(
    postgres_server: PostgresServer,
    monkeypatch: pytest.MonkeyPatch,
    open_database: OpenDatabase,
) -> None:
    """A signal landing just before DROP DATABASE arrives after the drop."""
    opened: list[str] = []
    drop = fixture_module.drop_database

    def interrupt_then_drop(admin: Engine, name: str) -> None:
        signal.raise_signal(signal.SIGINT)
        drop(admin, name)

    monkeypatch.setattr(fixture_module, "drop_database", interrupt_then_drop)
    try:
        with pytest.raises(KeyboardInterrupt):
            with open_database(postgres_server) as name:
                opened.append(name)

        assert_that(opened).is_length(1)
        assert_that(_database_exists(postgres_server, opened[0])).is_false()
    finally:
        monkeypatch.undo()
        _drop_if_left(postgres_server, opened)


@pytest.mark.parametrize("fixture", ["db_session", "client", "seeded_graph"])
def test_sqlite_fixtures_fail_in_postgres_tests(
    request: pytest.FixtureRequest,
    fixture: str,
) -> None:
    """A PostgreSQL test cannot fall back to an inherited SQLite fixture."""
    with pytest.raises(pytest.fail.Exception, match=f"{fixture} is the SQLite"):
        request.getfixturevalue(fixture)


_WAITING_TEST = '''
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.postgres


def test_wait(postgres: object, postgres_server: object) -> None:
    """Report the databases in use, then wait to be terminated."""
    names = [postgres_server.template, postgres.url.database]
    Path({ready!r}).write_text("\\n".join(names))
    time.sleep({seconds})
'''


@pytest.mark.launches_pytest
def test_sigterm_drops_the_template_and_test_database(
    request: pytest.FixtureRequest,
    pytester: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
    postgres_server: PostgresServer,
) -> None:
    """SIGTERM mid-test ends the inner session with both databases dropped."""
    ready = pytester.path / "ready.txt"
    source = _WAITING_TEST.format(ready=str(ready), seconds=INNER_RUN_TIMEOUT_SECONDS)
    build_isolated_suite(pytester, {"test_wait.py": source})
    inner = launch_inner_pytest(
        request,
        pytester,
        monkeypatch,
        ["tests/postgres/test_wait.py::test_wait"],
        "--require-postgres",
    )
    try:
        deadline = time.monotonic() + INNER_RUN_TIMEOUT_SECONDS
        while not ready.exists() and inner.poll() is None:
            if time.monotonic() > deadline:
                raise AssertionError("the inner test never reported its databases")
            time.sleep(0.05)
        names = ready.read_text().splitlines()
        assert_that(names).is_length(2)
        assert_that(all(_database_exists(postgres_server, n) for n in names)).is_true()

        inner.send_signal(signal.SIGTERM)
        output, _ = inner.communicate(timeout=INNER_RUN_TIMEOUT_SECONDS)
    finally:
        if inner.poll() is None:
            inner.kill()
            inner.communicate()

    assert_that(inner.returncode).is_equal_to(128 + signal.SIGTERM)
    assert_that(output.decode()).contains("terminated by signal 15")
    for name in names:
        assert_that(_database_exists(postgres_server, name)).is_false()

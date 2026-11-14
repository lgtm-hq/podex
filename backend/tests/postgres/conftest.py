"""Shared PostgreSQL fixture: one migrated database per test.

Each pytest process migrates a private template database once with
``alembic upgrade head``; every test then gets its own copy made with
``CREATE DATABASE ... TEMPLATE``. The fixture creates and force-drops whole
databases on the server ``PODEX_DATABASE__URL`` points at, so point it only at
a disposable server. Its role must be a superuser: installing pgvector needs
one on stock images.

Safety rules:

- Only databases this process created are dropped. Names are random
  (``DATABASE_NAME_PATTERN``); a name that already exists is never adopted.
  Creation retries with a fresh name, and a failed create never makes a name
  owned.
- A database is recorded as owned in the same signal-deferred step that
  creates it, and its drop is signal-deferred too (``tests/postgres/signals``),
  so SIGINT or SIGTERM cannot leave it behind. While databases exist, SIGTERM
  ends the session like Ctrl-C (``pytest.exit``). What remains: SIGKILL, a
  connection lost after the server created a database, and fixtures driven
  from a non-main thread; CONTRIBUTING.md has the recovery sweep.
- The database named in the URL is used only to issue ``CREATE DATABASE`` and
  ``DROP DATABASE``; no table in it is read or written.
- Every test collected from this directory must carry
  ``@pytest.mark.postgres``. That is checked as each test is collected, before
  any ``pytest_collection_modifyitems`` hook can deselect it. The SQLite
  fixtures (``db_session``, ``client``, ``seeded_graph``) fail here.

Run locally (disposable server only; see CONTRIBUTING.md)::

    PODEX_DATABASE__URL=postgresql+psycopg://podex@localhost:5432/podex \\
    uv run pytest tests -m postgres --require-postgres
"""

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

import pytest
from alembic.command import upgrade
from alembic.config import Config
from psycopg.errors import DuplicateDatabase
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from tests.conftest import POSTGRES_URL_ENV
from tests.postgres.signals import deferred_signals, sigterm_exits_session

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"
POSTGRES_TESTS_DIR = Path(__file__).resolve().parent

DATABASE_PREFIX = "podex_test_"
TEMPLATE_PREFIX = f"{DATABASE_PREFIX}template_"
# Every database the fixture creates matches this, and nothing else should.
DATABASE_NAME_PATTERN = r"^podex_test_(template_)?[0-9a-f]{32}$"
NAME_ATTEMPTS = 5


@dataclass(frozen=True, slots=True)
class PostgresDatabase:
    """A migrated, test-private PostgreSQL database.

    Attributes:
        url: URL of the test database.
        engine: Engine bound to the test database.
        sessions: Session factory bound to ``engine``; sessions do not expire
            attributes on commit.
    """

    url: URL
    engine: Engine
    sessions: sessionmaker[Session]


@dataclass(frozen=True, slots=True)
class PostgresServer:
    """Maintenance connection and the migrated template for one process.

    Attributes:
        admin: Autocommit engine on the URL's database, used only for
            ``CREATE DATABASE`` and ``DROP DATABASE``.
        base_url: The configured ``PODEX_DATABASE__URL``.
        template: Name of this process's migrated template database.
    """

    admin: Engine
    base_url: URL
    template: str


def new_database_name(prefix: str) -> str:
    """Return a random database name matching ``DATABASE_NAME_PATTERN``."""
    return f"{prefix}{uuid.uuid4().hex}"


def create_unique_database(admin: Engine, prefix: str, *, template: str | None) -> str:
    """Create a database under a fresh name, never adopting an existing one.

    Args:
        admin: Autocommit maintenance engine.
        prefix: Name prefix passed to ``new_database_name``.
        template: Database to copy, or ``None`` for an empty database.

    Returns:
        The name of the database this call created.

    Raises:
        RuntimeError: If every attempted name already existed.
    """
    preparer = admin.dialect.identifier_preparer
    for _ in range(NAME_ATTEMPTS):
        name = new_database_name(prefix)
        statement = f"CREATE DATABASE {preparer.quote(name)}"
        if template is not None:
            statement += f" TEMPLATE {preparer.quote(template)}"
        try:
            with admin.connect() as connection:
                connection.execute(text(statement))
        except ProgrammingError as error:
            if isinstance(error.orig, DuplicateDatabase):
                continue
            raise
        return name
    msg = f"no free database name with prefix {prefix!r} in {NAME_ATTEMPTS} attempts"
    raise RuntimeError(msg)


def drop_database(admin: Engine, name: str) -> None:
    """Drop a database this process created, ending sessions still on it."""
    quoted = admin.dialect.identifier_preparer.quote(name)
    with admin.connect() as connection:
        connection.execute(text(f"DROP DATABASE IF EXISTS {quoted} WITH (FORCE)"))


@contextmanager
def _owned_database(
    admin: Engine, prefix: str, *, template: str | None
) -> Iterator[str]:
    """Create a database, own it from that instant, and drop it on exit.

    Creation and the ownership record share one signal-deferred step inside
    the ``try`` whose ``finally`` drops what is owned, so a signal delivered
    when that step ends is already covered by cleanup. The drop is deferred
    too, so it runs to completion before a pending signal is delivered.

    Yields:
        The created database's name.
    """
    owned: list[str] = []
    try:
        with deferred_signals():
            owned.append(create_unique_database(admin, prefix, template=template))
        yield owned[0]
    finally:
        with deferred_signals():
            for name in owned:
                drop_database(admin, name)


def _migrate(url: URL) -> None:
    """Run the Alembic chain to head against ``url``."""
    config = Config(str(ALEMBIC_INI))
    rendered = url.render_as_string(hide_password=False)
    # ConfigParser interpolation treats ``%`` specially; escape it.
    config.set_main_option("sqlalchemy.url", rendered.replace("%", "%%"))
    upgrade(config, "head")


@contextmanager
def migrated_template(admin: Engine, base_url: URL) -> Iterator[str]:
    """Create and migrate a template database; drop it on exit.

    Yields:
        The template database's name.
    """
    with _owned_database(admin, TEMPLATE_PREFIX, template=None) as name:
        _migrate(base_url.set(database=name))
        yield name


@contextmanager
def private_database(server: PostgresServer) -> Iterator[PostgresDatabase]:
    """Copy the template into a new database; drop it on exit.

    Yields:
        The new database's URL, engine and session factory.
    """
    with _owned_database(
        server.admin, DATABASE_PREFIX, template=server.template
    ) as name:
        url = server.base_url.set(database=name)
        engine = create_engine(url)
        try:
            yield PostgresDatabase(
                url=url,
                engine=engine,
                sessions=sessionmaker(bind=engine, expire_on_commit=False),
            )
        finally:
            engine.dispose()


def check_postgres_marker(item: pytest.Item, *, directory: Path) -> None:
    """Refuse a test collected from ``directory`` without the postgres marker.

    Raises:
        pytest.UsageError: If ``item`` lives under ``directory`` unmarked.
    """
    if (
        item.path.is_relative_to(directory)
        and item.get_closest_marker("postgres") is None
    ):
        msg = "tests under tests/postgres must carry @pytest.mark.postgres: "
        raise pytest.UsageError(msg + item.nodeid)


def pytest_itemcollected(item: pytest.Item) -> None:
    """Check each test as it is collected, before any hook can deselect it.

    ``-m`` filtering and every ``pytest_collection_modifyitems`` hook, including
    one in a nested conftest, run only after collection, so neither the
    PostgreSQL lane nor a nested hook can drop an unmarked test unnoticed.
    """
    check_postgres_marker(item, directory=POSTGRES_TESTS_DIR)


def _sqlite_fixture_unavailable(name: str) -> NoReturn:
    """Fail a test that asked for a SQLite fixture from this directory."""
    pytest.fail(
        f"{name} is the SQLite fixture; tests under tests/postgres use the "
        "postgres fixture",
        pytrace=False,
    )


@pytest.fixture(name="db_session")
def db_session_fixture() -> NoReturn:
    """Block the inherited SQLite session in PostgreSQL tests."""
    _sqlite_fixture_unavailable("db_session")


@pytest.fixture(name="client")
def client_fixture() -> NoReturn:
    """Block the inherited SQLite-backed test client in PostgreSQL tests."""
    _sqlite_fixture_unavailable("client")


@pytest.fixture(name="seeded_graph")
def seeded_graph_fixture() -> NoReturn:
    """Block the inherited SQLite catalog graph in PostgreSQL tests."""
    _sqlite_fixture_unavailable("seeded_graph")


@pytest.fixture(name="postgres_server", scope="session")
def postgres_server_fixture() -> Iterator[PostgresServer]:
    """Migrate a private template database once per test process.

    Yields:
        The maintenance engine, configured URL and template name.
    """
    base_url = make_url(os.environ[POSTGRES_URL_ENV])
    admin = create_engine(base_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    try:
        with sigterm_exits_session(), migrated_template(admin, base_url) as template:
            yield PostgresServer(admin=admin, base_url=base_url, template=template)
    finally:
        admin.dispose()


@pytest.fixture(name="postgres")
def postgres_fixture(postgres_server: PostgresServer) -> Iterator[PostgresDatabase]:
    """Provide a migrated database private to this test, dropped afterwards.

    Only tests in this directory see this fixture, and collection refuses any
    of them without ``@pytest.mark.postgres``.

    Yields:
        The test database's URL, engine and session factory.
    """
    with private_database(postgres_server) as database:
        yield database

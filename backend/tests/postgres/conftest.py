"""Shared PostgreSQL fixture: one migrated database per test.

Each pytest process migrates a private template database once with
``alembic upgrade head``; every test then gets its own copy made with
``CREATE DATABASE ... TEMPLATE``. Database names are random, so parallel
workers and concurrent runs never share state, and the database named in
``PODEX_DATABASE__URL`` is used only to issue ``CREATE DATABASE`` and
``DROP DATABASE`` (its role needs ``CREATEDB``); no table in it is read or
written. Both databases are dropped with ``WITH (FORCE)`` in ``finally``
blocks, so a failing test, or one that leaves a connection open, still cleans
up. Tests using the fixture must carry ``@pytest.mark.postgres``, which is what
skips them, or fails them under ``--require-postgres``, when PostgreSQL is not
configured (see ``tests/conftest.py``).

Run locally against any PostgreSQL with pgvector::

    PODEX_DATABASE__URL=postgresql+psycopg://podex@localhost:5432/podex \\
    uv run pytest tests -m postgres --require-postgres
"""

import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from alembic.command import upgrade
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from tests.conftest import POSTGRES_URL_ENV

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


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
class _PostgresServer:
    """Maintenance connection and the migrated template for one process."""

    admin: Engine
    base_url: URL
    template: str


def _create_database(admin: Engine, name: str, *, template: str | None) -> None:
    """Create a database, optionally as a copy of ``template``."""
    preparer = admin.dialect.identifier_preparer
    statement = f"CREATE DATABASE {preparer.quote(name)}"
    if template is not None:
        statement += f" TEMPLATE {preparer.quote(template)}"
    with admin.connect() as connection:
        connection.execute(text(statement))


def _drop_database(admin: Engine, name: str) -> None:
    """Drop a database, ending any session still connected to it."""
    quoted = admin.dialect.identifier_preparer.quote(name)
    with admin.connect() as connection:
        connection.execute(text(f"DROP DATABASE IF EXISTS {quoted} WITH (FORCE)"))


def _migrate(url: URL) -> None:
    """Run the Alembic chain to head against ``url``."""
    config = Config(str(ALEMBIC_INI))
    rendered = url.render_as_string(hide_password=False)
    # ConfigParser interpolation treats ``%`` specially; escape it.
    config.set_main_option("sqlalchemy.url", rendered.replace("%", "%%"))
    upgrade(config, "head")


@pytest.fixture(name="postgres_server", scope="session")
def postgres_server_fixture() -> Iterator[_PostgresServer]:
    """Migrate a private template database once per test process.

    Yields:
        The maintenance engine and the template database name.
    """
    base_url = make_url(os.environ[POSTGRES_URL_ENV])
    admin = create_engine(base_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    template = f"podex_test_template_{uuid.uuid4().hex[:12]}"
    try:
        _create_database(admin, template, template=None)
        _migrate(base_url.set(database=template))
        yield _PostgresServer(admin=admin, base_url=base_url, template=template)
    finally:
        _drop_database(admin, template)
        admin.dispose()


@pytest.fixture(name="postgres")
def postgres_fixture(
    request: pytest.FixtureRequest,
    postgres_server: _PostgresServer,
) -> Iterator[PostgresDatabase]:
    """Provide a migrated database private to this test, dropped afterwards.

    Yields:
        The test database's URL, engine and session factory.
    """
    if request.node.get_closest_marker("postgres") is None:
        pytest.fail(
            "the postgres fixture needs @pytest.mark.postgres on the test",
            pytrace=False,
        )
    name = f"podex_test_{uuid.uuid4().hex[:16]}"
    url = postgres_server.base_url.set(database=name)
    try:
        _create_database(postgres_server.admin, name, template=postgres_server.template)
        engine = create_engine(url)
        try:
            yield PostgresDatabase(
                url=url,
                engine=engine,
                sessions=sessionmaker(bind=engine, expire_on_commit=False),
            )
        finally:
            engine.dispose()
    finally:
        _drop_database(postgres_server.admin, name)

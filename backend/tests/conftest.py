"""Shared pytest fixtures."""

import json
import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pytest
import sentry_sdk
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import HttpTransport
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from podex.api.deps import get_app_settings
from podex.config import (
    ObservabilitySettings,
    RateLimitSettings,
    RetentionSettings,
    Settings,
)
from podex.database import enable_sqlite_foreign_keys, get_db
from podex.main import create_app
from podex.models import (
    AccountAlertRule,
    Base,
    Episode,
    Media,
    MediaType,
    Mention,
    Podcast,
)
from podex.services.account_alerts import create_alert_rule
from podex.services.recurring_notifications import reconcile_notification_schedules
from podex.services.scheduled_work import plan_due_scheduled_work

REQUIRE_POSTGRES_OPTION = "--require-postgres"
POSTGRES_URL_ENV = "PODEX_DATABASE__URL"


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register the option that turns a missing PostgreSQL into a failure."""
    parser.addoption(
        REQUIRE_POSTGRES_OPTION,
        action="store_true",
        default=False,
        help=(
            "Fail, instead of skip, tests marked 'postgres' when "
            f"{POSTGRES_URL_ENV} is not a PostgreSQL URL (the PostgreSQL CI lane)."
        ),
    )


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    """Skip or fail PostgreSQL-marked tests when PostgreSQL is not configured.

    This is the repository's single PostgreSQL-availability mechanism. Outside
    the PostgreSQL lane, a marked test is skipped with the reason; with
    ``--require-postgres`` it fails before any fixture runs.
    """
    if item.get_closest_marker("postgres") is None:
        return
    if os.environ.get(POSTGRES_URL_ENV, "").startswith("postgresql"):
        return
    reason = f"needs PostgreSQL: {POSTGRES_URL_ENV} is not a postgresql URL"
    if item.config.getoption(REQUIRE_POSTGRES_OPTION):
        pytest.fail(f"{reason} and {REQUIRE_POSTGRES_OPTION} is set", pytrace=False)
    pytest.skip(reason)


@dataclass
class SeededGraph:
    """Identifiers for a linked podcast, episode, media, and mention graph.

    Attributes:
        podcast_id: Identifier of the seeded podcast source.
        episode_id: Identifier of the seeded episode belonging to the podcast.
        media_id: Identifier of the seeded canonical media item.
        mention_id: Identifier of the mention linking the episode to the media.
    """

    podcast_id: int
    episode_id: int
    media_id: int
    mention_id: int


def create_jre_episode(db: Session) -> Episode:
    """Persist the JRE source and episode used by derivative tests."""
    podcast = Podcast(name="The Joe Rogan Experience", slug="jre")
    db.add(podcast)
    db.flush()
    episode = Episode(podcast_id=podcast.id, title="JRE #1", episode_number=1)
    db.add(episode)
    db.flush()
    return episode


def seed_catalog_graph(db: Session) -> SeededGraph:
    """Persist a fully connected catalog graph and return its identifiers.

    Args:
        db: Database session used to persist the catalog rows.

    Returns:
        The identifiers of the seeded podcast, episode, media, and mention.
    """
    podcast = Podcast(
        name="The Example Show",
        slug="example-show",
        description="A show about examples.",
    )
    db.add(podcast)
    db.commit()

    episode = Episode(podcast_id=podcast.id, title="Pilot", episode_number=1)
    media = Media(type=MediaType.BOOK, title="Dune", author="Herbert", year=1965)
    db.add_all([episode, media])
    db.commit()

    mention = Mention(
        episode_id=episode.id,
        media_id=media.id,
        timestamp_seconds=42,
        context="a great book",
        confidence=0.9,
    )
    db.add(mention)
    db.commit()

    return SeededGraph(
        podcast_id=podcast.id,
        episode_id=episode.id,
        media_id=media.id,
        mention_id=mention.id,
    )


@pytest.fixture
def seeded_graph(db_session: Session) -> SeededGraph:
    """Return a fully linked podcast → episode → mention → media graph."""
    return seed_catalog_graph(db_session)


@pytest.fixture(name="db_session")
def db_session_fixture() -> Iterator[Session]:
    """Provide an isolated in-memory database session per test."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    enable_sqlite_foreign_keys(engine)
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = testing_session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture(name="client")
def client_fixture(db_session: Session) -> Iterator[TestClient]:
    """Return a test client backed by the in-memory database session."""
    app = create_app()

    def override_get_db() -> Iterator[Session]:
        """Yield the test database session in place of the real one."""
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client


def enable_ops(
    client: TestClient, *, api_key: str, retention: RetentionSettings | None = None
) -> None:
    """Configure ops authentication and an optional retention settings override."""
    app = client.app
    if not isinstance(app, FastAPI):
        raise AssertionError("Expected a FastAPI test app")
    app.dependency_overrides[get_app_settings] = lambda: Settings(
        ops_api_key=api_key,
        retention=retention if retention is not None else RetentionSettings(),
    )


def plan_notification_work(db: Session, *, now: datetime) -> None:
    """Plan due digest and retention work with the shared test cadence."""
    reconcile_notification_schedules(
        db=db,
        digest_interval_minutes=60,
        retention_interval_minutes=60,
        now=now,
    )
    plan_due_scheduled_work(db=db, now=now)


@pytest.fixture(name="failing_client")
def failing_client_fixture() -> Iterator[TestClient]:
    """Exercise unhandled-route failures through the full middleware stack."""
    app = create_app(Settings(rate_limit=RateLimitSettings(enabled=False)))

    async def fail_route() -> None:
        """Raise the opaque exception used by error-envelope and access-log tests."""
        raise RuntimeError("boom")

    app.add_api_route("/boom", fail_route, methods=["GET"])
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def persist_episode(db: Session, *, podcast: Podcast, **fields: Any) -> Episode:
    """Persist an episode and its source without committing the caller's transaction."""
    db.add(podcast)
    db.flush()
    episode = Episode(podcast_id=podcast.id, **fields)
    db.add(episode)
    db.flush()
    return episode


def create_media_alert_rule(
    *, db: Session, user_id: int, media_id: int
) -> AccountAlertRule | None:
    """Build the canonical new-mention alert used by account scenarios."""
    rule: AccountAlertRule | None = create_alert_rule(
        db=db,
        user_id=user_id,
        target_type="media",
        target_id=media_id,
        event_type="new_mention",
    )
    return rule


def create_podcast_alert_rule(
    *, db: Session, user_id: int, podcast_id: int
) -> AccountAlertRule | None:
    """Build the canonical new-episode alert used by scheduler/account scenarios."""
    rule: AccountAlertRule | None = create_alert_rule(
        db=db,
        user_id=user_id,
        target_type="podcast",
        target_id=podcast_id,
        event_type="new_episode",
    )
    return rule


class SentryEnvelopeRecorder:
    """Keeps every envelope the reporter would send, instead of sending it.

    Installed by the ``sentry_transport`` fixture in place of
    ``HttpTransport.capture_envelope``: the SDK builds its normal HTTP
    transport for the configured DSN, and each envelope (events with their
    breadcrumbs, sessions, client reports) is recorded here at the point it
    would be queued for the network. Nothing is sent.
    """

    def __init__(self) -> None:
        self.envelopes: list[Envelope] = []

    def record(self, envelope: Envelope) -> None:
        """Keep the envelope for inspection.

        Args:
            envelope: The serialisable envelope the SDK would have sent.
        """
        self.envelopes.append(envelope)

    def serialized(self) -> str:
        """Return every captured envelope exactly as it would be sent.

        Returns:
            str: The concatenated wire payloads (before transport compression).
        """
        sentry_sdk.flush()
        return "".join(
            envelope.serialize().decode("utf-8") for envelope in self.envelopes
        )

    def events(self) -> list[dict[str, Any]]:
        """Return the captured error events.

        Returns:
            list[dict[str, Any]]: Event payloads in capture order.
        """
        sentry_sdk.flush()
        return [
            json.loads(item.get_bytes())
            for envelope in self.envelopes
            for item in envelope.items
            if item.type == "event"
        ]


@pytest.fixture(name="sentry_transport")
def sentry_transport_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[SentryEnvelopeRecorder]:
    """Record, instead of send, every envelope the reporter emits.

    ``init_sentry`` runs unchanged; only the HTTP transport's send step is
    replaced for the duration of the test.

    Yields:
        SentryEnvelopeRecorder: Receives every envelope the SDK emits.
    """
    recorder = SentryEnvelopeRecorder()
    monkeypatch.setattr(HttpTransport, "capture_envelope", recorder.record)
    sentry_sdk.get_isolation_scope().clear_breadcrumbs()
    yield recorder
    sentry_sdk.get_client().close()
    sentry_sdk.get_global_scope().set_client(None)
    sentry_sdk.get_isolation_scope().clear()


def create_reporting_app(db_session: Session) -> FastAPI:
    """Build an app that reports errors, with the test database session.

    Use together with the ``sentry_transport`` fixture so reports stay in
    memory.

    Args:
        db_session: Database session the app should use.

    Returns:
        FastAPI: The configured application.
    """
    app: FastAPI = create_app(
        Settings(
            rate_limit=RateLimitSettings(enabled=False),
            observability=ObservabilitySettings(
                sentry_dsn="https://public@sentry.invalid/1",
            ),
        ),
    )

    def override_get_db() -> Iterator[Session]:
        """Yield the test database session in place of the real one."""
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    return app

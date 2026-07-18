"""Shared pytest fixtures."""

from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from podex.database import enable_sqlite_foreign_keys, get_db
from podex.main import create_app
from podex.models import Base, Episode, Media, MediaType, Mention, Podcast


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


@pytest.fixture
def db_session() -> Iterator[Session]:
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


@pytest.fixture
def client(db_session: Session) -> Iterator[TestClient]:
    """Return a test client backed by the in-memory database session."""
    app = create_app()

    def override_get_db() -> Iterator[Session]:
        """Yield the test database session in place of the real one."""
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client

"""A move is refused when its session copy is stale or a suppression is involved.

A session can hold a stale copy of an episode: another transaction may have
moved it, or taken its podcast down, since it was loaded. The guard locks the
podcasts the move names, then the episode row (on SQLite, the database write
lock first), and reads the podcast the episode is really in. If that differs
from the podcast the session remembers, the move is refused as stale. These
tests use a file database so that two sessions are two connections.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from assertpy import assert_that
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from podex.database import enable_sqlite_foreign_keys
from podex.models import (
    ContentSuppressedError,
    Episode,
    EpisodeSummary,
    Mention,
    Podcast,
    TakedownSubjectType,
)
from podex.models.base import Base
from podex.services.content_suppression import RetryableLockError
from tests.conftest import seed_catalog_graph
from tests.takedown_support import (
    approve_takedown,
    recorded_statements,
    summary_values,
)

_BOTH = ["unpublish_mentions", "suppress_derivatives"]


@pytest.fixture(name="sessions")
def sessions_fixture(tmp_path: Path) -> Iterator[Callable[[], Session]]:
    """Sessions on one file database, each on its own connection."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'moves.db'}",
        connect_args={"timeout": 0.2},
    )
    enable_sqlite_foreign_keys(engine)
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, autoflush=False)
    engine.dispose()


@dataclass(frozen=True, slots=True)
class _Catalog:
    """An episode in an open podcast and two more open podcasts."""

    episode: int
    media: int
    first: int
    second: int
    third: int


def _catalog(sessions: Callable[[], Session]) -> _Catalog:
    with sessions() as db:
        graph = seed_catalog_graph(db)
        podcasts = [
            Podcast(name=name, slug=name.lower()) for name in ("Second", "Third")
        ]
        db.add_all(podcasts)
        db.commit()
        return _Catalog(
            episode=graph.episode_id,
            media=graph.media_id,
            first=graph.podcast_id,
            second=podcasts[0].id,
            third=podcasts[1].id,
        )


def _move(sessions: Callable[[], Session], episode_id: int, podcast_id: int) -> None:
    with sessions() as db:
        db.get_one(Episode, episode_id).podcast_id = podcast_id
        db.commit()


def _engine(db: Session) -> Engine:
    engine = db.get_bind()
    if not isinstance(engine, Engine):
        raise TypeError(type(engine))
    return engine


def _assert_stale_move_refused(
    sessions: Callable[[], Session],
    stale: Session,
    episode: Episode,
    catalog: _Catalog,
) -> None:
    """The stale copy's move is refused and the episode stays where it is."""
    episode.podcast_id = catalog.third
    with pytest.raises(RetryableLockError, match=f"Episode {catalog.episode} "):
        stale.commit()
    stale.rollback()
    stale.close()
    with sessions() as db:
        assert_that(db.get_one(Episode, catalog.episode).podcast_id).is_equal_to(
            catalog.second,
        )


def test_stale_copy_of_a_moved_episode_is_refused(
    sessions: Callable[[], Session],
) -> None:
    """Scenario A: another session moved the episode after this one loaded it."""
    catalog = _catalog(sessions)
    stale = sessions()
    episode = stale.get_one(Episode, catalog.episode)
    _move(sessions, catalog.episode, catalog.second)

    _assert_stale_move_refused(sessions, stale, episode, catalog)


def test_stale_copy_after_a_takedown_of_the_new_podcast_is_refused(
    sessions: Callable[[], Session],
) -> None:
    """Scenario B: the episode was moved, then its new podcast taken down."""
    catalog = _catalog(sessions)
    stale = sessions()
    episode = stale.get_one(Episode, catalog.episode)
    _move(sessions, catalog.episode, catalog.second)
    with sessions() as db:
        approve_takedown(
            db,
            subject_type=TakedownSubjectType.PODCAST,
            subject_id=catalog.second,
            actions=_BOTH,
        )

    _assert_stale_move_refused(sessions, stale, episode, catalog)
    with sessions() as db:
        db.add(Mention(episode_id=catalog.episode, media_id=catalog.media))
        with pytest.raises(ContentSuppressedError):
            db.commit()


def test_expired_episode_moves_between_open_podcasts(
    sessions: Callable[[], Session],
) -> None:
    """A commit expired the copy; the move still knows the podcast it leaves.

    Assigning the column loads the old value, so the remembered origin is the
    persisted one and the move is not mistaken for a stale copy.
    """
    catalog = _catalog(sessions)
    with sessions() as db:
        episode = db.get_one(Episode, catalog.episode)
        db.commit()
        episode.podcast_id = catalog.second
        db.commit()

    with sessions() as db:
        assert_that(db.get_one(Episode, catalog.episode).podcast_id).is_equal_to(
            catalog.second,
        )


def test_move_holds_the_sqlite_write_lock_before_reading_coverage(
    sessions: Callable[[], Session],
) -> None:
    """A takedown cannot commit between the coverage read and the move's write.

    The takedown of the podcast the episode leaves is attempted right after
    the move read that podcast's suppressions. It must find the database
    locked; otherwise it would commit and the move would leave it behind.
    """
    catalog = _catalog(sessions)
    engine = _engine(sessions())
    refused: list[str] = []

    def take_down_origin(*args: object) -> None:
        statement = str(args[2])
        if refused or "FROM content_suppressions" not in statement:
            return
        if "subject_type" not in statement:
            return
        refused.append("tried")
        with sessions() as other:
            try:
                approve_takedown(
                    other,
                    subject_type=TakedownSubjectType.PODCAST,
                    subject_id=catalog.first,
                    actions=_BOTH,
                )
            except (OperationalError, ValueError) as error:
                refused.append(str(error))

    event.listen(engine, "after_cursor_execute", take_down_origin)
    try:
        _move(sessions, catalog.episode, catalog.second)
    finally:
        event.remove(engine, "after_cursor_execute", take_down_origin)

    assert_that(refused).is_length(2)
    assert_that(refused[1]).contains("database is locked")


def test_lock_cache_is_dropped_when_the_flush_ends(db_session: Session) -> None:
    """A later flush locks the episode's podcast again: it may have moved since."""
    graph = seed_catalog_graph(db_session)
    db_session.add(Mention(episode_id=graph.episode_id, media_id=graph.media_id))
    db_session.flush()
    db_session.add(
        EpisodeSummary(**summary_values(graph.episode_id, "later", "summary")),
    )

    with recorded_statements(_engine(db_session)) as statements:
        db_session.flush()

    assert_that(
        [item for item in statements if "FROM podcasts JOIN episodes" in item],
    ).is_length(1)

"""An episode move carries from the podcast the database says it leaves.

A session can hold a stale copy of an episode: another transaction may have
moved it, or taken its podcast down, since it was loaded. The guard locks the
episode row (on SQLite, the database write lock) and reads its persisted
podcast before carrying, so the carry always comes from the podcast the
episode really leaves. These tests use a file database so that two sessions
are two connections.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from assertpy import assert_that
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from podex.database import enable_sqlite_foreign_keys
from podex.models import (
    ContentSuppressedError,
    ContentSuppression,
    ContentSuppressionScope,
    ContentSuppressionVersion,
    Episode,
    EpisodeSummary,
    Mention,
    Podcast,
    TakedownSubjectType,
    Transcript,
)
from podex.models.base import Base
from podex.services.content_suppression import (
    count_published_mentions,
    is_episode_suppressed,
)
from podex.services.takedown_requests import execute_approved_takedown_request
from tests.conftest import seed_catalog_graph
from tests.takedown_support import (
    approve_takedown,
    recorded_statements,
    submit_takedown,
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
    """An episode in an open podcast, two more open podcasts, a suppressed one."""

    episode: int
    media: int
    first: int
    second: int
    third: int
    suppressed: int


def _catalog(sessions: Callable[[], Session]) -> _Catalog:
    with sessions() as db:
        graph = seed_catalog_graph(db)
        podcasts = [
            Podcast(name=name, slug=name.lower())
            for name in ("Second", "Third", "Suppressed")
        ]
        db.add_all(podcasts)
        db.commit()
        approve_takedown(
            db,
            subject_type=TakedownSubjectType.PODCAST,
            subject_id=podcasts[2].id,
            actions=_BOTH,
        )
        return _Catalog(
            episode=graph.episode_id,
            media=graph.media_id,
            first=graph.podcast_id,
            second=podcasts[0].id,
            third=podcasts[1].id,
            suppressed=podcasts[2].id,
        )


def _move(sessions: Callable[[], Session], episode_id: int, podcast_id: int) -> None:
    with sessions() as db:
        db.get_one(Episode, episode_id).podcast_id = podcast_id
        db.commit()


def _assert_stays_taken_down(
    sessions: Callable[[], Session], catalog: _Catalog
) -> None:
    """No scope is writable or visible for the episode."""
    with sessions() as db:
        for scope in ContentSuppressionScope:
            assert_that(
                is_episode_suppressed(db=db, episode_id=catalog.episode, scope=scope),
            ).described_as(scope.value).is_true()
        assert_that(
            count_published_mentions(db, Mention.episode_id == catalog.episode),
        ).is_zero()
    writes = [
        Mention(episode_id=catalog.episode, media_id=catalog.media),
        EpisodeSummary(**summary_values(catalog.episode, "k", "summary")),
        Transcript(
            episode_id=catalog.episode,
            provider="podscripts",
            cleaned_text="cleaned",
        ),
    ]
    for row in writes:
        with sessions() as db:
            db.add(row)
            with pytest.raises(ContentSuppressedError):
                db.commit()


def test_stale_copy_moved_out_of_a_podcast_it_never_saw_carries_it(
    sessions: Callable[[], Session],
) -> None:
    """Scenario A: another session moved the episode into a suppressed podcast.

    The stale session still believes the episode is in its first podcast; the
    carry must come from the suppressed podcast it actually leaves.
    """
    catalog = _catalog(sessions)
    stale = sessions()
    episode = stale.get_one(Episode, catalog.episode)
    _move(sessions, catalog.episode, catalog.suppressed)

    episode.podcast_id = catalog.third
    stale.commit()
    stale.close()

    _assert_stays_taken_down(sessions, catalog)


def test_stale_copy_keeps_a_takedown_of_the_podcast_it_was_moved_to(
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

    episode.podcast_id = catalog.third
    stale.commit()
    stale.close()

    _assert_stays_taken_down(sessions, catalog)


def test_expired_episode_moves_without_being_reloaded(
    sessions: Callable[[], Session],
) -> None:
    """A move of an episode a commit expired carries from its persisted podcast."""
    catalog = _catalog(sessions)
    _move(sessions, catalog.episode, catalog.suppressed)
    with sessions() as db:
        episode = db.get_one(Episode, catalog.episode)
        db.commit()
        with recorded_statements(_engine(db)) as before_flush:
            episode.podcast_id = catalog.third
        assert_that(before_flush).is_empty()
        db.commit()

    _assert_stays_taken_down(sessions, catalog)


def _engine(db: Session) -> Engine:
    engine = db.get_bind()
    if not isinstance(engine, Engine):
        raise TypeError(type(engine))
    return engine


def test_move_holds_the_sqlite_write_lock_before_reading_what_to_carry(
    sessions: Callable[[], Session],
) -> None:
    """A takedown cannot commit between the carry's read and the move's write.

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


def test_takedown_executed_with_a_pending_move_carries_its_suppression(
    db_session: Session,
) -> None:
    """Executing the takedown flushes the move; the carry sees the new row."""
    graph = seed_catalog_graph(db_session)
    other = Podcast(name="Other", slug="other")
    db_session.add(other)
    db_session.commit()
    request = submit_takedown(
        db_session,
        subject_type=TakedownSubjectType.PODCAST,
        subject_id=graph.podcast_id,
        actions=_BOTH,
    )
    db_session.get_one(Episode, graph.episode_id).podcast_id = other.id

    result = execute_approved_takedown_request(
        db=db_session,
        request=request,
        artifact_store=None,
    )
    db_session.commit()

    assert_that(result.episode_ids).contains(graph.episode_id)
    for scope in ContentSuppressionScope:
        assert_that(
            is_episode_suppressed(
                db=db_session,
                episode_id=graph.episode_id,
                scope=scope,
            ),
        ).described_as(scope.value).is_true()


def _version(db: Session) -> int:
    row = db.get_one(ContentSuppressionVersion, 1)
    db.refresh(row)
    version: int = row.version
    return version


@pytest.mark.parametrize("direction", ["into", "out-of"])
def test_move_that_changes_coverage_bumps_the_change_counter(
    client: TestClient,
    db_session: Session,
    direction: str,
) -> None:
    """Cached stats are recomputed after a move into or out of a takedown."""
    graph = seed_catalog_graph(db_session)
    suppressed = Podcast(name="Suppressed", slug="suppressed")
    db_session.add(suppressed)
    db_session.commit()
    approve_takedown(
        db_session,
        subject_type=TakedownSubjectType.PODCAST,
        subject_id=suppressed.id if direction == "into" else graph.podcast_id,
        actions=["unpublish_mentions"],
    )
    before = _version(db_session)
    expected = 1 if direction == "into" else 0
    assert_that(client.get("/api/v2/stats").json()["mentions"]).is_equal_to(expected)

    destination = suppressed.id if direction == "into" else _open(db_session)
    db_session.get_one(Episode, graph.episode_id).podcast_id = destination
    db_session.commit()

    assert_that(_version(db_session)).is_equal_to(before + 1)
    assert_that(client.get("/api/v2/stats").json()["mentions"]).is_zero()


def _open(db: Session) -> int:
    podcast = Podcast(name="Open", slug="open")
    db.add(podcast)
    db.commit()
    podcast_id: int = podcast.id
    return podcast_id


def test_move_between_open_podcasts_does_not_bump_the_counter(
    db_session: Session,
) -> None:
    """A move no suppression is involved in leaves the counter alone."""
    graph = seed_catalog_graph(db_session)
    destination = _open(db_session)
    before = _version(db_session)

    db_session.get_one(Episode, graph.episode_id).podcast_id = destination
    db_session.commit()

    assert_that(_version(db_session)).is_equal_to(before)


def test_each_scope_carries_its_own_takedown_case(db_session: Session) -> None:
    """Two cases suppress the origin for different scopes; each keeps its case."""
    graph = seed_catalog_graph(db_session)
    destination = _open(db_session)
    cases = {}
    for action, scope in (
        ("unpublish_mentions", "mentions"),
        ("suppress_derivatives", "derivatives"),
    ):
        request = submit_takedown(
            db_session,
            subject_type=TakedownSubjectType.PODCAST,
            subject_id=graph.podcast_id,
            actions=[action],
        )
        execute_approved_takedown_request(
            db=db_session,
            request=request,
            artifact_store=None,
        )
        db_session.commit()
        cases[scope] = request.id

    db_session.get_one(Episode, graph.episode_id).podcast_id = destination
    db_session.commit()

    carried = {
        row.scope: row.takedown_request_id
        for row in db_session.query(ContentSuppression).filter(
            ContentSuppression.episode_id == graph.episode_id,
        )
    }
    assert_that(carried).is_equal_to(cases)
    assert_that(cases["mentions"]).is_not_equal_to(cases["derivatives"])


def test_one_flush_carries_from_every_origin(db_session: Session) -> None:
    """Episodes leaving two suppressed podcasts each carry their own origin's rows."""
    graph = seed_catalog_graph(db_session)
    second = Podcast(name="Second", slug="second")
    db_session.add(second)
    db_session.commit()
    other = Episode(podcast_id=second.id, title="Other")
    db_session.add(other)
    db_session.commit()
    for podcast_id, action in (
        (graph.podcast_id, "unpublish_mentions"),
        (second.id, "suppress_derivatives"),
    ):
        approve_takedown(
            db_session,
            subject_type=TakedownSubjectType.PODCAST,
            subject_id=podcast_id,
            actions=[action],
        )
    destination = _open(db_session)

    db_session.get_one(Episode, graph.episode_id).podcast_id = destination
    other.podcast_id = destination
    db_session.commit()

    carried = sorted(
        (row.episode_id, row.scope)
        for row in db_session.query(ContentSuppression).filter(
            ContentSuppression.subject_type == "episode",
        )
    )
    assert_that(carried).is_equal_to(
        sorted([(graph.episode_id, "mentions"), (other.id, "derivatives")]),
    )


def test_moved_episode_is_locked_again_at_its_new_podcast(
    db_session: Session,
) -> None:
    """A guarded row written in the moving flush locks the podcast it joins.

    The pre-write lock covered the podcast the episode left; after the write
    the episode is dropped from the lock cache and its new podcast is locked.
    """
    graph = seed_catalog_graph(db_session)
    destination = _open(db_session)
    db_session.get_one(Episode, graph.episode_id).podcast_id = destination
    db_session.add(Mention(episode_id=graph.episode_id, media_id=graph.media_id))

    with recorded_statements(_engine(db_session)) as statements:
        db_session.flush()

    podcast_locks = [
        statement
        for statement in statements
        if "FROM podcasts JOIN episodes" in statement
    ]
    assert_that(podcast_locks).is_length(2)


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

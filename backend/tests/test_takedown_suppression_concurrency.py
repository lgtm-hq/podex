"""A takedown approved while a rebuild or publication is in flight wins, on SQLite.

Two sessions on separate connections to one SQLite file play the takedown
(``approver``) and the rebuild (``rebuilder``). Each test fixes the order of
their statements explicitly. SQLite has one writer, so the second writer either
fails fast on the write lock (``timeout`` is short) or, in the threaded case,
waits for the first to commit; the PostgreSQL lane covers row-lock waits.
"""

import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from podex.database import enable_sqlite_foreign_keys
from podex.models import (
    Base,
    ContentSuppressedError,
    DerivativeGenerationRun,
    Episode,
    Mention,
    SemanticChunk,
    TakedownSubjectType,
    Transcript,
)
from podex.services.content_suppression import (
    is_mention_pair_suppressed,
)
from podex.services.derivative_generation import generate_episode_derivatives
from podex.services.semantic_chunks import SemanticChunkingPolicy
from podex.services.takedown_requests import execute_approved_takedown_request
from tests.conftest import SeededGraph, seed_catalog_graph
from tests.takedown_support import (
    approve_takedown,
    assert_derivatives_taken_down,
    submit_takedown,
)

_POLICY = SemanticChunkingPolicy(max_words=6, overlap_words=0, min_words=2)
_LOCK_TIMEOUT_SECONDS = 0.2
_WAITING_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True, slots=True)
class _Database:
    """One SQLite file reached through two independent connections."""

    approver: Session
    rebuilder: Session
    rebuilder_engine: Engine
    graph: SeededGraph


def _engine(path: Path, timeout: float) -> Engine:
    """Return an engine on ``path`` that waits ``timeout`` for the write lock."""
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False, "timeout": timeout},
    )
    enable_sqlite_foreign_keys(engine)
    return engine


@pytest.fixture(name="database")
def database_fixture(tmp_path: Path) -> Iterator[_Database]:
    """Seed one file database and open a session per side."""
    path = tmp_path / "suppression.db"
    approver_engine = _engine(path, _LOCK_TIMEOUT_SECONDS)
    rebuilder_engine = _engine(path, _LOCK_TIMEOUT_SECONDS)
    Base.metadata.create_all(approver_engine)
    approver = sessionmaker(bind=approver_engine, autoflush=False)()
    rebuilder = sessionmaker(bind=rebuilder_engine, autoflush=False)()
    graph = seed_catalog_graph(approver)
    approver.add(
        Transcript(
            episode_id=graph.episode_id,
            provider="podscripts",
            raw_text="one two three four five six",
        ),
    )
    approver.commit()
    try:
        yield _Database(
            approver=approver,
            rebuilder=rebuilder,
            rebuilder_engine=rebuilder_engine,
            graph=graph,
        )
    finally:
        approver.close()
        rebuilder.close()
        approver_engine.dispose()
        rebuilder_engine.dispose()


def _rebuild(db: Session, episode_id: int) -> DerivativeGenerationRun:
    """Run derivative generation for one episode without committing."""
    episode = db.query(Episode).filter(Episode.id == episode_id).one()
    return generate_episode_derivatives(
        db=db,
        episode=episode,
        chunking_policy=_POLICY,
    )


def _takedown(db: Session, graph: SeededGraph) -> None:
    """Approve and execute a takedown of the seeded episode; do not commit."""
    request = submit_takedown(
        db,
        subject_type=TakedownSubjectType.EPISODE,
        subject_id=graph.episode_id,
        actions=["suppress_derivatives", "unpublish_mentions"],
    )
    execute_approved_takedown_request(db=db, request=request, artifact_store=None)


def test_publication_checked_before_approval_is_refused_after_it(
    database: _Database,
) -> None:
    """A writer that checked before the approval committed is refused at flush.

    1. The writer checks the pair: not suppressed. It stages a Mention.
    2. The takedown is approved, executed and committed.
    3. The writer commits: the flush guard re-checks and refuses.
    """
    graph = database.graph
    rebuilder = database.rebuilder
    assert_that(
        is_mention_pair_suppressed(
            db=rebuilder,
            episode_id=graph.episode_id,
            media_id=graph.media_id,
        ),
    ).is_false()
    rebuilder.rollback()
    rebuilder.add(Mention(episode_id=graph.episode_id, media_id=graph.media_id))

    _takedown(database.approver, graph)
    database.approver.commit()

    with pytest.raises(ContentSuppressedError):
        rebuilder.commit()
    rebuilder.rollback()
    database.approver.expire_all()
    assert_that(database.approver.query(Mention).count()).is_zero()


def test_takedown_waits_for_a_rebuild_that_wrote_first_then_deletes_it(
    database: _Database,
) -> None:
    """A rebuild that already wrote holds the write lock; the takedown follows it.

    1. The rebuild writes its run and chunks, uncommitted.
    2. The takedown cannot write while the rebuild is open.
    3. The rebuild commits; the takedown runs and deletes what it wrote.
    """
    graph = database.graph
    _rebuild(database.rebuilder, graph.episode_id)

    with pytest.raises(OperationalError, match="database is locked"):
        _takedown(database.approver, graph)
    database.approver.rollback()
    database.rebuilder.commit()
    assert_that(database.rebuilder.query(SemanticChunk).count()).is_positive()

    approve_takedown(
        database.approver,
        subject_type=TakedownSubjectType.EPISODE,
        subject_id=graph.episode_id,
        actions=["suppress_derivatives"],
    )

    assert_derivatives_taken_down(database.approver, graph.episode_id)


def test_rebuild_cannot_write_while_takedown_is_open_and_is_refused_after(
    database: _Database,
) -> None:
    """A takedown that wrote first makes the rebuild fail, then refuse.

    1. The takedown executes, uncommitted.
    2. The rebuild passes its pre-check (the takedown is not committed) and
       fails on the write lock at its first flush.
    3. The takedown commits; a retried rebuild is refused before writing.
    """
    graph = database.graph
    _takedown(database.approver, graph)

    with pytest.raises(OperationalError, match="database is locked"):
        _rebuild(database.rebuilder, graph.episode_id)
    database.rebuilder.rollback()
    database.approver.commit()

    with pytest.raises(ContentSuppressedError):
        _rebuild(database.rebuilder, graph.episode_id)
    database.rebuilder.rollback()
    assert_derivatives_taken_down(database.approver, graph.episode_id)


def _publish_after_check(db: Session, graph: SeededGraph) -> None:
    """Check the pair, then publish a mention of it, as review approval would.

    Raises:
        AssertionError: If the pair is already suppressed at the check.
    """
    if is_mention_pair_suppressed(
        db=db,
        episode_id=graph.episode_id,
        media_id=graph.media_id,
    ):
        raise AssertionError("the check ran after the takedown committed")
    db.add(Mention(episode_id=graph.episode_id, media_id=graph.media_id))
    db.commit()


def test_publication_whose_write_waits_for_the_takedown_commit_is_refused(
    tmp_path: Path,
    database: _Database,
) -> None:
    """The check after the write sees a takedown committed during the wait.

    1. The takedown executes, uncommitted, holding the write lock.
    2. The publisher, which waits for the lock this time, checks the pair (not
       suppressed yet) and reaches its INSERT.
    3. Only then does the takedown commit. The INSERT proceeds once the lock
       is free, and the flush guard, checking after the write, refuses it.
    """
    graph = database.graph
    waiting_engine = _engine(tmp_path / "suppression.db", _WAITING_TIMEOUT_SECONDS)
    waiting = sessionmaker(bind=waiting_engine, autoflush=False)()
    reached_insert = threading.Event()

    def note_insert(*args: Any) -> None:
        if str(args[2]).startswith("INSERT INTO mentions"):
            reached_insert.set()

    event.listen(waiting_engine, "before_cursor_execute", note_insert)
    _takedown(database.approver, graph)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            publication = pool.submit(_publish_after_check, waiting, graph)
            assert_that(reached_insert.wait(_WAITING_TIMEOUT_SECONDS)).is_true()
            database.approver.commit()
            with pytest.raises(ContentSuppressedError):
                publication.result(timeout=_WAITING_TIMEOUT_SECONDS)
        waiting.rollback()
    finally:
        waiting.close()
        waiting_engine.dispose()

    database.approver.expire_all()
    assert_that(database.approver.query(Mention).count()).is_zero()


def test_check_remembered_by_a_session_ends_with_its_transaction(
    database: _Database,
) -> None:
    """A pair checked in one transaction is checked again in the next.

    1. The writer publishes a mention of the pair and commits.
    2. The approver takes the pair's episode down and commits.
    3. The writer, in a new transaction on the same session, publishes the
       pair again: refused, not let through on the earlier answer.
    """
    graph = database.graph
    writer = database.rebuilder
    writer.add(Mention(episode_id=graph.episode_id, media_id=graph.media_id))
    writer.commit()
    approve_takedown(
        database.approver,
        subject_type=TakedownSubjectType.EPISODE,
        subject_id=graph.episode_id,
        actions=["unpublish_mentions"],
    )

    writer.add(Mention(episode_id=graph.episode_id, media_id=graph.media_id))
    with pytest.raises(ContentSuppressedError):
        writer.commit()
    writer.rollback()

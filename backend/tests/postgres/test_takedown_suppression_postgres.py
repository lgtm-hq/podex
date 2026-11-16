"""PostgreSQL races between a takedown and a derivative rebuild.

A takedown locks the covered podcast rows ``FOR UPDATE`` before it reads what
to delete; a guarded writer locks them ``FOR SHARE`` before it writes. Each
test holds one side open and starts the other, which must wait on that lock
(observed from the server, see ``tests/postgres/concurrency.py``). Either order
ends with the takedown in force and no derived rows. Each race also runs with
its production lock removed and must then fail.
"""

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial

import pytest
from assertpy import assert_that
from sqlalchemy import event
from sqlalchemy.orm import Session

from podex.models import (
    ContentSuppressedError,
    Episode,
    TakedownSubjectType,
    Transcript,
    suppression_guard,
)
from podex.services import takedown_requests
from podex.services.derivative_generation import generate_episode_derivatives
from podex.services.semantic_chunks import SemanticChunkingPolicy
from podex.services.takedown_requests import execute_approved_takedown_request
from tests.conftest import seed_catalog_graph
from tests.postgres.concurrency import (
    LOCK_WAIT_TIMEOUT_SECONDS,
    ExpectedWait,
    backend_pid,
    row_lock_wait,
    run_overlapping,
    wait_for_expected_lock,
)
from tests.postgres.conftest import PostgresDatabase
from tests.takedown_support import assert_derivatives_taken_down, submit_takedown

pytestmark = pytest.mark.postgres

_POLICY = SemanticChunkingPolicy(max_words=6, overlap_words=0, min_words=2)

_WRITER_WAITS = ExpectedWait(
    statement_patterns=(r"\bFROM podcasts\b", r"\bFOR SHARE\b"),
    row_lock_table="podcasts",
)
_TAKEDOWN_WAITS = row_lock_wait("podcasts")


def _seed(postgres: PostgresDatabase) -> int:
    """Seed a catalog episode with a chunkable transcript.

    Returns:
        The episode id.
    """
    with postgres.sessions() as db:
        graph = seed_catalog_graph(db)
        db.add(
            Transcript(
                episode_id=graph.episode_id,
                provider="podscripts",
                raw_text="one two three four five six",
            ),
        )
        db.commit()
        return graph.episode_id


def _rebuild(db: Session, *, episode_id: int) -> int:
    """Generate derivatives for the episode without committing.

    Returns:
        The number of semantic chunks the run created.
    """
    episode = db.query(Episode).filter(Episode.id == episode_id).one()
    run = generate_episode_derivatives(db=db, episode=episode, chunking_policy=_POLICY)
    created: int = run.semantic_chunks_created
    return created


def _takedown(db: Session, *, episode_id: int) -> int:
    """Approve and execute a derivatives takedown without committing.

    Returns:
        The number of derived rows the execution deleted.
    """
    request = submit_takedown(
        db,
        subject_type=TakedownSubjectType.EPISODE,
        subject_id=episode_id,
        actions=["suppress_derivatives"],
    )
    result = execute_approved_takedown_request(
        db=db,
        request=request,
        artifact_store=None,
    )
    deleted: int = result.derivatives_suppressed
    return deleted


def _takedown_first(postgres: PostgresDatabase) -> int:
    """Hold an executed takedown open; the rebuild waits, then is refused.

    Returns:
        The episode id.
    """
    episode_id = _seed(postgres)
    with pytest.raises(ContentSuppressedError):
        run_overlapping(
            postgres,
            first=partial(_takedown, episode_id=episode_id),
            second=partial(_rebuild, episode_id=episode_id),
            expected=_WRITER_WAITS,
        )
    return episode_id


def _rebuild_first(postgres: PostgresDatabase) -> int:
    """Hold a written rebuild open; the takedown waits, then deletes its rows.

    Returns:
        The episode id.
    """
    episode_id = _seed(postgres)
    created, deleted = run_overlapping(
        postgres,
        first=partial(_rebuild, episode_id=episode_id),
        second=partial(_takedown, episode_id=episode_id),
        expected=_TAKEDOWN_WAITS,
    )
    assert_that(created).is_equal_to(1)
    assert_that(deleted).is_equal_to(2)
    return episode_id


def test_rebuild_waiting_on_a_takedown_is_refused(postgres: PostgresDatabase) -> None:
    """A rebuild that starts during a takedown waits for it and writes nothing."""
    episode_id = _takedown_first(postgres)
    with postgres.sessions() as db:
        assert_derivatives_taken_down(db, episode_id)


def test_takedown_waiting_on_a_rebuild_deletes_its_rows(
    postgres: PostgresDatabase,
) -> None:
    """A takedown that starts during a rebuild waits, then removes its output."""
    episode_id = _rebuild_first(postgres)
    with postgres.sessions() as db:
        assert_derivatives_taken_down(db, episode_id)


def _skip_lock(**_kwargs: object) -> None:
    """Stand in for a production lock and take none."""


@pytest.mark.parametrize(
    ("scenario", "module", "lock_name"),
    [
        (_takedown_first, suppression_guard, "lock_podcasts_of_episodes"),
        (_rebuild_first, takedown_requests, "lock_podcasts_for_takedown"),
    ],
    ids=["writer-share-lock", "takedown-update-lock"],
)
def test_race_fails_without_its_production_lock(
    postgres: PostgresDatabase,
    monkeypatch: pytest.MonkeyPatch,
    scenario: Callable[[PostgresDatabase], int],
    module: object,
    lock_name: str,
) -> None:
    """Without the lock, the second transaction no longer waits on it.

    The race helper then refuses the run: the rebuild would commit its rows
    next to the suppression, or the takedown would miss them.
    """
    monkeypatch.setattr(module, lock_name, _skip_lock)

    with pytest.raises(AssertionError, match="never waited on the expected lock"):
        scenario(postgres)


def _commit_after(db: Session, step: Callable[[Session], int]) -> int:
    """Run a step and commit it, as a worker or request transaction would.

    Returns:
        The step's result.
    """
    result = step(db)
    db.commit()
    return result


def _rebuild_paused_after_first_write(postgres: PostgresDatabase) -> int:
    """Pause a re-run right after it updates its existing run row.

    The re-run of an already generated episode first updates the stored run
    row. With the guard locking before that write, the takedown started during
    the pause must wait on the podcast lock, not on that run row, so the two
    can never wait on each other.

    Returns:
        The episode id.
    """
    episode_id = _seed(postgres)
    with postgres.sessions() as db:
        _rebuild(db, episode_id=episode_id)
        db.commit()
    paused = threading.Event()
    release = threading.Event()

    def pause_after_run_update(*args: object) -> None:
        if threading.current_thread().name.startswith("rebuild") and str(
            args[2],
        ).startswith("UPDATE derivative_generation_runs"):
            paused.set()
            release.wait(LOCK_WAIT_TIMEOUT_SECONDS)

    rebuilder = postgres.sessions()
    approver = postgres.sessions()
    rebuilder_pid = backend_pid(rebuilder)
    approver_pid = backend_pid(approver)
    event.listen(postgres.engine, "after_cursor_execute", pause_after_run_update)
    try:
        with (
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="rebuild") as rebuilds,
            ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="takedown"
            ) as takedowns,
        ):
            rebuild = rebuilds.submit(
                _commit_after,
                rebuilder,
                partial(_rebuild, episode_id=episode_id),
            )
            assert_that(paused.wait(LOCK_WAIT_TIMEOUT_SECONDS)).is_true()
            takedown = takedowns.submit(
                _commit_after,
                approver,
                partial(_takedown, episode_id=episode_id),
            )
            try:
                wait_for_expected_lock(
                    postgres.engine,
                    waiter=approver_pid,
                    blocker=rebuilder_pid,
                    expected=_TAKEDOWN_WAITS,
                )
            finally:
                release.set()
            rebuild.result(timeout=LOCK_WAIT_TIMEOUT_SECONDS)
            takedown.result(timeout=LOCK_WAIT_TIMEOUT_SECONDS)
    finally:
        event.remove(postgres.engine, "after_cursor_execute", pause_after_run_update)
        rebuilder.close()
        approver.close()
    return episode_id


def test_rebuild_locks_before_its_first_write(postgres: PostgresDatabase) -> None:
    """A re-run holds the podcast lock before writing; the takedown follows it."""
    episode_id = _rebuild_paused_after_first_write(postgres)
    with postgres.sessions() as db:
        assert_derivatives_taken_down(db, episode_id)


def test_rebuild_without_the_pre_write_lock_fails(postgres: PostgresDatabase) -> None:
    """Without the before-flush lock the takedown waits on the run row instead."""
    event.remove(Session, "before_flush", suppression_guard.lock_before_guarded_writes)
    try:
        with pytest.raises(
            AssertionError,
            match="never waited on the expected lock",
        ):
            _rebuild_paused_after_first_write(postgres)
    finally:
        event.listen(
            Session, "before_flush", suppression_guard.lock_before_guarded_writes
        )

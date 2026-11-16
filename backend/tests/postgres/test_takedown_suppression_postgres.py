"""PostgreSQL races between a takedown and the writers it must win against.

Locks, in the shared order (media rows before podcast rows):

- a takedown locks the media records whose summaries quote its text
  ``FOR KEY SHARE``, then the covered podcast rows ``FOR UPDATE``;
- a writer that reads episode text for a provider locks the episodes'
  podcast rows ``FOR SHARE`` before reading, until its transaction ends;
- a guarded flush locks media rows ``FOR KEY SHARE`` and podcast rows
  ``FOR SHARE`` before it writes;
- a media merge locks both records ``FOR UPDATE``.

Each test holds one side open and starts the other, which must wait on the
expected lock (observed from the server, see ``tests/postgres/concurrency.py``).
Every race ends with the takedown in force, and each production lock is also
removed in turn, after which its race must fail.
"""

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any
from unittest.mock import Mock

import pytest
from assertpy import assert_that
from sqlalchemy import event
from sqlalchemy.orm import Session

from podex.models import (
    ContentSuppressedError,
    ContentSuppression,
    DerivativeGenerationRun,
    Episode,
    Media,
    MediaType,
    Mention,
    TakedownSubjectType,
    suppression_guard,
)
from podex.services import content_suppression, takedown_requests
from podex.services.derivative_generation import generate_episode_derivatives
from podex.services.derivative_summaries import generate_episode_summary
from podex.services.ops_media_commands import merge_ops_media
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
from tests.takedown_support import (
    assert_derivatives_taken_down,
    submit_takedown,
    write_episode_summary,
)

pytestmark = pytest.mark.postgres

_WRITER_WAITS = ExpectedWait(
    statement_patterns=(r"\bFROM podcasts\b", r"\bFOR SHARE\b"),
    row_lock_table="podcasts",
)
_TAKEDOWN_WAITS = row_lock_wait("podcasts")
_TAKEDOWN_MEDIA_WAITS = ExpectedWait(
    statement_patterns=(r"\bFROM media\b", r"\bFOR KEY SHARE\b"),
    row_lock_table="media",
)
_MERGE_WAITS = row_lock_wait("media")


class _Dispatched(Exception):
    """Raised by the test provider once it has received the text."""


def _seed(postgres: PostgresDatabase) -> int:
    """Seed a catalog episode without a transcript.

    Its rebuild writes, and on a re-run updates, the episode's derivative run
    row and no semantic chunk, which keeps these races independent of how
    embeddings bind on PostgreSQL.

    Returns:
        The episode id.
    """
    with postgres.sessions() as db:
        graph = seed_catalog_graph(db)
        return graph.episode_id


def _episode(db: Session, episode_id: int) -> Episode:
    return db.query(Episode).filter(Episode.id == episode_id).one()


def _rebuild(db: Session, *, episode_id: int) -> int:
    """Generate derivatives for the episode without committing.

    Returns:
        The id of the derivative run the rebuild wrote.
    """
    run = generate_episode_derivatives(db=db, episode=_episode(db, episode_id))
    run_id: int = run.id
    return run_id


def _direct_write(db: Session, *, episode_id: int) -> int:
    """Write an episode summary with a writer that does not authorise first.

    Only the flush guard's locks order it against a takedown.

    Returns:
        The summary id.
    """
    summary = write_episode_summary(db, _episode(db, episode_id), "written directly")
    summary_id: int = summary.id
    return summary_id


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


def _takedown_first(
    postgres: PostgresDatabase,
    writer: Callable[..., int] = _rebuild,
) -> int:
    """Hold an executed takedown open; the writer waits, then is refused.

    Returns:
        The episode id.
    """
    episode_id = _seed(postgres)
    with pytest.raises(ContentSuppressedError):
        run_overlapping(
            postgres,
            first=partial(_takedown, episode_id=episode_id),
            second=partial(writer, episode_id=episode_id),
            expected=_WRITER_WAITS,
        )
    return episode_id


def _rebuild_first(postgres: PostgresDatabase) -> int:
    """Hold a written rebuild open; the takedown waits, then deletes its rows.

    Returns:
        The episode id.
    """
    episode_id = _seed(postgres)
    run_id, deleted = run_overlapping(
        postgres,
        first=partial(_rebuild, episode_id=episode_id),
        second=partial(_takedown, episode_id=episode_id),
        expected=_TAKEDOWN_WAITS,
    )
    assert_that(run_id).is_positive()
    assert_that(deleted).is_equal_to(1)
    return episode_id


def test_rebuild_waiting_on_a_takedown_is_refused(postgres: PostgresDatabase) -> None:
    """A rebuild that starts during a takedown waits for it and writes nothing."""
    episode_id = _takedown_first(postgres)
    with postgres.sessions() as db:
        assert_derivatives_taken_down(db, episode_id)


def test_direct_writer_waiting_on_a_takedown_is_refused(
    postgres: PostgresDatabase,
) -> None:
    """A writer guarded only by the flush waits for the takedown, then is refused."""
    episode_id = _takedown_first(postgres, writer=_direct_write)
    with postgres.sessions() as db:
        assert_derivatives_taken_down(db, episode_id)


def test_takedown_waiting_on_a_rebuild_deletes_its_rows(
    postgres: PostgresDatabase,
) -> None:
    """A takedown that starts during a rebuild waits, then removes its output."""
    episode_id = _rebuild_first(postgres)
    with postgres.sessions() as db:
        assert_derivatives_taken_down(db, episode_id)


def _summarize_until_dispatched(db: Session, *, episode_id: int, provider: Mock) -> int:
    """Authorise, read the text and call the provider; stop right after the call.

    The transaction stays open with nothing written, so only the dispatch lock
    can make a takedown wait.

    Returns:
        The number of provider calls.
    """
    try:
        generate_episode_summary(
            db=db,
            episode=_episode(db, episode_id),
            provider=provider,
            pipeline_version="v1",
            prompt_version="p1",
        )
    except _Dispatched:
        pass
    calls: int = provider.summarize.call_count
    return calls


def _dispatch_first(postgres: PostgresDatabase) -> int:
    """Hold a provider call's transaction open; the takedown waits for it.

    Returns:
        The episode id.
    """
    episode_id = _seed(postgres)
    provider = Mock(model_name="model")
    provider.summarize.side_effect = _Dispatched
    calls, deleted = run_overlapping(
        postgres,
        first=partial(
            _summarize_until_dispatched,
            episode_id=episode_id,
            provider=provider,
        ),
        second=partial(_takedown, episode_id=episode_id),
        expected=_TAKEDOWN_WAITS,
    )
    assert_that(calls).is_equal_to(1)
    assert_that(deleted).is_zero()
    return episode_id


def test_takedown_waits_for_a_provider_call_to_finish(
    postgres: PostgresDatabase,
) -> None:
    """Text authorised before the takedown leaves before the takedown commits."""
    episode_id = _dispatch_first(postgres)
    with postgres.sessions() as db:
        assert_derivatives_taken_down(db, episode_id)


def test_provider_is_not_called_after_a_takedown_commits(
    postgres: PostgresDatabase,
) -> None:
    """A writer waiting on an open takedown re-checks and never reads the text."""
    episode_id = _seed(postgres)
    provider = Mock(model_name="model")

    with pytest.raises(ContentSuppressedError):
        run_overlapping(
            postgres,
            first=partial(_takedown, episode_id=episode_id),
            second=partial(
                _summarize_until_dispatched,
                episode_id=episode_id,
                provider=provider,
            ),
            expected=_WRITER_WAITS,
        )

    assert_that(provider.summarize.mock_calls).is_empty()


def _seed_merge_pair(postgres: PostgresDatabase) -> tuple[int, int, int, int]:
    """Seed a mention of a duplicate record and its canonical survivor.

    Returns:
        Episode id, duplicate (source) id, survivor (target) id, mention id.
    """
    with postgres.sessions() as db:
        graph = seed_catalog_graph(db)
        duplicate = Media(type=MediaType.BOOK, title="Dune (dupe)")
        db.add(duplicate)
        db.flush()
        mention = Mention(episode_id=graph.episode_id, media_id=duplicate.id)
        db.add(mention)
        db.commit()
        return graph.episode_id, duplicate.id, graph.media_id, mention.id


def _merge(db: Session, *, source_id: int, target_id: int) -> int:
    """Merge the duplicate into the survivor without committing.

    Returns:
        The survivor id.
    """
    merge_ops_media(db=db, source_media_id=source_id, target_media_id=target_id)
    return target_id


def _take_down_mention(db: Session, *, mention_id: int) -> int:
    """Approve and execute a mention takedown without committing.

    Returns:
        The number of mentions unpublished.
    """
    request = submit_takedown(
        db,
        subject_type=TakedownSubjectType.MENTION,
        subject_id=mention_id,
        actions=["unpublish_mentions"],
    )
    result = execute_approved_takedown_request(
        db=db,
        request=request,
        artifact_store=None,
    )
    unpublished: int = result.mentions_unpublished
    return unpublished


def _assert_pair_suppressed(
    postgres: PostgresDatabase,
    *,
    episode_id: int,
    target_id: int,
) -> None:
    """The survivor's pair is suppressed and none of its mentions is published."""
    with postgres.sessions() as db:
        keys = [row.suppression_key for row in db.query(ContentSuppression)]
        assert_that(keys).is_equal_to(
            [f"mention_pair:{episode_id}:{target_id}:mentions"]
        )
        assert_that(
            content_suppression.count_published_mentions(
                db,
                Mention.media_id == target_id,
            ),
        ).is_zero()


def _merge_first(postgres: PostgresDatabase) -> tuple[int, int, int]:
    """Hold a merge open; the mention takedown waits on the media lock.

    After the merge commits the takedown finds its mention moved to another
    record it did not lock, refuses, and succeeds when retried.

    Returns:
        Episode id, survivor id and the retried takedown's unpublished count.
    """
    episode_id, source_id, target_id, mention_id = _seed_merge_pair(postgres)
    with pytest.raises(ValueError, match="retry"):
        run_overlapping(
            postgres,
            first=partial(_merge, source_id=source_id, target_id=target_id),
            second=partial(_take_down_mention, mention_id=mention_id),
            expected=_TAKEDOWN_MEDIA_WAITS,
        )
    with postgres.sessions() as db:
        unpublished = _take_down_mention(db, mention_id=mention_id)
        db.commit()
    return episode_id, target_id, unpublished


def test_mention_takedown_waits_for_a_merge_of_its_media(
    postgres: PostgresDatabase,
) -> None:
    """Takedown and merge take media locks first; neither deadlocks."""
    episode_id, target_id, unpublished = _merge_first(postgres)

    assert_that(unpublished).is_equal_to(1)
    _assert_pair_suppressed(postgres, episode_id=episode_id, target_id=target_id)


def test_merge_waits_for_a_mention_takedown(postgres: PostgresDatabase) -> None:
    """A merge started during a mention takedown waits, then carries its pair."""
    episode_id, source_id, target_id, mention_id = _seed_merge_pair(postgres)

    unpublished, _ = run_overlapping(
        postgres,
        first=partial(_take_down_mention, mention_id=mention_id),
        second=partial(_merge, source_id=source_id, target_id=target_id),
        expected=_MERGE_WAITS,
    )

    assert_that(unpublished).is_equal_to(1)
    _assert_pair_suppressed(postgres, episode_id=episode_id, target_id=target_id)


def _skip_lock(**_kwargs: object) -> None:
    """Stand in for a production lock and take none."""


@pytest.mark.parametrize(
    ("scenario", "module", "lock_name"),
    [
        (
            partial(_takedown_first, writer=_direct_write),
            suppression_guard,
            "lock_podcasts_of_episodes",
        ),
        (_rebuild_first, takedown_requests, "lock_podcasts_for_takedown"),
        (_dispatch_first, content_suppression, "lock_episode_text"),
        (_merge_first, takedown_requests, "lock_media_for_takedown"),
    ],
    ids=[
        "writer-share-lock",
        "takedown-update-lock",
        "dispatch-lock",
        "takedown-media-lock",
    ],
)
def test_race_fails_without_its_production_lock(
    postgres: PostgresDatabase,
    monkeypatch: pytest.MonkeyPatch,
    scenario: Callable[[PostgresDatabase], Any],
    module: object,
    lock_name: str,
) -> None:
    """Without the lock, the second transaction no longer waits on it.

    The race helper then refuses the run: the writer would commit next to the
    suppression, the takedown would miss rows, text would reach a provider
    after the takedown committed, or the takedown and a merge could deadlock.
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


def _touch_run(db: Session, *, run_id: int) -> int:
    """Update an existing run row through the ORM, without authorising.

    Returns:
        The run id.
    """
    run = (
        db.query(DerivativeGenerationRun)
        .filter(DerivativeGenerationRun.id == run_id)
        .one()
    )
    run.error_message = "touched"
    db.flush()
    return run_id


def _writer_paused_after_first_write(postgres: PostgresDatabase) -> int:
    """Pause a writer right after it updates a row the takedown will delete.

    With the guard locking before that write, the takedown started during the
    pause waits on the podcast lock, not on that row, so the two can never
    wait on each other.

    Returns:
        The episode id.
    """
    episode_id = _seed(postgres)
    with postgres.sessions() as db:
        run_id = _rebuild(db, episode_id=episode_id)
        db.commit()
    paused = threading.Event()
    release = threading.Event()

    def pause_after_run_update(*args: object) -> None:
        if threading.current_thread().name.startswith("writer") and str(
            args[2],
        ).startswith("UPDATE derivative_generation_runs"):
            paused.set()
            release.wait(LOCK_WAIT_TIMEOUT_SECONDS)

    writer = postgres.sessions()
    approver = postgres.sessions()
    writer_pid = backend_pid(writer)
    approver_pid = backend_pid(approver)
    event.listen(postgres.engine, "after_cursor_execute", pause_after_run_update)
    try:
        with (
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="writer") as writes,
            ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="takedown"
            ) as takedowns,
        ):
            write = writes.submit(
                _commit_after,
                writer,
                partial(_touch_run, run_id=run_id),
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
                    blocker=writer_pid,
                    expected=_TAKEDOWN_WAITS,
                )
            finally:
                release.set()
            write.result(timeout=LOCK_WAIT_TIMEOUT_SECONDS)
            takedown.result(timeout=LOCK_WAIT_TIMEOUT_SECONDS)
    finally:
        event.remove(postgres.engine, "after_cursor_execute", pause_after_run_update)
        writer.close()
        approver.close()
    return episode_id


def test_writer_locks_before_its_first_write(postgres: PostgresDatabase) -> None:
    """A writer holds the podcast lock before writing; the takedown follows it."""
    episode_id = _writer_paused_after_first_write(postgres)
    with postgres.sessions() as db:
        assert_derivatives_taken_down(db, episode_id)


def test_writer_without_the_pre_write_lock_fails(postgres: PostgresDatabase) -> None:
    """Without the before-flush lock the takedown waits on the run row instead."""
    event.remove(Session, "before_flush", suppression_guard.lock_before_guarded_writes)
    try:
        with pytest.raises(
            AssertionError,
            match="never waited on the expected lock",
        ):
            _writer_paused_after_first_write(postgres)
    finally:
        event.listen(
            Session, "before_flush", suppression_guard.lock_before_guarded_writes
        )

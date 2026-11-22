"""Deterministic two-transaction races for PostgreSQL tests.

``run_overlapping`` holds a first transaction open, starts a second one on its
own connection, and commits the first only after the server shows the second
waiting for the lock the test is about:

- ``pg_stat_activity``: the second backend waits on a ``Lock`` while running
  the expected statement (for row locks, the ``SELECT ... FOR UPDATE`` on the
  expected table), so a wait at some later write does not count;
- ``pg_blocking_pids``: the first transaction's backend is what blocks it;
- ``pg_locks``: for row locks, the second backend holds or awaits the tuple
  lock on a row of the expected table.

If that never happens within the timeout, the helper raises with the last
state it saw. The ordering is observed from the server, not assumed from sleeps.

``run_paused`` interleaves two transactions inside their work rather than at
its end: the first runs until it executes a statement matching a pattern and
pauses there, holding its locks; the second runs until it either finishes or
the server shows it waiting behind the first; then the first resumes. Both
commit, or roll back when they raise, and the outcome of each is returned, so
a deadlock or a stale write shows up as that side's error instead of hanging.
"""

import re
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, TypeVar

from sqlalchemy import Engine, event, text
from sqlalchemy.orm import Session

from tests.postgres.conftest import PostgresDatabase

LOCK_WAIT_TIMEOUT_SECONDS = 10.0

FirstResult = TypeVar("FirstResult")
SecondResult = TypeVar("SecondResult")

_OBSERVE = text("""
    SELECT a.wait_event_type,
           a.query,
           pg_blocking_pids(a.pid) AS blockers,
           ARRAY(
               SELECT l.locktype || ':' || coalesce(l.relation::regclass::text, '')
               FROM pg_locks AS l
               WHERE l.pid = a.pid
               ORDER BY 1
           ) AS locks
    FROM pg_stat_activity AS a
    WHERE a.pid = :pid
    """)


@dataclass(frozen=True, slots=True)
class ExpectedWait:
    """The lock wait a race must show before the first transaction commits.

    Attributes:
        statement_patterns: Regular expressions the waiting statement must
            all match.
        row_lock_table: Table whose row the waiter must hold or await a tuple
            lock on, or ``None`` for waits without one (unique-index checks).
    """

    statement_patterns: tuple[str, ...]
    row_lock_table: str | None

    def describe(self) -> str:
        """Return a readable form for failure messages."""
        return (
            f"statement matching {self.statement_patterns!r}, "
            f"row lock on {self.row_lock_table!r}"
        )


def row_lock_wait(table: str) -> ExpectedWait:
    """Expect a ``SELECT ... FOR UPDATE`` on ``table`` blocked on a row lock."""
    return ExpectedWait(
        statement_patterns=(rf"\bFROM {re.escape(table)}\b", r"\bFOR UPDATE\b"),
        row_lock_table=table,
    )


def insert_wait(table: str) -> ExpectedWait:
    """Expect an ``INSERT`` into ``table`` blocked on a uniqueness check."""
    return ExpectedWait(
        statement_patterns=(rf"\bINSERT INTO {re.escape(table)}\b",),
        row_lock_table=None,
    )


@dataclass(frozen=True, slots=True)
class _Observation:
    """One sample of the waiting backend's state."""

    wait_event_type: str | None
    query: str
    blockers: Sequence[int]
    locks: Sequence[str]


def backend_pid(db: Session) -> int:
    """Return the server process id serving ``db``'s connection."""
    return int(db.execute(text("SELECT pg_backend_pid()")).scalar_one())


def _observe(engine: Engine, pid: int) -> _Observation | None:
    """Sample one backend's wait, statement, blockers and locks."""
    # pg_stat_activity is a per-transaction snapshot, so sample in new ones.
    with engine.connect() as connection:
        row: Any = connection.execute(_OBSERVE, {"pid": pid}).one_or_none()
    if row is None:
        return None
    return _Observation(
        wait_event_type=row.wait_event_type,
        query=row.query or "",
        blockers=list(row.blockers or []),
        locks=list(row.locks or []),
    )


def _matches(seen: _Observation, *, blocker: int, expected: ExpectedWait) -> bool:
    """Return whether ``seen`` is the expected wait on ``blocker``."""
    if seen.wait_event_type != "Lock" or blocker not in seen.blockers:
        return False
    if not all(
        re.search(pattern, seen.query) for pattern in expected.statement_patterns
    ):
        return False
    if expected.row_lock_table is None:
        return True
    return f"tuple:{expected.row_lock_table}" in seen.locks


def wait_for_expected_lock(
    engine: Engine,
    *,
    waiter: int,
    blocker: int,
    expected: ExpectedWait,
) -> None:
    """Block until backend ``waiter`` shows ``expected`` behind ``blocker``.

    Raises:
        AssertionError: If that wait is not seen within the timeout.
    """
    deadline = time.monotonic() + LOCK_WAIT_TIMEOUT_SECONDS
    seen: _Observation | None = None
    while time.monotonic() < deadline:
        seen = _observe(engine, waiter)
        if seen is not None and _matches(seen, blocker=blocker, expected=expected):
            return
        time.sleep(0.02)
    msg = (
        f"the second transaction never waited on the expected lock "
        f"({expected.describe()}, blocked by pid {blocker}); last seen: {seen}"
    )
    raise AssertionError(msg)


def _run_then_commit(
    db: Session, step: Callable[[Session], SecondResult]
) -> SecondResult:
    """Run a step and commit it, as a request or worker transaction would.

    Returns:
        The step's result.
    """
    result = step(db)
    db.commit()
    return result


def run_overlapping(
    postgres: PostgresDatabase,
    *,
    first: Callable[[Session], FirstResult],
    second: Callable[[Session], SecondResult],
    expected: ExpectedWait,
    rollback_first: bool = False,
) -> tuple[FirstResult, SecondResult]:
    """Hold ``first`` open, start ``second`` until it shows ``expected``, commit.

    ``first`` runs and stays uncommitted; ``second`` runs on another connection
    in a worker thread and must block on ``expected`` behind ``first``.
    Exceptions from ``second`` propagate to the caller. With
    ``rollback_first``, ``first`` is rolled back instead of committed once
    ``second`` waits.

    Returns:
        The first and second steps' results.
    """
    first_db = postgres.sessions()
    second_db = postgres.sessions()
    first_pid = backend_pid(first_db)
    second_pid = backend_pid(second_db)
    try:
        first_result = first(first_db)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_run_then_commit, second_db, second)
            try:
                wait_for_expected_lock(
                    postgres.engine,
                    waiter=second_pid,
                    blocker=first_pid,
                    expected=expected,
                )
            finally:
                if rollback_first:
                    first_db.rollback()
                else:
                    first_db.commit()
            return first_result, future.result(timeout=LOCK_WAIT_TIMEOUT_SECONDS)
    finally:
        first_db.close()
        second_db.close()


_PAUSED_THREAD = "paused-first"


@dataclass(frozen=True, slots=True)
class StepOutcome:
    """What one side of a paused race returned or raised.

    Attributes:
        result: The step's return value, ``None`` when it raised.
        error: The exception it raised, ``None`` when it committed.
    """

    result: Any
    error: BaseException | None


@dataclass(frozen=True, slots=True)
class PausedRun:
    """Outcome of ``run_paused``.

    Attributes:
        first: The paused transaction's outcome.
        second: The other transaction's outcome.
        second_waited_on: The statement the second transaction was waiting
            on behind the first while the first was paused, or ``None`` when
            it finished without waiting.
    """

    first: StepOutcome
    second: StepOutcome
    second_waited_on: str | None


def _commit_or_roll_back(
    db: Session, step: Callable[[Session], SecondResult]
) -> SecondResult:
    """Run a step and commit it; roll back if it raises, releasing its locks.

    Returns:
        The step's result.
    """
    try:
        return _run_then_commit(db, step)
    finally:
        if db.in_transaction():
            db.rollback()


def _outcome(future: "Future[Any]") -> StepOutcome:
    """Return a finished step's result or error."""
    error = future.exception(timeout=LOCK_WAIT_TIMEOUT_SECONDS)
    if error is not None:
        return StepOutcome(result=None, error=error)
    return StepOutcome(result=future.result(), error=None)


def _finished_or_waiting(
    engine: Engine,
    future: "Future[Any]",
    *,
    waiter: int,
    blocker: int,
) -> str | None:
    """Wait until ``future`` is done or ``waiter`` waits on a lock of ``blocker``.

    Returns:
        The statement ``waiter`` is waiting on, or ``None`` when it finished.

    Raises:
        AssertionError: If neither happens within the timeout.
    """
    deadline = time.monotonic() + LOCK_WAIT_TIMEOUT_SECONDS
    seen: _Observation | None = None
    while time.monotonic() < deadline:
        if future.done():
            return None
        seen = _observe(engine, waiter)
        if (
            seen is not None
            and seen.wait_event_type == "Lock"
            and blocker in seen.blockers
        ):
            return seen.query
        time.sleep(0.02)
    msg = (
        "the second transaction neither finished nor waited behind the paused "
        f"one (pid {blocker}); last seen: {seen}"
    )
    raise AssertionError(msg)


class _Pause:
    """Pause the first thread once, right after a matching statement."""

    def __init__(self, pattern: str) -> None:
        self.pattern = re.compile(pattern, re.DOTALL)
        self.paused = threading.Event()
        self.release = threading.Event()

    def __call__(
        self,
        _conn: object,
        _cursor: object,
        statement: str,
        *_rest: object,
    ) -> None:
        """``after_cursor_execute`` listener: wait here once, in the first thread."""
        if (
            threading.current_thread().name.startswith(_PAUSED_THREAD)
            and not self.paused.is_set()
            and self.pattern.search(statement)
        ):
            self.paused.set()
            self.release.wait(LOCK_WAIT_TIMEOUT_SECONDS)

    def wait_until_paused(self, first: "Future[Any]") -> None:
        """Block until the first thread pauses.

        Raises:
            AssertionError: If it finishes or times out without pausing.
        """
        deadline = time.monotonic() + LOCK_WAIT_TIMEOUT_SECONDS
        while not self.paused.wait(0.02):
            if first.done() or time.monotonic() > deadline:
                self.release.set()
                msg = (
                    "the first transaction never ran a statement matching "
                    f"{self.pattern.pattern!r}; outcome: {_outcome(first)}"
                )
                raise AssertionError(msg)


def run_paused(
    postgres: PostgresDatabase,
    *,
    first: Callable[[Session], FirstResult],
    second: Callable[[Session], SecondResult],
    pause_after: str,
) -> PausedRun:
    """Pause ``first`` after a statement, run ``second`` against it, resume.

    ``first`` runs in a worker thread until it executes a statement matching
    the regular expression ``pause_after`` (searched in the SQL text), and
    waits there with its locks held. ``second`` then runs on another
    connection until it finishes or the server shows it waiting behind
    ``first``. ``first`` then resumes. Each side commits, or rolls back if it
    raises; errors are returned, not raised.

    Returns:
        Both outcomes and the statement the second side waited on.

    Raises:
        AssertionError: If ``first`` ends without running a matching
            statement, or ``second`` neither finishes nor waits behind it.
    """
    pause = _Pause(pause_after)
    sessions = (postgres.sessions(), postgres.sessions())
    pids = tuple(backend_pid(db) for db in sessions)
    event.listen(postgres.engine, "after_cursor_execute", pause)
    try:
        with (
            ThreadPoolExecutor(
                max_workers=1, thread_name_prefix=_PAUSED_THREAD
            ) as firsts,
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="second") as seconds,
        ):
            first_future = firsts.submit(_commit_or_roll_back, sessions[0], first)
            pause.wait_until_paused(first_future)
            second_future = seconds.submit(_commit_or_roll_back, sessions[1], second)
            try:
                waited_on = _finished_or_waiting(
                    postgres.engine,
                    second_future,
                    waiter=pids[1],
                    blocker=pids[0],
                )
            finally:
                pause.release.set()
            return PausedRun(
                first=_outcome(first_future),
                second=_outcome(second_future),
                second_waited_on=waited_on,
            )
    finally:
        pause.release.set()
        event.remove(postgres.engine, "after_cursor_execute", pause)
        for db in sessions:
            db.close()

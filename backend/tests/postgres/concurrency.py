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
"""

import re
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, TypeVar

from sqlalchemy import Engine, text
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
) -> tuple[FirstResult, SecondResult]:
    """Hold ``first`` open, start ``second`` until it shows ``expected``, commit.

    ``first`` runs and stays uncommitted; ``second`` runs on another connection
    in a worker thread and must block on ``expected`` behind ``first``.
    Exceptions from ``second`` propagate to the caller.

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
                first_db.commit()
            return first_result, future.result(timeout=LOCK_WAIT_TIMEOUT_SECONDS)
    finally:
        first_db.close()
        second_db.close()

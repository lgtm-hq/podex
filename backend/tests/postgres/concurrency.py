"""Deterministic two-transaction races for PostgreSQL tests.

``run_overlapping`` holds a first transaction open, starts a second one on its
own connection, waits until PostgreSQL reports the second backend waiting on a
lock (``pg_stat_activity``), and only then commits the first. The ordering is
observed from the server rather than assumed from sleeps.
"""

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from tests.postgres.conftest import PostgresDatabase

LOCK_WAIT_TIMEOUT_SECONDS = 10.0

FirstResult = TypeVar("FirstResult")
SecondResult = TypeVar("SecondResult")


def backend_pid(db: Session) -> int:
    """Return the server process id serving ``db``'s connection."""
    return int(db.execute(text("SELECT pg_backend_pid()")).scalar_one())


def wait_until_lock_wait(engine: Engine, pid: int) -> None:
    """Block until the backend with ``pid`` waits on a lock.

    Raises:
        AssertionError: If the backend never waits on a lock.
    """
    deadline = time.monotonic() + LOCK_WAIT_TIMEOUT_SECONDS
    query = text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid")
    while time.monotonic() < deadline:
        # pg_stat_activity is a per-transaction snapshot, so poll in new ones.
        with engine.connect() as connection:
            wait_type = connection.execute(query, {"pid": pid}).scalar()
        if wait_type == "Lock":
            return
        time.sleep(0.02)
    raise AssertionError("the second transaction never waited on a lock")


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
) -> tuple[FirstResult, SecondResult]:
    """Hold ``first`` open, start ``second`` until it blocks, then commit both.

    ``first`` runs and stays uncommitted; ``second`` runs on another connection
    in a worker thread and must block on a lock ``first`` holds. Exceptions from
    ``second`` propagate to the caller.

    Returns:
        The first and second steps' results.
    """
    first_db = postgres.sessions()
    second_db = postgres.sessions()
    second_pid = backend_pid(second_db)
    try:
        first_result = first(first_db)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_run_then_commit, second_db, second)
            try:
                wait_until_lock_wait(postgres.engine, second_pid)
            finally:
                first_db.commit()
            return first_result, future.result(timeout=LOCK_WAIT_TIMEOUT_SECONDS)
    finally:
        first_db.close()
        second_db.close()

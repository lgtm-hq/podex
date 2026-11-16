"""PostgreSQL concurrency checks for paid API quota consumption.

Concurrent requests at the last unit, or at the month's first unit, only race
on PostgreSQL: SQLite admits one writer at a time. Two-transaction races use
``tests/postgres/concurrency.py``, which commits the first transaction only
after the server shows the second waiting for the lock under test. Each test
runs in its own migrated database from the shared ``postgres`` fixture and runs
in the PostgreSQL CI lane; elsewhere the ``postgres`` marker skips it.
"""

import contextlib
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from functools import partial
from typing import Any

import psycopg.errors
import pytest
from assertpy import assert_that
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from podex.models import AccountQuotaUsage
from podex.services import account_subscriptions
from tests.paid_quota_support import JULY, consume_outcome, seed_paid_account
from tests.postgres.concurrency import (
    LOCK_WAIT_TIMEOUT_SECONDS,
    ExpectedWait,
    insert_wait,
    run_overlapping,
)
from tests.postgres.conftest import PostgresDatabase

pytestmark = pytest.mark.postgres

_AUGUST = datetime(2026, 8, 1, tzinfo=UTC)

# The guarded increment waits for the holder's row lock in its own UPDATE.
_USAGE_UPDATE_WAIT = ExpectedWait(
    statement_patterns=(r"\bUPDATE account_quota_usage\b",),
    row_lock_table="account_quota_usage",
)
# A first consumption waits on the unique key in its savepoint INSERT.
_USAGE_INSERT_WAIT = insert_wait("account_quota_usage")


def _stored(postgres: PostgresDatabase) -> list[tuple[int, str, int]]:
    """Return every committed ``(user_id, period, units)`` usage row."""
    with postgres.sessions() as db:
        rows = db.query(AccountQuotaUsage).order_by(AccountQuotaUsage.id).all()
        return [(row.user_id, row.period, row.units) for row in rows]


def _last_unit_race(
    postgres: PostgresDatabase,
    *,
    used_before: int,
    limit: int,
) -> tuple[int, tuple[str, str]]:
    """Overlap two requests at one remaining unit; the second waits on the row.

    Returns:
        The account id and both requests' outcomes.
    """
    with postgres.sessions() as db:
        user_id = seed_paid_account(
            db, email="last-unit@example.com", units=used_before
        )
    consume = partial(consume_outcome, user_id=user_id, limit=limit)
    outcomes = run_overlapping(
        postgres,
        first=consume,
        second=consume,
        expected=_USAGE_UPDATE_WAIT,
    )
    return user_id, outcomes


@pytest.mark.parametrize(
    ("used_before", "limit"),
    [(0, 1), (4, 5)],
    ids=["single-unit-allocation", "last-of-five"],
)
def test_concurrent_paid_api_quota_last_unit(
    postgres: PostgresDatabase,
    used_before: int,
    limit: int,
) -> None:
    """Of two requests racing for the last unit exactly one gets it."""
    user_id, outcomes = _last_unit_race(
        postgres,
        used_before=used_before,
        limit=limit,
    )

    assert_that(outcomes).is_equal_to((f"used={limit}", "exceeded"))
    assert_that(_stored(postgres)).is_equal_to([(user_id, "2026-07", limit)])


@pytest.mark.parametrize(
    ("limit", "expected_second"),
    [(1, "exceeded"), (2, "used=2")],
    ids=["single-unit-allocation", "two-units"],
)
def test_concurrent_quota_first_row_creation(
    postgres: PostgresDatabase,
    limit: int,
    expected_second: str,
) -> None:
    """Racing first consumptions share one row and raise no conflict."""
    with postgres.sessions() as db:
        user_id = seed_paid_account(db, email="first-row@example.com", units=None)
    consume = partial(consume_outcome, user_id=user_id, limit=limit)

    outcomes = run_overlapping(
        postgres,
        first=consume,
        second=consume,
        expected=_USAGE_INSERT_WAIT,
    )

    assert_that(outcomes).is_equal_to(("used=1", expected_second))
    assert_that(_stored(postgres)).is_equal_to(
        [(user_id, "2026-07", min(limit, 2))],
    )


@pytest.mark.parametrize(
    ("used_before", "expected"),
    [(0, _USAGE_UPDATE_WAIT), (None, _USAGE_INSERT_WAIT)],
    ids=["existing-row", "first-row"],
)
def test_rolled_back_request_releases_the_last_unit_to_a_waiter(
    postgres: PostgresDatabase,
    used_before: int | None,
    expected: ExpectedWait,
) -> None:
    """A unit taken by a transaction that rolls back goes to the next request."""
    with postgres.sessions() as db:
        user_id = seed_paid_account(db, email="rollback@example.com", units=used_before)
    consume = partial(consume_outcome, user_id=user_id, limit=1)

    outcomes = run_overlapping(
        postgres,
        first=consume,
        second=consume,
        expected=expected,
        rollback_first=True,
    )

    assert_that(outcomes).is_equal_to(("used=1", "used=1"))
    assert_that(_stored(postgres)).is_equal_to([(user_id, "2026-07", 1)])


def _consume_after_barrier(
    postgres: PostgresDatabase,
    barrier: threading.Barrier,
    *,
    user_id: int,
    limit: int,
) -> str:
    """Wait for every worker, then consume and commit on a fresh session.

    Returns:
        The request's outcome.
    """
    with postgres.sessions() as db:
        # Connect before the barrier, so starts line up, and turn any lock
        # wait that never ends into an error rather than a hung worker.
        db.execute(text("SET LOCAL lock_timeout = '10s'"))
        barrier.wait(timeout=LOCK_WAIT_TIMEOUT_SECONDS)
        outcome = consume_outcome(db, user_id=user_id, limit=limit)
        db.commit()
        return outcome


@pytest.mark.parametrize(
    ("used_before", "limit"),
    [(1, 6), (None, 5)],
    ids=["existing-row", "first-row"],
)
def test_n_concurrent_requests_take_exactly_the_remaining_units(
    postgres: PostgresDatabase,
    used_before: int | None,
    limit: int,
) -> None:
    """With N requests and R units left, exactly min(N, R) succeed."""
    workers = 8
    with postgres.sessions() as db:
        user_id = seed_paid_account(db, email="n-way@example.com", units=used_before)
    remaining = limit - (used_before or 0)
    barrier = threading.Barrier(workers)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(
                _consume_after_barrier,
                postgres,
                barrier,
                user_id=user_id,
                limit=limit,
            )
            for _ in range(workers)
        ]
        outcomes = [
            future.result(timeout=2 * LOCK_WAIT_TIMEOUT_SECONDS) for future in futures
        ]

    used = sorted(
        int(item.removeprefix("used=")) for item in outcomes if item != "exceeded"
    )
    assert_that(used).is_equal_to(list(range(limit - remaining + 1, limit + 1)))
    assert_that(Counter(outcomes)["exceeded"]).is_equal_to(workers - remaining)
    assert_that(_stored(postgres)).is_equal_to([(user_id, "2026-07", limit)])


@pytest.mark.parametrize(
    ("other_email", "now", "period"),
    [("other@example.com", JULY, "2026-07"), (None, _AUGUST, "2026-08")],
    ids=["other-account", "next-month"],
)
def test_other_accounts_and_months_do_not_wait_for_a_held_unit(
    postgres: PostgresDatabase,
    other_email: str | None,
    now: datetime,
    period: str,
) -> None:
    """Holding one account's last July unit blocks no other allocation."""
    with postgres.sessions() as db:
        holder = seed_paid_account(db, email="holder@example.com", units=0)
        other = (
            holder
            if other_email is None
            else seed_paid_account(db, email=other_email, units=0)
        )
    first_db = postgres.sessions()
    second_db = postgres.sessions()
    try:
        held = consume_outcome(first_db, user_id=holder, limit=1)
        # A wait for the held row would fail here instead of hanging.
        second_db.execute(text("SET LOCAL lock_timeout = '5s'"))
        other_outcome = consume_outcome(second_db, user_id=other, limit=1, now=now)
        second_db.commit()
        first_db.commit()
    finally:
        first_db.close()
        second_db.close()

    assert_that((held, other_outcome)).is_equal_to(("used=1", "used=1"))
    assert_that(
        [row for row in _stored(postgres) if row[0] == other and row[1] == period],
    ).is_equal_to([(other, period, 1)])


def _read_check_write(*, db: Session, key: Any, limit: int) -> int | None:
    """Increment as before the guard: read, check in Python, then write.

    Returns:
        The written units, or ``None`` when the read showed no unit left.
    """
    usage = db.query(AccountQuotaUsage).filter(*key.criteria()).one_or_none()
    if usage is None or usage.units >= limit:
        return None
    usage.units += 1
    db.flush()
    return int(usage.units)


def test_last_unit_race_overconsumes_without_the_guarded_update(
    postgres: PostgresDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The race is real: with read-check-write both requests get the unit.

    The second request reads the usage before the first commits, passes the
    check in Python and overwrites the first increment, so two requests are
    served for one unit and the stored count misses one of them.
    """
    monkeypatch.setattr(account_subscriptions, "_increment_usage", _read_check_write)

    user_id, outcomes = _last_unit_race(postgres, used_before=0, limit=1)

    assert_that(outcomes).is_equal_to(("used=1", "used=1"))
    assert_that(_stored(postgres)).is_equal_to([(user_id, "2026-07", 1)])


def test_first_row_race_conflicts_without_the_savepoint(
    postgres: PostgresDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The race is real: without the savepoint the losing insert fails the request.

    With ``begin_nested`` a no-op the first-row insert is flushed in the
    caller's transaction, outside the conflict handler.
    """
    monkeypatch.setattr(Session, "begin_nested", lambda _self: contextlib.nullcontext())
    with postgres.sessions() as db:
        user_id = seed_paid_account(db, email="first-row-plain@example.com", units=None)
    consume = partial(consume_outcome, user_id=user_id, limit=2)

    with pytest.raises(IntegrityError) as raised:
        run_overlapping(
            postgres,
            first=consume,
            second=consume,
            expected=_USAGE_INSERT_WAIT,
        )

    assert_that(raised.value.orig).is_instance_of(psycopg.errors.UniqueViolation)
    assert_that(_stored(postgres)).is_equal_to([(user_id, "2026-07", 1)])


_INTEGER_MAX = 2**31 - 1


@pytest.mark.parametrize(
    ("limit", "used_before"),
    [(2**31, None), (2**31, 5), (2**63, None), (2**63, 5)],
    ids=[
        "int32-first-row",
        "int32-existing-row",
        "int64-first-row",
        "int64-existing-row",
    ],
)
def test_limits_beyond_the_integer_column_take_the_unit(
    postgres: PostgresDatabase,
    limit: int,
    used_before: int | None,
) -> None:
    """A limit PostgreSQL ``integer`` cannot hold binds and serves the request."""
    with postgres.sessions() as db:
        user_id = seed_paid_account(
            db, email="large-limit@example.com", units=used_before
        )
        outcome = consume_outcome(db, user_id=user_id, limit=limit)
        db.commit()

    expected = (used_before or 0) + 1
    assert_that(outcome).is_equal_to(f"used={expected}")
    assert_that(_stored(postgres)).is_equal_to([(user_id, "2026-07", expected)])


@pytest.mark.parametrize(
    ("used_before", "expected"),
    [(_INTEGER_MAX - 1, f"used={_INTEGER_MAX}"), (_INTEGER_MAX, "exceeded")],
    ids=["reaches-column-maximum", "stops-at-column-maximum"],
)
def test_counter_stops_at_the_integer_column_maximum(
    postgres: PostgresDatabase,
    used_before: int,
    expected: str,
) -> None:
    """At the migrated column's maximum the unit is refused, not overflowed."""
    with postgres.sessions() as db:
        data_type = db.execute(
            text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name = 'account_quota_usage' AND column_name = 'units'",
            ),
        ).scalar_one()
        user_id = seed_paid_account(
            db, email="column-max@example.com", units=used_before
        )
        outcome = consume_outcome(db, user_id=user_id, limit=2**63)
        db.commit()

    assert_that(data_type).is_equal_to("integer")
    assert_that(outcome).is_equal_to(expected)
    assert_that(_stored(postgres)).is_equal_to([(user_id, "2026-07", _INTEGER_MAX)])

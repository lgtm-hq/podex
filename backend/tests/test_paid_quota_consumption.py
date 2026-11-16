"""Atomic consumption of the monthly paid API quota on SQLite.

The interleaving tests use two sessions on one file database. A connection
event runs the first request to completion right before the second request's
first write to ``account_quota_usage``, after every read the second request
makes. SQLite admits one writer at a time, so that is the only point where two
requests can interleave there; PostgreSQL races live in
``tests/postgres/test_paid_quota_postgres.py``.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from podex.config import BillingSettings, Settings
from podex.database import enable_sqlite_foreign_keys
from podex.models import AccountQuotaUsage, AccountUser, Base
from podex.services import account_subscriptions
from podex.services.account_subscriptions import (
    API_REQUESTS,
    AccountQuotaExceededError,
    PaidSubscriptionRequiredError,
    QuotaSnapshot,
    consume_paid_api_request,
    get_quota_snapshot,
)
from tests.paid_quota_support import (
    JULY,
    consume_outcome,
    enforced,
    seed_paid_account,
)

_JULY_END = datetime(2026, 7, 31, 23, 59, 59, 999999, tzinfo=UTC)
_AUGUST_START = datetime(2026, 8, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class _FileDatabase:
    """A SQLite file database that hands out independent sessions."""

    engine: Engine
    sessions: sessionmaker[Session]


@pytest.fixture(name="file_db")
def file_db_fixture(tmp_path: Path) -> Iterator[_FileDatabase]:
    """Provide a file-backed SQLite database for multi-session tests."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'quota.db'}",
        # A writer that cannot get the lock fails within a second, not hangs.
        connect_args={"check_same_thread": False, "timeout": 1},
    )
    enable_sqlite_foreign_keys(engine)
    Base.metadata.create_all(engine)
    try:
        yield _FileDatabase(
            engine=engine,
            sessions=sessionmaker(bind=engine, autoflush=False),
        )
    finally:
        engine.dispose()


def _usage_rows(db: Session) -> list[tuple[int, str, int]]:
    """Return every committed ``(user_id, period, units)`` usage row."""
    db.rollback()
    return [
        (row.user_id, row.period, row.units)
        for row in db.query(AccountQuotaUsage).order_by(AccountQuotaUsage.id).all()
    ]


def _consume(db: Session, *, user_id: int, limit: int, now: datetime = JULY) -> str:
    """Consume one unit like a request, committing on success.

    Returns:
        ``"used=<n>"`` on success or ``"exceeded"`` when the quota is spent.
    """
    outcome = consume_outcome(db, user_id=user_id, limit=limit, now=now)
    db.commit()
    return outcome


def _before_first_quota_write(
    engine: Engine,
    run_first: Callable[[], str],
) -> list[str]:
    """Run ``run_first`` once, right before the next write to the usage table.

    Returns:
        A list that receives the first request's outcome.
    """
    outcomes: list[str] = []

    def before_cursor_execute(*args: Any) -> None:
        statement = str(args[2]).lstrip().upper()
        if outcomes or "ACCOUNT_QUOTA_USAGE" not in statement:
            return
        if statement.startswith(("UPDATE", "INSERT")):
            outcomes.append("pending")
            outcomes[0] = run_first()

    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    return outcomes


def _interleave(
    file_db: _FileDatabase,
    *,
    user_id: int,
    limit: int,
) -> tuple[list[str], str]:
    """Let a first request complete between the second's reads and writes.

    Returns:
        The first request's outcome list and the second request's outcome.
    """
    first_db = file_db.sessions()
    second_db = file_db.sessions()
    try:
        first = _before_first_quota_write(
            file_db.engine,
            lambda: _consume(first_db, user_id=user_id, limit=limit),
        )
        second = _consume(second_db, user_id=user_id, limit=limit)
    finally:
        first_db.close()
        second_db.close()
    return first, second


@pytest.mark.parametrize(
    ("used_before", "limit"),
    [(0, 1), (4, 5)],
    ids=["single-unit-allocation", "last-of-five"],
)
def test_interleaved_requests_cannot_both_take_the_last_unit(
    file_db: _FileDatabase,
    used_before: int,
    limit: int,
) -> None:
    """A request that read the quota before another consumed it is refused."""
    with file_db.sessions() as db:
        user_id = seed_paid_account(db, units=used_before)

    first, second = _interleave(file_db, user_id=user_id, limit=limit)

    assert_that(first).is_equal_to([f"used={limit}"])
    assert_that(second).is_equal_to("exceeded")
    with file_db.sessions() as db:
        assert_that(_usage_rows(db)).is_equal_to([(user_id, "2026-07", limit)])


@pytest.mark.parametrize(
    ("limit", "expected_second"),
    [(1, "exceeded"), (2, "used=2")],
    ids=["single-unit-allocation", "two-units"],
)
def test_interleaved_first_consumption_creates_one_row(
    file_db: _FileDatabase,
    limit: int,
    expected_second: str,
) -> None:
    """Two first-of-the-month requests share one usage row without conflict."""
    with file_db.sessions() as db:
        user_id = seed_paid_account(db)

    first, second = _interleave(file_db, user_id=user_id, limit=limit)

    assert_that(first).is_equal_to(["used=1"])
    assert_that(second).is_equal_to(expected_second)
    with file_db.sessions() as db:
        assert_that(_usage_rows(db)).is_equal_to(
            [(user_id, "2026-07", min(2, limit))],
        )


def test_snapshot_reports_the_stored_count(db_session: Session) -> None:
    """The returned usage is the stored row's count after this unit."""
    user_id = seed_paid_account(db_session, units=3)

    assert_that(_consume(db_session, user_id=user_id, limit=10)).is_equal_to(
        "used=4",
    )
    assert_that(_usage_rows(db_session)).is_equal_to([(user_id, "2026-07", 4)])


def test_consumption_refreshes_a_cached_usage_row(db_session: Session) -> None:
    """A usage row already loaded in the session shows the new count."""
    user_id = seed_paid_account(db_session, units=1)
    settings = enforced(5)
    before = get_quota_snapshot(
        db=db_session,
        user_id=user_id,
        settings=settings,
        feature=API_REQUESTS,
        now=JULY,
    )

    consume_paid_api_request(
        db=db_session, user_id=user_id, settings=settings, now=JULY
    )
    after = get_quota_snapshot(
        db=db_session,
        user_id=user_id,
        settings=settings,
        feature=API_REQUESTS,
        now=JULY,
    )

    assert_that((before.used, after.used, after.remaining)).is_equal_to((1, 2, 3))


@pytest.mark.parametrize(
    ("limit", "used_before"),
    [(0, None), (0, 3), (-1, None), (5, 5), (5, 7)],
    ids=[
        "zero-allocation",
        "zero-allocation-after-use",
        "negative-allocation",
        "spent",
        "allocation-lowered-below-use",
    ],
)
def test_spent_or_empty_allocation_is_refused_without_writes(
    db_session: Session,
    limit: int,
    used_before: int | None,
) -> None:
    """A refused unit leaves stored usage as it was and creates no row."""
    user_id = seed_paid_account(db_session, units=used_before)
    expected = [] if used_before is None else [(user_id, "2026-07", used_before)]

    with pytest.raises(AccountQuotaExceededError):
        consume_paid_api_request(
            db=db_session,
            user_id=user_id,
            settings=enforced(limit),
            now=JULY,
        )
    db_session.commit()

    assert_that(_usage_rows(db_session)).is_equal_to(expected)


def test_rolled_back_request_returns_its_unit(db_session: Session) -> None:
    """Consumption is part of the request's transaction."""
    user_id = seed_paid_account(db_session, units=0)

    consume_paid_api_request(
        db=db_session,
        user_id=user_id,
        settings=enforced(1),
        now=JULY,
    )
    db_session.rollback()

    assert_that(_usage_rows(db_session)).is_equal_to([(user_id, "2026-07", 0)])
    assert_that(_consume(db_session, user_id=user_id, limit=1)).is_equal_to(
        "used=1",
    )


def test_month_rollover_starts_a_new_allocation(db_session: Session) -> None:
    """The UTC month boundary splits usage; a spent July leaves August free."""
    user_id = seed_paid_account(db_session, units=2)

    july = _consume(db_session, user_id=user_id, limit=2, now=_JULY_END)
    august = _consume(db_session, user_id=user_id, limit=2, now=_AUGUST_START)

    assert_that((july, august)).is_equal_to(("exceeded", "used=1"))
    assert_that(_usage_rows(db_session)).is_equal_to(
        [(user_id, "2026-07", 2), (user_id, "2026-08", 1)],
    )


def test_accounts_and_features_have_separate_allocations(
    db_session: Session,
) -> None:
    """One account's spent allocation does not limit another's."""
    spent = seed_paid_account(db_session, email="spent@example.com", units=1)
    fresh = seed_paid_account(db_session, email="fresh@example.com")
    db_session.add(
        AccountQuotaUsage(user_id=fresh, period="2026-07", feature="llm_requests"),
    )
    db_session.commit()

    outcomes = (
        _consume(db_session, user_id=spent, limit=1),
        _consume(db_session, user_id=fresh, limit=1),
    )

    assert_that(outcomes).is_equal_to(("exceeded", "used=1"))
    assert_that(
        db_session.query(AccountQuotaUsage.feature, AccountQuotaUsage.units)
        .filter(AccountQuotaUsage.user_id == fresh)
        .order_by(AccountQuotaUsage.feature)
        .all(),
    ).is_equal_to([("api_requests", 1), ("llm_requests", 0)])


def test_disabled_enforcement_and_free_tier_consume_nothing(
    db_session: Session,
) -> None:
    """No unit is recorded when enforcement is off or the account is free."""
    paid = seed_paid_account(db_session, email="paid@example.com")
    free = AccountUser(email="free@example.com")
    db_session.add(free)
    db_session.commit()

    relaxed = consume_paid_api_request(
        db=db_session,
        user_id=paid,
        settings=Settings(billing=BillingSettings(paid_tier_enforced=False)),
        now=JULY,
    )
    with pytest.raises(PaidSubscriptionRequiredError):
        consume_paid_api_request(
            db=db_session,
            user_id=free.id,
            settings=enforced(5),
            now=JULY,
        )
    db_session.rollback()

    assert_that(relaxed).is_none()
    assert_that(_usage_rows(db_session)).is_empty()


def _row_unseen(
    monkeypatch: pytest.MonkeyPatch,
    *,
    checks: int,
    increments: int = 0,
) -> None:
    """Hide the usage row from the first ``checks`` and ``increments`` calls.

    This reproduces on SQLite what a concurrent first consumption causes on
    PostgreSQL: the other request commits the row after this request's
    statement could see it, so this request's insert conflicts.
    """
    helpers = vars(account_subscriptions)
    real_exists = helpers["_usage_row_exists"]
    real_increment = helpers["_increment_usage"]
    misses = {"checks": checks, "increments": increments}

    def usage_row_exists(**kwargs: Any) -> bool:
        if misses["checks"] > 0:
            misses["checks"] -= 1
            return False
        return bool(real_exists(**kwargs))

    def increment_usage(**kwargs: Any) -> int | None:
        if misses["increments"] > 0:
            misses["increments"] -= 1
            return None
        units: int | None = real_increment(**kwargs)
        return units

    monkeypatch.setattr(account_subscriptions, "_usage_row_exists", usage_row_exists)
    monkeypatch.setattr(account_subscriptions, "_increment_usage", increment_usage)


@pytest.mark.parametrize(
    ("used_before", "expected"),
    [(1, "used=2"), (2, "exceeded")],
    ids=["unit-left", "spent"],
)
def test_lost_creation_race_keeps_the_callers_transaction(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    used_before: int,
    expected: str,
) -> None:
    """A conflicting insert is discarded alone; the guard then decides."""
    user_id = seed_paid_account(db_session, units=used_before)
    _row_unseen(monkeypatch, checks=1, increments=1)
    db_session.add(AccountUser(email="pending@example.com"))
    db_session.flush()

    try:
        snapshot: QuotaSnapshot | None = consume_paid_api_request(
            db=db_session,
            user_id=user_id,
            settings=enforced(2),
            now=JULY,
        )
        outcome = f"used={snapshot.used}" if snapshot is not None else "none"
    except AccountQuotaExceededError:
        outcome = "exceeded"

    db_session.commit()

    assert_that(outcome).is_equal_to(expected)
    assert_that(
        db_session.query(AccountUser).filter_by(email="pending@example.com").count(),
    ).is_equal_to(1)
    assert_that(_usage_rows(db_session)).is_equal_to(
        [(user_id, "2026-07", 2)],
    )


def test_integrity_error_other_than_the_race_propagates(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An insert failure is re-raised when no usage row exists afterwards."""
    user_id = seed_paid_account(db_session, units=1)
    _row_unseen(monkeypatch, checks=2)

    with pytest.raises(IntegrityError):
        consume_paid_api_request(
            db=db_session,
            user_id=user_id,
            settings=enforced(1),
            now=JULY,
        )

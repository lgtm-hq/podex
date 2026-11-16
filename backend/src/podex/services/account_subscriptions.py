"""Hosted subscription entitlement and quota policy."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import ColumnElement, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from podex.config import Settings
from podex.models import AccountQuotaUsage, AccountSubscription

QuotaFeature = Literal["api_requests", "llm_requests"]
API_REQUESTS: QuotaFeature = "api_requests"
LLM_REQUESTS: QuotaFeature = "llm_requests"

# Largest count ``account_quota_usage.units`` can hold: the column is
# ``Integer``, a 32-bit ``integer`` on PostgreSQL. The guarded update binds
# ``min(limit, _UNITS_MAX)``, so any configured limit binds as a valid value
# and the counter stops at the column's maximum instead of overflowing it.
_UNITS_MAX = 2**31 - 1


class PaidSubscriptionRequiredError(Exception):
    """Raised when a protected paid feature is unavailable to an account."""


class AccountQuotaExceededError(Exception):
    """Raised when an account has consumed its monthly paid feature allocation."""


@dataclass(frozen=True)
class QuotaSnapshot:
    """Current month's quota totals for personalized write actions."""

    period: str
    feature: str
    limit: int
    used: int

    @property
    def remaining(self) -> int:
        """Return units remaining without exposing a negative value."""
        return max(self.limit - self.used, 0)


def get_account_subscription(*, db: Session, user_id: int) -> AccountSubscription:
    """Return an account's entitlement, creating its free default when absent."""
    subscription = (
        db.query(AccountSubscription)
        .filter(AccountSubscription.user_id == user_id)
        .first()
    )
    if subscription is not None:
        return subscription
    subscription = AccountSubscription(user_id=user_id)
    db.add(subscription)
    db.flush()
    return subscription


def has_paid_access(*, subscription: AccountSubscription) -> bool:
    """Return whether the entitlement permits paid account features."""
    return bool(subscription.tier == "paid" and subscription.status == "active")


@dataclass(frozen=True, slots=True)
class _UsageKey:
    """The unique key of one account's usage row for a feature and month."""

    user_id: int
    period: str
    feature: QuotaFeature

    def criteria(self) -> tuple[ColumnElement[bool], ...]:
        """Return the filter that selects this key's usage row."""
        return (
            AccountQuotaUsage.user_id == self.user_id,
            AccountQuotaUsage.period == self.period,
            AccountQuotaUsage.feature == self.feature,
        )


def _quota_period(now: datetime | None) -> str:
    """Return the billing month, ``YYYY-MM``, that ``now`` falls in."""
    return (now or datetime.now(UTC)).strftime("%Y-%m")


def _quota_limit(*, settings: Settings, feature: QuotaFeature) -> int:
    """Return the monthly allocation configured for ``feature``."""
    limit: int = (
        settings.billing.paid_api_requests_per_month
        if feature == API_REQUESTS
        else settings.billing.paid_llm_requests_per_month
    )
    return limit


def get_quota_snapshot(
    *,
    db: Session,
    user_id: int,
    settings: Settings,
    feature: QuotaFeature,
    now: datetime | None = None,
) -> QuotaSnapshot:
    """Load current usage for a monthly paid-feature allocation."""
    period = _quota_period(now)
    key = _UsageKey(user_id=user_id, period=period, feature=feature)
    usage = db.query(AccountQuotaUsage).filter(*key.criteria()).first()
    return QuotaSnapshot(
        period=period,
        feature=feature,
        limit=_quota_limit(settings=settings, feature=feature),
        used=usage.units if usage is not None else 0,
    )


def _increment_usage(*, db: Session, key: _UsageKey, limit: int) -> int | None:
    """Add one unit to the usage row in one guarded statement.

    The ``units < limit`` guard and the increment are one ``UPDATE``, so the
    database checks the limit against the row it changes. On PostgreSQL a
    concurrent consumer waits for the row lock and re-evaluates the guard on
    the committed row once the holder commits or rolls back; SQLite admits one
    writer at a time. Either way two transactions cannot both take the last
    unit, and neither overwrites the other's increment.

    The bound is clamped to the column's maximum: a configured limit larger
    than the column can store would otherwise fail to bind (PostgreSQL
    ``integer out of range``, SQLite ``OverflowError`` past 64 bits), and
    ``units + 1`` can never exceed the column.

    Returns:
        The stored units after the increment, or ``None`` when no row matched:
        the row does not exist (or this statement cannot see it yet) or its
        allocation is spent.
    """
    statement = (
        update(AccountQuotaUsage)
        .where(*key.criteria(), AccountQuotaUsage.units < min(limit, _UNITS_MAX))
        .values(units=AccountQuotaUsage.units + 1)
        .returning(AccountQuotaUsage.units)
        .execution_options(synchronize_session="fetch")
    )
    units: int | None = db.execute(statement).scalar_one_or_none()
    return units


def _usage_row_exists(*, db: Session, key: _UsageKey) -> bool:
    """Return whether ``key``'s usage row is visible to this transaction."""
    return db.query(AccountQuotaUsage.id).filter(*key.criteria()).first() is not None


def _create_usage_row(*, db: Session, key: _UsageKey) -> None:
    """Create ``key``'s zero-usage row unless it exists or another one wins.

    Concurrent first consumers both insert; the unique key admits one. The
    loser's insert runs in a savepoint, so losing discards only that insert,
    not the caller's transaction, and the winner's row is used instead.

    Raises:
        IntegrityError: If the insert fails and no row exists afterwards, so
            the failure was not a lost creation race.
    """
    if _usage_row_exists(db=db, key=key):
        return
    # Flush first, so the handler below only ever sees the usage insert and
    # cannot hide an integrity error from another pending row.
    db.flush()
    try:
        with db.begin_nested():
            db.add(
                AccountQuotaUsage(
                    user_id=key.user_id,
                    period=key.period,
                    feature=key.feature,
                    units=0,
                ),
            )
    except IntegrityError:
        if not _usage_row_exists(db=db, key=key):
            raise


def consume_paid_api_request(
    *,
    db: Session,
    user_id: int,
    settings: Settings,
    now: datetime | None = None,
) -> QuotaSnapshot | None:
    """Enforce paid access and record one personalized API mutation.

    The unit is taken by a guarded ``UPDATE`` in the caller's transaction:
    the caller's commit makes it durable and a rollback returns it, so a
    refused or failed mutation consumes nothing.

    Returns:
        The month's usage including this unit, or ``None`` when paid
        enforcement is disabled.

    Raises:
        PaidSubscriptionRequiredError: If the account has no active paid
            subscription.
        AccountQuotaExceededError: If the month's allocation is spent.
        RuntimeError: If the month's usage row is gone when the unit is
            retried, so the refusal cannot be attributed to a spent quota.
    """
    if not settings.billing.paid_tier_enforced:
        return None
    subscription = get_account_subscription(db=db, user_id=user_id)
    if not has_paid_access(subscription=subscription):
        raise PaidSubscriptionRequiredError
    key = _UsageKey(user_id=user_id, period=_quota_period(now), feature=API_REQUESTS)
    limit = _quota_limit(settings=settings, feature=API_REQUESTS)
    if limit <= 0:
        raise AccountQuotaExceededError
    used = _increment_usage(db=db, key=key, limit=limit)
    if used is None:
        # Either the month's first unit (no row yet, or one this statement
        # could not see) or a spent allocation: make sure the row exists,
        # then let the guard decide on it.
        _create_usage_row(db=db, key=key)
        used = _increment_usage(db=db, key=key, limit=limit)
    if used is None:
        if not _usage_row_exists(db=db, key=key):
            raise RuntimeError("Paid API quota usage row could not be charged")
        raise AccountQuotaExceededError
    return QuotaSnapshot(
        period=key.period,
        feature=key.feature,
        limit=limit,
        used=used,
    )

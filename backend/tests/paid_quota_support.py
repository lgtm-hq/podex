"""Shared setup for paid API quota tests on SQLite and PostgreSQL."""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from podex.config import BillingSettings, Settings
from podex.models import AccountQuotaUsage, AccountSubscription, AccountUser
from podex.services.account_subscriptions import (
    API_REQUESTS,
    AccountQuotaExceededError,
    consume_paid_api_request,
)

JULY = datetime(2026, 7, 19, tzinfo=UTC)


def enforced(limit: int) -> Settings:
    """Return settings that enforce the paid tier with ``limit`` units a month."""
    return Settings(
        billing=BillingSettings(
            paid_tier_enforced=True,
            paid_api_requests_per_month=limit,
        ),
    )


def seed_paid_account(
    db: Session,
    *,
    email: str = "quota@example.com",
    units: int | None = None,
) -> int:
    """Persist a paid account, optionally with July 2026 usage, and commit.

    Returns:
        The account's id.
    """
    user = AccountUser(email=email)
    db.add(user)
    db.flush()
    db.add(AccountSubscription(user_id=user.id, tier="paid", status="active"))
    if units is not None:
        db.add(
            AccountQuotaUsage(
                user_id=user.id,
                period="2026-07",
                feature=API_REQUESTS,
                units=units,
            ),
        )
    db.commit()
    user_id: int = user.id
    return user_id


def consume_outcome(
    db: Session,
    *,
    user_id: int,
    limit: int,
    now: datetime = JULY,
) -> str:
    """Consume one unit without committing, rolling back a refused request.

    Returns:
        ``"used=<n>"`` on success, or ``"exceeded"`` after rolling back.
    """
    try:
        snapshot = consume_paid_api_request(
            db=db,
            user_id=user_id,
            settings=enforced(limit),
            now=now,
        )
    except AccountQuotaExceededError:
        db.rollback()
        return "exceeded"
    return "disabled" if snapshot is None else f"used={snapshot.used}"

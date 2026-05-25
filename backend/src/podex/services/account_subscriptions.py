"""Hosted subscription entitlement and quota policy."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy.orm import Session

from podex.config import Settings
from podex.models import AccountQuotaUsage, AccountSubscription

QuotaFeature = Literal["api_requests", "llm_requests"]
API_REQUESTS: QuotaFeature = "api_requests"
LLM_REQUESTS: QuotaFeature = "llm_requests"


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
    return subscription.tier == "paid" and subscription.status == "active"


def get_quota_snapshot(
    *,
    db: Session,
    user_id: int,
    settings: Settings,
    feature: QuotaFeature,
    now: datetime | None = None,
) -> QuotaSnapshot:
    """Load current usage for a monthly paid-feature allocation."""
    period = (now or datetime.now(UTC)).strftime("%Y-%m")
    usage = (
        db.query(AccountQuotaUsage)
        .filter(
            AccountQuotaUsage.user_id == user_id,
            AccountQuotaUsage.period == period,
            AccountQuotaUsage.feature == feature,
        )
        .first()
    )
    return QuotaSnapshot(
        period=period,
        feature=feature,
        limit=(
            settings.paid_api_requests_per_month
            if feature == API_REQUESTS
            else settings.paid_llm_requests_per_month
        ),
        used=usage.units if usage is not None else 0,
    )


def consume_paid_api_request(
    *,
    db: Session,
    user_id: int,
    settings: Settings,
    now: datetime | None = None,
) -> QuotaSnapshot | None:
    """Enforce paid access and record one personalized API mutation."""
    if not settings.paid_tier_enforced:
        return None
    subscription = get_account_subscription(db=db, user_id=user_id)
    if not has_paid_access(subscription=subscription):
        raise PaidSubscriptionRequiredError
    snapshot = get_quota_snapshot(
        db=db,
        user_id=user_id,
        settings=settings,
        feature=API_REQUESTS,
        now=now,
    )
    if snapshot.used >= snapshot.limit:
        raise AccountQuotaExceededError
    usage = (
        db.query(AccountQuotaUsage)
        .filter(
            AccountQuotaUsage.user_id == user_id,
            AccountQuotaUsage.period == snapshot.period,
            AccountQuotaUsage.feature == snapshot.feature,
        )
        .first()
    )
    if usage is None:
        usage = AccountQuotaUsage(
            user_id=user_id,
            period=snapshot.period,
            feature=snapshot.feature,
            units=0,
        )
        db.add(usage)
    usage.units += 1
    db.flush()
    return QuotaSnapshot(
        period=snapshot.period,
        feature=snapshot.feature,
        limit=snapshot.limit,
        used=snapshot.used + 1,
    )

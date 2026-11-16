"""Hosted plan entitlement and paid-tier checkout endpoints under ``/me``.

Route handlers resolve the authenticated account from the session cookie,
delegate to the subscription services, and commit the unit of work. The
checkout provider is an injectable dependency (see
:mod:`podex.api.v2.account_deps`).
"""

from fastapi import APIRouter, HTTPException, Request, status

from podex.api.deps import AppSettings, DbSession
from podex.api.v2.account_deps import (
    BillingProviderDep,
    require_authenticated_account,
)
from podex.schemas.account import (
    QuotaRead,
    SubscriptionCheckoutRead,
    SubscriptionRead,
)
from podex.services.account_subscriptions import (
    API_REQUESTS,
    LLM_REQUESTS,
    get_account_subscription,
    get_quota_snapshot,
)

router = APIRouter(tags=["auth"])


def get_current_account_subscription(
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> SubscriptionRead:
    """Return hosted plan entitlement and current monthly usage."""
    user = require_authenticated_account(request=request, db=db, settings=settings)
    subscription = get_account_subscription(db=db, user_id=user.id)
    quotas = [
        get_quota_snapshot(db=db, user_id=user.id, settings=settings, feature=feature)
        for feature in (API_REQUESTS, LLM_REQUESTS)
    ]
    db.commit()
    return SubscriptionRead(
        tier=subscription.tier,
        status=subscription.status,
        paid_features_enforced=settings.billing.paid_tier_enforced,
        current_period_ends_at=subscription.current_period_ends_at,
        quotas=[
            QuotaRead(
                period=quota.period,
                feature=quota.feature,
                limit=quota.limit,
                used=quota.used,
                remaining=quota.remaining,
            )
            for quota in quotas
        ],
    )


def begin_current_account_checkout(
    request: Request,
    db: DbSession,
    settings: AppSettings,
    provider: BillingProviderDep,
) -> SubscriptionCheckoutRead:
    """Start provider-hosted paid-tier checkout once launch gates are enabled."""
    user = require_authenticated_account(request=request, db=db, settings=settings)
    if not settings.billing.paid_tier_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Paid upgrades are not available until launch review is complete",
        )
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Billing checkout is not configured",
        )
    get_account_subscription(db=db, user_id=user.id)
    checkout = provider.create_checkout(
        email=user.email,
        account_reference=str(user.id),
    )
    db.commit()
    return SubscriptionCheckoutRead(
        provider=checkout.provider,
        checkout_url=checkout.checkout_url,
    )


router.add_api_route(
    "/me/subscription",
    get_current_account_subscription,
    methods=["GET"],
    response_model=SubscriptionRead,
)

router.add_api_route(
    "/me/subscription/checkout",
    begin_current_account_checkout,
    methods=["POST"],
    response_model=SubscriptionCheckoutRead,
)

"""Shared dependencies for the account-facing ``/api/v2`` routers.

Provider factories and their ``Annotated`` aliases live here so the sign-in
routes and every ``/me*`` router resolve the same function objects, which is
what ``app.dependency_overrides`` keys on. The session, cookie and paid-quota
helpers are shared by the auth and account routers.
"""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response, status

from podex.api.deps import DbSession
from podex.config import Settings, get_settings
from podex.models import AccountUser
from podex.services.account_auth import get_authenticated_user
from podex.services.account_subscriptions import (
    AccountQuotaExceededError,
    PaidSubscriptionRequiredError,
    consume_paid_api_request,
)
from podex.services.billing_checkout import (
    BillingCheckoutProvider,
    build_billing_checkout_provider,
)
from podex.services.magic_link_delivery import (
    MagicLinkSender,
    build_magic_link_sender,
)
from podex.services.notification_delivery import DigestSender, build_digest_sender
from podex.services.workos_auth import WorkOSAuthClient, build_workos_auth_client


def get_workos_auth_client() -> WorkOSAuthClient | None:
    """Build the configured WorkOS AuthKit client, if credentials exist."""
    return build_workos_auth_client(settings=get_settings())


def get_auth_magic_link_sender() -> MagicLinkSender | None:
    """Build the configured sign-in link sender, if SMTP is available."""
    return build_magic_link_sender(settings=get_settings())


def get_digest_sender() -> DigestSender | None:
    """Build the configured digest sender, if SMTP is available."""
    return build_digest_sender(settings=get_settings())


def get_billing_provider() -> BillingCheckoutProvider | None:
    """Build the configured checkout provider bridge, if billing is set up."""
    return build_billing_checkout_provider(settings=get_settings())


MagicLinkSenderDep = Annotated[
    MagicLinkSender | None,
    Depends(get_auth_magic_link_sender),
]
DigestSenderDep = Annotated[DigestSender | None, Depends(get_digest_sender)]
BillingProviderDep = Annotated[
    BillingCheckoutProvider | None,
    Depends(get_billing_provider),
]
WorkOSClientDep = Annotated[
    WorkOSAuthClient | None,
    Depends(get_workos_auth_client),
]


def require_authenticated_account(
    *,
    request: Request,
    db: DbSession,
    settings: Settings,
) -> AccountUser:
    """Resolve the authenticated account or reject the request."""
    user = get_authenticated_user(
        db=db,
        session_token=request.cookies.get(settings.auth.session_cookie_name),
    )
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    return user


def set_session_cookie(
    *,
    response: Response,
    token: str,
    settings: Settings,
) -> None:
    """Set the secure, http-only account session cookie."""
    response.set_cookie(
        key=settings.auth.session_cookie_name,
        value=token,
        max_age=settings.auth.session_ttl_days * 24 * 60 * 60,
        path="/",
        secure=settings.auth.session_cookie_secure,
        httponly=True,
        samesite="lax",
    )


def consume_paid_personalization_action(
    *,
    db: DbSession,
    user_id: int,
    settings: Settings,
) -> None:
    """Apply paid-feature enforcement and translate quota failures to HTTP."""
    try:
        consume_paid_api_request(db=db, user_id=user_id, settings=settings)
    except PaidSubscriptionRequiredError as error:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="A paid subscription is required for this feature",
        ) from error
    except AccountQuotaExceededError as error:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Monthly personalization quota exceeded",
        ) from error

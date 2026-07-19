"""Passwordless account authentication and personalization endpoints.

Route handlers stay thin: they resolve the authenticated account from the
session cookie, delegate to the account services, and commit the unit of
work. Magic-link and digest email delivery go through injectable sender
boundaries so tests and deployments without SMTP stay deterministic.
"""

from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from podex.api.deps import AppSettings, DbSession
from podex.config import Settings, get_settings
from podex.logging_config import get_logger
from podex.models import AccountUser
from podex.schemas.account import (
    AccountUserRead,
    AlertEvaluationRead,
    AlertEventRead,
    AlertRuleCreateRequest,
    AlertRuleDeleteResponse,
    AlertRuleListRead,
    AlertRuleRead,
    AlertRuleUpdateRequest,
    AuthLogoutResponse,
    AuthMagicLinkRequest,
    AuthMagicLinkRequestResponse,
    AuthMagicLinkVerifyRequest,
    AuthSessionRead,
    DigestListRead,
    DigestRead,
    DigestSendResponse,
    FollowedPodcastDeleteResponse,
    FollowedPodcastListRead,
    FollowedPodcastRead,
    PreferenceRead,
    PreferenceUpdateRequest,
    QuotaRead,
    SavedMediaDeleteResponse,
    SavedMediaListRead,
    SavedMediaRead,
    SubscriptionCheckoutRead,
    SubscriptionRead,
)
from podex.schemas.media import MediaRead
from podex.schemas.podcast import PodcastRead
from podex.services.account_alerts import (
    create_alert_rule,
    delete_alert_rule,
    evaluate_alert_rules,
    list_alert_rules,
    set_alert_rule_enabled,
)
from podex.services.account_auth import (
    authenticate_magic_link,
    get_authenticated_user,
    issue_magic_link,
    revoke_user_session,
)
from podex.services.account_digests import list_account_digests, send_pending_digest
from podex.services.account_follows import (
    follow_podcast,
    list_followed_podcasts,
    unfollow_podcast,
)
from podex.services.account_preferences import (
    get_account_preferences,
    update_account_preferences,
)
from podex.services.account_saves import (
    SavedMediaData,
    list_saved_media,
    remove_saved_media,
    save_media,
)
from podex.services.account_subscriptions import (
    API_REQUESTS,
    LLM_REQUESTS,
    AccountQuotaExceededError,
    PaidSubscriptionRequiredError,
    consume_paid_api_request,
    get_account_subscription,
    get_quota_snapshot,
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

router = APIRouter(tags=["auth"])
logger = get_logger(__name__)


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


def _require_authenticated_account(
    *,
    request: Request,
    db: DbSession,
    settings: Settings,
) -> AccountUser:
    """Resolve the authenticated account or reject the request."""
    user = get_authenticated_user(
        db=db,
        session_token=request.cookies.get(settings.auth_session_cookie_name),
    )
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    return user


def _set_session_cookie(
    *,
    response: Response,
    token: str,
    settings: Settings,
) -> None:
    """Set the secure, http-only account session cookie."""
    response.set_cookie(
        key=settings.auth_session_cookie_name,
        value=token,
        max_age=settings.auth_session_ttl_days * 24 * 60 * 60,
        path="/",
        secure=settings.auth_session_cookie_secure,
        httponly=True,
        samesite="lax",
    )


def _consume_paid_personalization_action(
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


def _to_saved_media_read(*, saved: SavedMediaData) -> SavedMediaRead:
    """Convert a saved-media service result to its API schema."""
    return SavedMediaRead(
        media=MediaRead.model_validate(saved.media),
        saved_at=saved.saved_at,
    )


def request_auth_magic_link(
    payload: AuthMagicLinkRequest,
    db: DbSession,
    settings: AppSettings,
    sender: MagicLinkSenderDep,
) -> AuthMagicLinkRequestResponse:
    """Request delivery of a short-lived, single-use email sign-in link."""
    if sender is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Email sign-in is not configured",
        )
    issued = issue_magic_link(
        db=db,
        email=payload.email,
        redirect_path=payload.redirect_path,
        ttl_minutes=settings.auth_magic_link_ttl_minutes,
    )
    # The token travels in the URL fragment, which browsers never send in
    # Referer headers or request lines, so it stays out of proxy and access
    # logs. Only the redirect path rides in the query string.
    query = (
        f"?{urlencode({'redirect_path': issued.redirect_path})}"
        if issued.redirect_path is not None
        else ""
    )
    fragment = urlencode({"token": issued.token})
    verification_url = (
        f"{settings.public_web_url.rstrip('/')}/auth/verify{query}#{fragment}"
    )
    try:
        sender.send_magic_link(email=issued.email, verification_url=verification_url)
    except Exception as error:
        db.rollback()
        logger.warning("magic_link_delivery_failed: %s", error)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to deliver sign-in link",
        ) from error
    db.commit()
    return AuthMagicLinkRequestResponse()


def verify_auth_magic_link(
    payload: AuthMagicLinkVerifyRequest,
    response: Response,
    db: DbSession,
    settings: AppSettings,
) -> AuthSessionRead:
    """Verify a one-time link token and begin an authenticated session."""
    authenticated = authenticate_magic_link(
        db=db,
        token=payload.token,
        session_ttl_days=settings.auth_session_ttl_days,
    )
    if authenticated is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign-in link is invalid or expired",
        )
    _set_session_cookie(
        response=response,
        token=authenticated.token,
        settings=settings,
    )
    db.commit()
    return AuthSessionRead(
        user=AccountUserRead.model_validate(authenticated.user),
        expires_at=authenticated.expires_at,
    )


def logout_account_session(
    request: Request,
    response: Response,
    db: DbSession,
    settings: AppSettings,
) -> AuthLogoutResponse:
    """Revoke the current account session and expire its browser cookie."""
    signed_out = revoke_user_session(
        db=db,
        session_token=request.cookies.get(settings.auth_session_cookie_name),
    )
    response.delete_cookie(key=settings.auth_session_cookie_name, path="/")
    db.commit()
    return AuthLogoutResponse(signed_out=signed_out)


def get_current_account(
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> AccountUserRead:
    """Return the account represented by the current browser session."""
    user = _require_authenticated_account(request=request, db=db, settings=settings)
    db.commit()
    return AccountUserRead.model_validate(user)


def list_account_saved_media(
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> SavedMediaListRead:
    """List saved public catalog media for the current account."""
    user = _require_authenticated_account(request=request, db=db, settings=settings)
    saved = list_saved_media(db=db, user_id=user.id)
    db.commit()
    return SavedMediaListRead(
        items=[_to_saved_media_read(saved=item) for item in saved],
        total=len(saved),
    )


def put_account_saved_media(
    media_id: int,
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> SavedMediaRead:
    """Idempotently save a public catalog media record."""
    user = _require_authenticated_account(request=request, db=db, settings=settings)
    saved = save_media(db=db, user_id=user.id, media_id=media_id)
    if saved is None:
        raise HTTPException(status_code=404, detail="Media not found")
    _consume_paid_personalization_action(db=db, user_id=user.id, settings=settings)
    db.commit()
    return _to_saved_media_read(saved=saved)


def delete_account_saved_media(
    media_id: int,
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> SavedMediaDeleteResponse:
    """Remove a public catalog media record from the current account's saves."""
    user = _require_authenticated_account(request=request, db=db, settings=settings)
    deleted = remove_saved_media(db=db, user_id=user.id, media_id=media_id)
    db.commit()
    return SavedMediaDeleteResponse(deleted=deleted)


def list_account_followed_podcasts(
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> FollowedPodcastListRead:
    """List followed public podcast sources for the current account."""
    user = _require_authenticated_account(request=request, db=db, settings=settings)
    followed = list_followed_podcasts(db=db, user_id=user.id)
    db.commit()
    return FollowedPodcastListRead(
        items=[
            FollowedPodcastRead(
                podcast=PodcastRead.model_validate(item.podcast),
                followed_at=item.followed_at,
            )
            for item in followed
        ],
        total=len(followed),
    )


def put_account_followed_podcast(
    podcast_id: int,
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> FollowedPodcastRead:
    """Idempotently follow a public podcast source."""
    user = _require_authenticated_account(request=request, db=db, settings=settings)
    followed = follow_podcast(db=db, user_id=user.id, podcast_id=podcast_id)
    if followed is None:
        raise HTTPException(status_code=404, detail="Podcast not found")
    _consume_paid_personalization_action(db=db, user_id=user.id, settings=settings)
    db.commit()
    return FollowedPodcastRead(
        podcast=PodcastRead.model_validate(followed.podcast),
        followed_at=followed.followed_at,
    )


def delete_account_followed_podcast(
    podcast_id: int,
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> FollowedPodcastDeleteResponse:
    """Remove a public podcast source from the current account's follows."""
    user = _require_authenticated_account(request=request, db=db, settings=settings)
    deleted = unfollow_podcast(db=db, user_id=user.id, podcast_id=podcast_id)
    db.commit()
    return FollowedPodcastDeleteResponse(deleted=deleted)


def list_account_alert_rules(
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> AlertRuleListRead:
    """List alert rules belonging to the authenticated account."""
    user = _require_authenticated_account(request=request, db=db, settings=settings)
    rules = list_alert_rules(db=db, user_id=user.id)
    db.commit()
    return AlertRuleListRead(
        items=[AlertRuleRead.model_validate(rule) for rule in rules],
        total=len(rules),
    )


def create_account_alert_rule(
    payload: AlertRuleCreateRequest,
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> AlertRuleRead:
    """Create an alert rule for a saved media or followed podcast resource."""
    user = _require_authenticated_account(request=request, db=db, settings=settings)
    rule = create_alert_rule(
        db=db,
        user_id=user.id,
        target_type=payload.target_type,
        target_id=payload.target_id,
        event_type=payload.event_type,
    )
    if rule is None:
        raise HTTPException(
            status_code=400,
            detail="Alert target must be a saved reference or followed source",
        )
    _consume_paid_personalization_action(db=db, user_id=user.id, settings=settings)
    db.commit()
    return AlertRuleRead.model_validate(rule)


def update_account_alert_rule(
    rule_id: int,
    payload: AlertRuleUpdateRequest,
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> AlertRuleRead:
    """Pause or resume an account alert rule."""
    user = _require_authenticated_account(request=request, db=db, settings=settings)
    rule = set_alert_rule_enabled(
        db=db,
        user_id=user.id,
        rule_id=rule_id,
        enabled=payload.enabled,
    )
    if rule is None:
        raise HTTPException(status_code=404, detail="Alert rule not found")
    db.commit()
    return AlertRuleRead.model_validate(rule)


def delete_account_alert_rule(
    rule_id: int,
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> AlertRuleDeleteResponse:
    """Remove an alert rule belonging to the authenticated account."""
    user = _require_authenticated_account(request=request, db=db, settings=settings)
    deleted = delete_alert_rule(db=db, user_id=user.id, rule_id=rule_id)
    db.commit()
    return AlertRuleDeleteResponse(deleted=deleted)


def evaluate_account_alert_rules(
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> AlertEvaluationRead:
    """Evaluate alert rules and generate events for new published activity."""
    user = _require_authenticated_account(request=request, db=db, settings=settings)
    generated = evaluate_alert_rules(db=db, user_id=user.id)
    db.commit()
    return AlertEvaluationRead(
        items=[
            AlertEventRead(
                rule=AlertRuleRead.model_validate(item.rule),
                previous_count=item.event.previous_count,
                observed_count=item.event.observed_count,
            )
            for item in generated
        ],
        generated=len(generated),
    )


def list_current_account_digests(
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> DigestListRead:
    """List delivered notification digests for the current account."""
    user = _require_authenticated_account(request=request, db=db, settings=settings)
    digests = list_account_digests(db=db, user_id=user.id)
    db.commit()
    return DigestListRead(
        items=[DigestRead.model_validate(digest) for digest in digests],
        total=len(digests),
    )


def send_current_account_digest(
    request: Request,
    db: DbSession,
    settings: AppSettings,
    sender: DigestSenderDep,
) -> DigestSendResponse:
    """Evaluate alert rules and deliver pending activity by email."""
    user = _require_authenticated_account(request=request, db=db, settings=settings)
    preferences = get_account_preferences(db=db, user_id=user.id)
    if not preferences.digest_enabled:
        db.commit()
        return DigestSendResponse(delivered=False)
    _consume_paid_personalization_action(db=db, user_id=user.id, settings=settings)
    if sender is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Digest delivery is not configured",
        )
    evaluate_alert_rules(db=db, user_id=user.id)
    try:
        digest = send_pending_digest(db=db, user=user, sender=sender)
    except Exception as error:
        db.rollback()
        logger.warning("digest_delivery_failed: %s", error)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to deliver digest",
        ) from error
    db.commit()
    return DigestSendResponse(
        digest=DigestRead.model_validate(digest) if digest is not None else None,
        delivered=digest is not None,
    )


def get_current_account_preferences(
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> PreferenceRead:
    """Return persisted notification preferences for the signed-in account."""
    user = _require_authenticated_account(request=request, db=db, settings=settings)
    preferences = get_account_preferences(db=db, user_id=user.id)
    db.commit()
    return PreferenceRead.model_validate(preferences)


def update_current_account_preferences(
    payload: PreferenceUpdateRequest,
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> PreferenceRead:
    """Update notification preferences for the signed-in account."""
    user = _require_authenticated_account(request=request, db=db, settings=settings)
    preferences = update_account_preferences(
        db=db,
        user_id=user.id,
        digest_enabled=payload.digest_enabled,
        digest_frequency=payload.digest_frequency,
    )
    db.commit()
    return PreferenceRead.model_validate(preferences)


def get_current_account_subscription(
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> SubscriptionRead:
    """Return hosted plan entitlement and current monthly usage."""
    user = _require_authenticated_account(request=request, db=db, settings=settings)
    subscription = get_account_subscription(db=db, user_id=user.id)
    quotas = [
        get_quota_snapshot(db=db, user_id=user.id, settings=settings, feature=feature)
        for feature in (API_REQUESTS, LLM_REQUESTS)
    ]
    db.commit()
    return SubscriptionRead(
        tier=subscription.tier,
        status=subscription.status,
        paid_features_enforced=settings.paid_tier_enforced,
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
    user = _require_authenticated_account(request=request, db=db, settings=settings)
    if not settings.paid_tier_enabled:
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
    "/auth/magic-link/request",
    request_auth_magic_link,
    methods=["POST"],
    response_model=AuthMagicLinkRequestResponse,
    status_code=status.HTTP_202_ACCEPTED,
)

router.add_api_route(
    "/auth/magic-link/verify",
    verify_auth_magic_link,
    methods=["POST"],
    response_model=AuthSessionRead,
)

router.add_api_route(
    "/auth/logout",
    logout_account_session,
    methods=["POST"],
    response_model=AuthLogoutResponse,
)

router.add_api_route(
    "/me",
    get_current_account,
    methods=["GET"],
    response_model=AccountUserRead,
)

router.add_api_route(
    "/me/saves",
    list_account_saved_media,
    methods=["GET"],
    response_model=SavedMediaListRead,
)

router.add_api_route(
    "/me/saves/{media_id}",
    put_account_saved_media,
    methods=["PUT"],
    response_model=SavedMediaRead,
)

router.add_api_route(
    "/me/saves/{media_id}",
    delete_account_saved_media,
    methods=["DELETE"],
    response_model=SavedMediaDeleteResponse,
)

router.add_api_route(
    "/me/follows",
    list_account_followed_podcasts,
    methods=["GET"],
    response_model=FollowedPodcastListRead,
)

router.add_api_route(
    "/me/follows/{podcast_id}",
    put_account_followed_podcast,
    methods=["PUT"],
    response_model=FollowedPodcastRead,
)

router.add_api_route(
    "/me/follows/{podcast_id}",
    delete_account_followed_podcast,
    methods=["DELETE"],
    response_model=FollowedPodcastDeleteResponse,
)

router.add_api_route(
    "/me/alerts",
    list_account_alert_rules,
    methods=["GET"],
    response_model=AlertRuleListRead,
)

router.add_api_route(
    "/me/alerts",
    create_account_alert_rule,
    methods=["POST"],
    response_model=AlertRuleRead,
)

router.add_api_route(
    "/me/alerts/{rule_id}",
    update_account_alert_rule,
    methods=["PATCH"],
    response_model=AlertRuleRead,
)

router.add_api_route(
    "/me/alerts/{rule_id}",
    delete_account_alert_rule,
    methods=["DELETE"],
    response_model=AlertRuleDeleteResponse,
)

router.add_api_route(
    "/me/alerts/evaluate",
    evaluate_account_alert_rules,
    methods=["POST"],
    response_model=AlertEvaluationRead,
)

router.add_api_route(
    "/me/digests",
    list_current_account_digests,
    methods=["GET"],
    response_model=DigestListRead,
)

router.add_api_route(
    "/me/digests/send",
    send_current_account_digest,
    methods=["POST"],
    response_model=DigestSendResponse,
)

router.add_api_route(
    "/me/preferences",
    get_current_account_preferences,
    methods=["GET"],
    response_model=PreferenceRead,
)

router.add_api_route(
    "/me/preferences",
    update_current_account_preferences,
    methods=["PATCH"],
    response_model=PreferenceRead,
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

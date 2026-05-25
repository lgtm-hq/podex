"""Passwordless account authentication endpoints."""

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from podex.api.v2.identifiers import (
    decode_account_alert_rule_id,
    decode_media_id,
    decode_podcast_id,
    encode_account_alert_rule_id,
    encode_account_digest_id,
    encode_account_user_id,
    encode_media_id,
    encode_podcast_id,
)
from podex.api.v2.schemas import (
    AccountAlertEvaluationResponse,
    AccountAlertEventResponse,
    AccountAlertRuleCreateRequest,
    AccountAlertRuleDeleteResponse,
    AccountAlertRuleListResponse,
    AccountAlertRuleResponse,
    AccountAlertRuleUpdateRequest,
    AccountDigestListResponse,
    AccountDigestResponse,
    AccountDigestSendResponse,
    AccountFollowedPodcastDeleteResponse,
    AccountFollowedPodcastListResponse,
    AccountFollowedPodcastResponse,
    AccountPreferenceResponse,
    AccountPreferenceUpdateRequest,
    AccountQuotaResponse,
    AccountSavedMediaDeleteResponse,
    AccountSavedMediaListResponse,
    AccountSavedMediaResponse,
    AccountSubscriptionCheckoutResponse,
    AccountSubscriptionResponse,
    AccountUserResponse,
    AuthLogoutResponse,
    AuthMagicLinkRequest,
    AuthMagicLinkRequestResponse,
    AuthMagicLinkVerifyRequest,
    AuthSessionResponse,
    PublicMediaSummary,
    PublicPodcastSummary,
)
from podex.config import Settings, get_settings
from podex.database import get_db
from podex.logging_config import get_logger
from podex.models import (
    AccountAlertRule,
    AccountDigest,
    AccountPreference,
    AccountSubscription,
    AccountUser,
)
from podex.services.account_alerts import (
    AlertEventData,
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
    FollowedPodcastData,
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
    QuotaSnapshot,
    consume_paid_api_request,
    get_account_subscription,
    get_quota_snapshot,
    has_paid_access,
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

router = APIRouter(tags=["v2-auth"])
logger = get_logger(__name__)


def get_auth_magic_link_sender() -> MagicLinkSender | None:
    """Get configured email delivery for magic-link authentication."""
    return build_magic_link_sender(settings=get_settings())


def get_digest_sender() -> DigestSender | None:
    """Get configured email delivery for account digests."""
    return build_digest_sender(settings=get_settings())


def get_billing_provider() -> BillingCheckoutProvider | None:
    """Get the configured external paid-tier checkout boundary."""
    return build_billing_checkout_provider(settings=get_settings())


def _to_account_user_response(*, user: AccountUser) -> AccountUserResponse:
    """Convert an account user model to the authenticated API boundary."""
    return AccountUserResponse(
        id=encode_account_user_id(user_id=user.id),
        email=user.email,
        created_at=user.created_at,
        last_signed_in_at=user.last_signed_in_at,
    )


def _to_saved_media_response(*, saved: SavedMediaData) -> AccountSavedMediaResponse:
    """Convert a saved public catalog record to the account API boundary."""
    media = saved.media
    return AccountSavedMediaResponse(
        media=PublicMediaSummary(
            id=encode_media_id(media_id=media.id),
            type=media.type,
            title=media.title,
            author=media.author,
            cover_url=media.cover_url,
            year=media.year,
            description=media.description,
            mention_count=media.mention_count,
            episode_count=media.episode_count,
            created_at=media.created_at,
        ),
        saved_at=saved.saved_at,
    )


def _to_followed_podcast_response(
    *,
    followed: FollowedPodcastData,
) -> AccountFollowedPodcastResponse:
    """Convert a followed public podcast to the account API boundary."""
    podcast = followed.podcast
    return AccountFollowedPodcastResponse(
        podcast=PublicPodcastSummary(
            id=encode_podcast_id(podcast_id=podcast.id),
            name=podcast.name,
            slug=podcast.slug,
            description=podcast.description,
            cover_url=podcast.cover_url,
            created_at=podcast.created_at,
            episode_count=podcast.episode_count,
            mention_count=podcast.mention_count,
        ),
        followed_at=followed.followed_at,
    )


def _to_alert_rule_response(*, rule: AccountAlertRule) -> AccountAlertRuleResponse:
    """Convert a rule to its opaque authenticated API representation."""
    target_id = (
        encode_media_id(media_id=rule.target_id)
        if rule.target_type == "media"
        else encode_podcast_id(podcast_id=rule.target_id)
    )
    return AccountAlertRuleResponse(
        id=encode_account_alert_rule_id(rule_id=rule.id),
        target_type=rule.target_type,
        target_id=target_id,
        event_type=rule.event_type,
        baseline_count=rule.baseline_count,
        enabled=rule.enabled,
        last_evaluated_at=rule.last_evaluated_at,
        created_at=rule.created_at,
    )


def _to_alert_event_response(*, generated: AlertEventData) -> AccountAlertEventResponse:
    """Convert a generated event with its associated rule."""
    return AccountAlertEventResponse(
        id=generated.event.id,
        rule=_to_alert_rule_response(rule=generated.rule),
        previous_count=generated.event.previous_count,
        observed_count=generated.event.observed_count,
        created_at=generated.event.created_at,
    )


def _to_digest_response(*, digest: AccountDigest) -> AccountDigestResponse:
    """Convert a delivered digest to the authenticated API boundary."""
    return AccountDigestResponse(
        id=encode_account_digest_id(digest_id=digest.id),
        channel=digest.channel,
        subject=digest.subject,
        body_text=digest.body_text,
        event_count=digest.event_count,
        created_at=digest.created_at,
        delivered_at=digest.delivered_at,
    )


def _to_preference_response(
    *,
    preferences: AccountPreference,
) -> AccountPreferenceResponse:
    """Convert notification preferences to their account API payload."""
    return AccountPreferenceResponse(
        digest_enabled=preferences.digest_enabled,
        digest_frequency=preferences.digest_frequency,
        updated_at=preferences.updated_at,
    )


def _to_subscription_response(
    *,
    subscription: AccountSubscription,
    quotas: list[QuotaSnapshot],
    settings: Settings,
) -> AccountSubscriptionResponse:
    """Convert account billing entitlement and quota state to its API boundary."""
    return AccountSubscriptionResponse(
        tier=subscription.tier,
        status=subscription.status,
        paid_access=has_paid_access(subscription=subscription),
        paid_tier_enabled=settings.paid_tier_enabled,
        paid_features_enforced=settings.paid_tier_enforced,
        quotas=[
            AccountQuotaResponse(
                period=quota.period,
                feature=quota.feature,
                limit=quota.limit,
                used=quota.used,
                remaining=quota.remaining,
            )
            for quota in quotas
        ],
        current_period_ends_at=subscription.current_period_ends_at,
    )


def _consume_paid_personalization_action(*, db: Session, user_id: int) -> None:
    """Apply paid-feature enforcement and translate quota failures to HTTP."""
    try:
        consume_paid_api_request(
            db=db,
            user_id=user_id,
            settings=get_settings(),
        )
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


def _decode_alert_target(*, target_type: str, target_id: str) -> int:
    """Decode a resource target using its declared public type."""
    try:
        if target_type == "media":
            return decode_media_id(media_id=target_id)
        return decode_podcast_id(podcast_id=target_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Alert target not found") from error


def _require_authenticated_account(*, request: Request, db: Session) -> AccountUser:
    """Resolve the authenticated account or reject the request."""
    settings = get_settings()
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
    """Set the secure account session cookie."""
    response.set_cookie(
        key=settings.auth_session_cookie_name,
        value=token,
        max_age=settings.auth_session_ttl_days * 24 * 60 * 60,
        path="/",
        secure=settings.auth_session_cookie_secure,
        httponly=True,
        samesite="lax",
    )


@router.post(
    "/auth/magic-link/request",
    response_model=AuthMagicLinkRequestResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def request_auth_magic_link(
    payload: AuthMagicLinkRequest,
    db: Session = Depends(get_db),
    sender: MagicLinkSender | None = Depends(get_auth_magic_link_sender),
) -> AuthMagicLinkRequestResponse:
    """Request delivery of a short-lived, single-use email sign-in link."""
    settings = get_settings()
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
    query = {"token": issued.token}
    if issued.redirect_path is not None:
        query["redirect_path"] = issued.redirect_path
    verification_url = (
        f"{settings.public_web_url.rstrip('/')}/auth/verify?{urlencode(query)}"
    )
    try:
        sender.send_magic_link(email=issued.email, verification_url=verification_url)
    except Exception as error:
        db.rollback()
        logger.warning("magic_link_delivery_failed", error=str(error))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to deliver sign-in link",
        ) from error
    db.commit()
    return AuthMagicLinkRequestResponse()


@router.post("/auth/magic-link/verify", response_model=AuthSessionResponse)
def verify_auth_magic_link(
    payload: AuthMagicLinkVerifyRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> AuthSessionResponse:
    """Verify a one-time link token and begin an authenticated session."""
    settings = get_settings()
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
    return AuthSessionResponse(
        user=_to_account_user_response(user=authenticated.user),
        expires_at=authenticated.expires_at,
    )


@router.post("/auth/logout", response_model=AuthLogoutResponse)
def logout_account_session(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> AuthLogoutResponse:
    """Revoke the current account session and expire its browser cookie."""
    settings = get_settings()
    signed_out = revoke_user_session(
        db=db,
        session_token=request.cookies.get(settings.auth_session_cookie_name),
    )
    response.delete_cookie(key=settings.auth_session_cookie_name, path="/")
    db.commit()
    return AuthLogoutResponse(signed_out=signed_out)


@router.get("/me", response_model=AccountUserResponse)
def get_current_account(
    request: Request,
    db: Session = Depends(get_db),
) -> AccountUserResponse:
    """Return the account represented by the current browser session."""
    user = _require_authenticated_account(request=request, db=db)
    db.commit()
    return _to_account_user_response(user=user)


@router.get("/me/saves", response_model=AccountSavedMediaListResponse)
def list_account_saved_media(
    request: Request,
    db: Session = Depends(get_db),
) -> AccountSavedMediaListResponse:
    """List saved public catalog media for the current account."""
    user = _require_authenticated_account(request=request, db=db)
    saved = list_saved_media(db=db, user_id=user.id)
    db.commit()
    return AccountSavedMediaListResponse(
        items=[_to_saved_media_response(saved=item) for item in saved],
        total=len(saved),
    )


@router.put("/me/saves/{media_id}", response_model=AccountSavedMediaResponse)
def put_account_saved_media(
    media_id: str,
    request: Request,
    db: Session = Depends(get_db),
) -> AccountSavedMediaResponse:
    """Idempotently save a public catalog media record."""
    user = _require_authenticated_account(request=request, db=db)
    try:
        internal_media_id = decode_media_id(media_id=media_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Media not found") from error
    saved = save_media(db=db, user_id=user.id, media_id=internal_media_id)
    if saved is None:
        raise HTTPException(status_code=404, detail="Media not found")
    _consume_paid_personalization_action(db=db, user_id=user.id)
    db.commit()
    return _to_saved_media_response(saved=saved)


@router.delete(
    "/me/saves/{media_id}",
    response_model=AccountSavedMediaDeleteResponse,
)
def delete_account_saved_media(
    media_id: str,
    request: Request,
    db: Session = Depends(get_db),
) -> AccountSavedMediaDeleteResponse:
    """Remove a public catalog media record from the current account's saves."""
    user = _require_authenticated_account(request=request, db=db)
    try:
        internal_media_id = decode_media_id(media_id=media_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Media not found") from error
    deleted = remove_saved_media(db=db, user_id=user.id, media_id=internal_media_id)
    db.commit()
    return AccountSavedMediaDeleteResponse(deleted=deleted)


@router.get("/me/follows", response_model=AccountFollowedPodcastListResponse)
def list_account_followed_podcasts(
    request: Request,
    db: Session = Depends(get_db),
) -> AccountFollowedPodcastListResponse:
    """List followed public podcast sources for the current account."""
    user = _require_authenticated_account(request=request, db=db)
    followed = list_followed_podcasts(db=db, user_id=user.id)
    db.commit()
    return AccountFollowedPodcastListResponse(
        items=[_to_followed_podcast_response(followed=item) for item in followed],
        total=len(followed),
    )


@router.put("/me/follows/{podcast_id}", response_model=AccountFollowedPodcastResponse)
def put_account_followed_podcast(
    podcast_id: str,
    request: Request,
    db: Session = Depends(get_db),
) -> AccountFollowedPodcastResponse:
    """Idempotently follow a public podcast source."""
    user = _require_authenticated_account(request=request, db=db)
    try:
        internal_podcast_id = decode_podcast_id(podcast_id=podcast_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Podcast not found") from error
    followed = follow_podcast(
        db=db,
        user_id=user.id,
        podcast_id=internal_podcast_id,
    )
    if followed is None:
        raise HTTPException(status_code=404, detail="Podcast not found")
    _consume_paid_personalization_action(db=db, user_id=user.id)
    db.commit()
    return _to_followed_podcast_response(followed=followed)


@router.delete(
    "/me/follows/{podcast_id}",
    response_model=AccountFollowedPodcastDeleteResponse,
)
def delete_account_followed_podcast(
    podcast_id: str,
    request: Request,
    db: Session = Depends(get_db),
) -> AccountFollowedPodcastDeleteResponse:
    """Remove a public podcast source from the current account's follows."""
    user = _require_authenticated_account(request=request, db=db)
    try:
        internal_podcast_id = decode_podcast_id(podcast_id=podcast_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Podcast not found") from error
    deleted = unfollow_podcast(
        db=db,
        user_id=user.id,
        podcast_id=internal_podcast_id,
    )
    db.commit()
    return AccountFollowedPodcastDeleteResponse(deleted=deleted)


@router.get("/me/alerts", response_model=AccountAlertRuleListResponse)
def list_account_alert_rules(
    request: Request,
    db: Session = Depends(get_db),
) -> AccountAlertRuleListResponse:
    """List alert rules belonging to the authenticated account."""
    user = _require_authenticated_account(request=request, db=db)
    rules = list_alert_rules(db=db, user_id=user.id)
    db.commit()
    return AccountAlertRuleListResponse(
        items=[_to_alert_rule_response(rule=rule) for rule in rules],
        total=len(rules),
    )


@router.post("/me/alerts", response_model=AccountAlertRuleResponse)
def create_account_alert_rule(
    payload: AccountAlertRuleCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> AccountAlertRuleResponse:
    """Create an alert rule for a saved media or followed podcast resource."""
    user = _require_authenticated_account(request=request, db=db)
    target_id = _decode_alert_target(
        target_type=payload.target_type,
        target_id=payload.target_id,
    )
    rule = create_alert_rule(
        db=db,
        user_id=user.id,
        target_type=payload.target_type,
        target_id=target_id,
        event_type=payload.event_type,
    )
    if rule is None:
        raise HTTPException(
            status_code=400,
            detail="Alert target must be a saved reference or followed source",
        )
    _consume_paid_personalization_action(db=db, user_id=user.id)
    db.commit()
    return _to_alert_rule_response(rule=rule)


@router.patch("/me/alerts/{rule_id}", response_model=AccountAlertRuleResponse)
def update_account_alert_rule(
    rule_id: str,
    payload: AccountAlertRuleUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> AccountAlertRuleResponse:
    """Pause or resume an account alert rule."""
    user = _require_authenticated_account(request=request, db=db)
    try:
        internal_rule_id = decode_account_alert_rule_id(rule_id=rule_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Alert rule not found") from error
    rule = set_alert_rule_enabled(
        db=db,
        user_id=user.id,
        rule_id=internal_rule_id,
        enabled=payload.enabled,
    )
    if rule is None:
        raise HTTPException(status_code=404, detail="Alert rule not found")
    db.commit()
    return _to_alert_rule_response(rule=rule)


@router.delete("/me/alerts/{rule_id}", response_model=AccountAlertRuleDeleteResponse)
def delete_account_alert_rule(
    rule_id: str,
    request: Request,
    db: Session = Depends(get_db),
) -> AccountAlertRuleDeleteResponse:
    """Remove an alert rule belonging to the authenticated account."""
    user = _require_authenticated_account(request=request, db=db)
    try:
        internal_rule_id = decode_account_alert_rule_id(rule_id=rule_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Alert rule not found") from error
    deleted = delete_alert_rule(db=db, user_id=user.id, rule_id=internal_rule_id)
    db.commit()
    return AccountAlertRuleDeleteResponse(deleted=deleted)


@router.post("/me/alerts/evaluate", response_model=AccountAlertEvaluationResponse)
def evaluate_account_alert_rules(
    request: Request,
    db: Session = Depends(get_db),
) -> AccountAlertEvaluationResponse:
    """Evaluate alert rules and generate events for new published activity."""
    user = _require_authenticated_account(request=request, db=db)
    generated = evaluate_alert_rules(db=db, user_id=user.id)
    db.commit()
    return AccountAlertEvaluationResponse(
        items=[_to_alert_event_response(generated=item) for item in generated],
        generated=len(generated),
    )


@router.get("/me/digests", response_model=AccountDigestListResponse)
def list_current_account_digests(
    request: Request,
    db: Session = Depends(get_db),
) -> AccountDigestListResponse:
    """List delivered notification digests for the current account."""
    user = _require_authenticated_account(request=request, db=db)
    digests = list_account_digests(db=db, user_id=user.id)
    db.commit()
    return AccountDigestListResponse(
        items=[_to_digest_response(digest=digest) for digest in digests],
        total=len(digests),
    )


@router.post("/me/digests/send", response_model=AccountDigestSendResponse)
def send_current_account_digest(
    request: Request,
    db: Session = Depends(get_db),
    sender: DigestSender | None = Depends(get_digest_sender),
) -> AccountDigestSendResponse:
    """Evaluate alert rules and deliver pending activity by email."""
    user = _require_authenticated_account(request=request, db=db)
    preferences = get_account_preferences(db=db, user_id=user.id)
    if not preferences.digest_enabled:
        db.commit()
        return AccountDigestSendResponse(delivered=False)
    _consume_paid_personalization_action(db=db, user_id=user.id)
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
        logger.warning("digest_delivery_failed", error=str(error))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to deliver digest",
        ) from error
    db.commit()
    return AccountDigestSendResponse(
        digest=_to_digest_response(digest=digest) if digest is not None else None,
        delivered=digest is not None,
    )


@router.get("/me/preferences", response_model=AccountPreferenceResponse)
def get_current_account_preferences(
    request: Request,
    db: Session = Depends(get_db),
) -> AccountPreferenceResponse:
    """Return persisted notification preferences for the signed-in account."""
    user = _require_authenticated_account(request=request, db=db)
    preferences = get_account_preferences(db=db, user_id=user.id)
    db.commit()
    return _to_preference_response(preferences=preferences)


@router.patch("/me/preferences", response_model=AccountPreferenceResponse)
def update_current_account_preferences(
    payload: AccountPreferenceUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> AccountPreferenceResponse:
    """Update notification preferences for the signed-in account."""
    user = _require_authenticated_account(request=request, db=db)
    preferences = update_account_preferences(
        db=db,
        user_id=user.id,
        digest_enabled=payload.digest_enabled,
        digest_frequency=payload.digest_frequency,
    )
    db.commit()
    return _to_preference_response(preferences=preferences)


@router.get("/me/subscription", response_model=AccountSubscriptionResponse)
def get_current_account_subscription(
    request: Request,
    db: Session = Depends(get_db),
) -> AccountSubscriptionResponse:
    """Return hosted plan entitlement and current monthly usage."""
    settings = get_settings()
    user = _require_authenticated_account(request=request, db=db)
    subscription = get_account_subscription(db=db, user_id=user.id)
    quotas = [
        get_quota_snapshot(
            db=db,
            user_id=user.id,
            settings=settings,
            feature=feature,
        )
        for feature in (API_REQUESTS, LLM_REQUESTS)
    ]
    db.commit()
    return _to_subscription_response(
        subscription=subscription,
        quotas=quotas,
        settings=settings,
    )


@router.post(
    "/me/subscription/checkout",
    response_model=AccountSubscriptionCheckoutResponse,
)
def begin_current_account_checkout(
    request: Request,
    db: Session = Depends(get_db),
    provider: BillingCheckoutProvider | None = Depends(get_billing_provider),
) -> AccountSubscriptionCheckoutResponse:
    """Start provider-hosted paid-tier checkout after launch gates are enabled."""
    settings = get_settings()
    user = _require_authenticated_account(request=request, db=db)
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
        account_reference=encode_account_user_id(user_id=user.id),
    )
    db.commit()
    return AccountSubscriptionCheckoutResponse(
        provider=checkout.provider,
        checkout_url=checkout.checkout_url,
    )

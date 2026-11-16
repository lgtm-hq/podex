"""Saved media, follows, alert rules and digest endpoints under ``/me``.

Route handlers resolve the authenticated account from the session cookie,
delegate to the account services, and commit the unit of work. Digest email
delivery goes through an injectable sender dependency (see
:mod:`podex.api.v2.account_deps`) so tests and deployments without SMTP stay
deterministic.
"""

from fastapi import APIRouter, HTTPException, Request, status

from podex.api.deps import AppSettings, DbSession
from podex.api.v2.account_deps import (
    DigestSenderDep,
    consume_paid_personalization_action,
    require_authenticated_account,
)
from podex.logging_config import get_logger
from podex.schemas.account import (
    AlertEvaluationRead,
    AlertEventRead,
    AlertRuleCreateRequest,
    AlertRuleDeleteResponse,
    AlertRuleListRead,
    AlertRuleRead,
    AlertRuleUpdateRequest,
    DigestListRead,
    DigestRead,
    DigestSendResponse,
    FollowedPodcastDeleteResponse,
    FollowedPodcastListRead,
    FollowedPodcastRead,
    SavedMediaDeleteResponse,
    SavedMediaListRead,
    SavedMediaRead,
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
from podex.services.account_digests import list_account_digests, send_pending_digest
from podex.services.account_follows import (
    follow_podcast,
    list_followed_podcasts,
    unfollow_podcast,
)
from podex.services.account_preferences import get_account_preferences
from podex.services.account_saves import (
    SavedMediaData,
    list_saved_media,
    remove_saved_media,
    save_media,
)

router = APIRouter(tags=["auth"])
logger = get_logger(__name__)


def _to_saved_media_read(*, saved: SavedMediaData) -> SavedMediaRead:
    """Convert a saved-media service result to its API schema."""
    return SavedMediaRead(
        media=MediaRead.model_validate(saved.media),
        saved_at=saved.saved_at,
    )


def list_account_saved_media(
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> SavedMediaListRead:
    """List saved public catalog media for the current account."""
    user = require_authenticated_account(request=request, db=db, settings=settings)
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
    user = require_authenticated_account(request=request, db=db, settings=settings)
    saved = save_media(db=db, user_id=user.id, media_id=media_id)
    if saved is None:
        raise HTTPException(status_code=404, detail="Media not found")
    consume_paid_personalization_action(db=db, user_id=user.id, settings=settings)
    db.commit()
    return _to_saved_media_read(saved=saved)


def delete_account_saved_media(
    media_id: int,
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> SavedMediaDeleteResponse:
    """Remove a public catalog media record from the current account's saves."""
    user = require_authenticated_account(request=request, db=db, settings=settings)
    deleted = remove_saved_media(db=db, user_id=user.id, media_id=media_id)
    db.commit()
    return SavedMediaDeleteResponse(deleted=deleted)


def list_account_followed_podcasts(
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> FollowedPodcastListRead:
    """List followed public podcast sources for the current account."""
    user = require_authenticated_account(request=request, db=db, settings=settings)
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
    user = require_authenticated_account(request=request, db=db, settings=settings)
    followed = follow_podcast(db=db, user_id=user.id, podcast_id=podcast_id)
    if followed is None:
        raise HTTPException(status_code=404, detail="Podcast not found")
    consume_paid_personalization_action(db=db, user_id=user.id, settings=settings)
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
    user = require_authenticated_account(request=request, db=db, settings=settings)
    deleted = unfollow_podcast(db=db, user_id=user.id, podcast_id=podcast_id)
    db.commit()
    return FollowedPodcastDeleteResponse(deleted=deleted)


def list_account_alert_rules(
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> AlertRuleListRead:
    """List alert rules belonging to the authenticated account."""
    user = require_authenticated_account(request=request, db=db, settings=settings)
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
    user = require_authenticated_account(request=request, db=db, settings=settings)
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
    consume_paid_personalization_action(db=db, user_id=user.id, settings=settings)
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
    user = require_authenticated_account(request=request, db=db, settings=settings)
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
    user = require_authenticated_account(request=request, db=db, settings=settings)
    deleted = delete_alert_rule(db=db, user_id=user.id, rule_id=rule_id)
    db.commit()
    return AlertRuleDeleteResponse(deleted=deleted)


def evaluate_account_alert_rules(
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> AlertEvaluationRead:
    """Evaluate alert rules and generate events for new published activity."""
    user = require_authenticated_account(request=request, db=db, settings=settings)
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
    user = require_authenticated_account(request=request, db=db, settings=settings)
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
    user = require_authenticated_account(request=request, db=db, settings=settings)
    preferences = get_account_preferences(db=db, user_id=user.id)
    if not preferences.digest_enabled:
        db.commit()
        return DigestSendResponse(delivered=False)
    consume_paid_personalization_action(db=db, user_id=user.id, settings=settings)
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

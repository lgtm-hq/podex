"""Account profile, preferences, export and deletion endpoints under ``/me``.

Route handlers resolve the authenticated account from the session cookie,
delegate to the account services, and commit the unit of work.
"""

from fastapi import APIRouter, Request, Response

from podex.api.deps import AppSettings, DbSession
from podex.api.v2.account_deps import require_authenticated_account
from podex.models import AuditAction
from podex.schemas.account import (
    AccountUserRead,
    AuthLogoutResponse,
    PreferenceRead,
    PreferenceUpdateRequest,
)
from podex.services.account_preferences import (
    get_account_preferences,
    update_account_preferences,
)
from podex.services.account_privacy import delete_account, export_account_data
from podex.services.audit_log import record_audit_log

router = APIRouter(tags=["auth"])


def get_current_account(
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> AccountUserRead:
    """Return the account represented by the current browser session."""
    user = require_authenticated_account(request=request, db=db, settings=settings)
    db.commit()
    return AccountUserRead.model_validate(user)


def get_current_account_preferences(
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> PreferenceRead:
    """Return persisted notification preferences for the signed-in account."""
    user = require_authenticated_account(request=request, db=db, settings=settings)
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
    user = require_authenticated_account(request=request, db=db, settings=settings)
    preferences = update_account_preferences(
        db=db,
        user_id=user.id,
        digest_enabled=payload.digest_enabled,
        digest_frequency=payload.digest_frequency,
    )
    db.commit()
    return PreferenceRead.model_validate(preferences)


def export_current_account(
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> dict[str, object]:
    """Return a machine-readable export of the account's stored data."""
    user = require_authenticated_account(request=request, db=db, settings=settings)
    export: dict[str, object] = export_account_data(db=db, user=user)
    db.commit()
    return export


def delete_current_account(
    request: Request,
    response: Response,
    db: DbSession,
    settings: AppSettings,
) -> AuthLogoutResponse:
    """Delete the account and everything stored for it, then sign out."""
    user = require_authenticated_account(request=request, db=db, settings=settings)
    result = delete_account(db=db, user=user)
    record_audit_log(
        db=db,
        action=AuditAction.DELETE_ACCOUNT,
        resource_type="account_user",
        resource_id=result.user_id,
        summary=f"Account {result.user_id} deleted at the owner's request",
        metadata_json={
            "sessions": result.sessions,
            "saves": result.saves,
            "follows": result.follows,
            "alert_rules": result.alert_rules,
            "alert_events": result.alert_events,
            "digests": result.digests,
        },
    )
    response.delete_cookie(key=settings.auth.session_cookie_name, path="/")
    db.commit()
    return AuthLogoutResponse(signed_out=True)


router.add_api_route(
    "/me",
    get_current_account,
    methods=["GET"],
    response_model=AccountUserRead,
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
    "/me/export",
    export_current_account,
    methods=["GET"],
)
router.add_api_route(
    "/me",
    delete_current_account,
    methods=["DELETE"],
    response_model=AuthLogoutResponse,
)

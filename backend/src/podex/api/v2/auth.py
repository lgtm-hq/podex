"""Passwordless sign-in and sign-out endpoints under ``/auth``.

Route handlers stay thin: they delegate to the account services and commit
the unit of work. Magic-link email delivery and the hosted WorkOS client go
through injectable provider dependencies (see
:mod:`podex.api.v2.account_deps`) so tests and deployments without SMTP or
WorkOS stay deterministic.
"""

from secrets import compare_digest, token_urlsafe
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse

from podex.api.deps import AppSettings, DbSession
from podex.api.v2.account_deps import (
    MagicLinkSenderDep,
    WorkOSClientDep,
    set_session_cookie,
)
from podex.api.v2.auth_rejection import SIGN_IN_FAILED, rejected_sign_in_code
from podex.api.v2.auth_state import (
    WORKOS_CALLBACK_ROUTE_NAME,
    WORKOS_STATE_COOKIE,
    expire_workos_state,
)
from podex.logging_config import get_logger
from podex.schemas.account import (
    AccountUserRead,
    AuthLogoutResponse,
    AuthMagicLinkRequest,
    AuthMagicLinkRequestResponse,
    AuthMagicLinkVerifyRequest,
    AuthSessionRead,
)
from podex.services.account_auth import (
    authenticate_magic_link,
    issue_magic_link,
    issue_user_session,
    revoke_user_session,
)
from podex.services.redirect_paths import site_path
from podex.services.workos_auth import (
    WorkOSAuthError,
    WorkOSInvalidCodeError,
    upsert_workos_account_user,
)

router = APIRouter(tags=["auth"])
logger = get_logger(__name__)


# The OAuth ``state`` value only needs to survive one round-trip through the
# hosted sign-in screen, so its cookie stays deliberately short-lived.
_WORKOS_STATE_TTL_SECONDS = 10 * 60


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
        redirect_path=(
            site_path(target=payload.redirect_path, origin=settings.public_web_url)
            if payload.redirect_path is not None
            else None
        ),
        ttl_minutes=settings.auth.magic_link_ttl_minutes,
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
        session_ttl_days=settings.auth.session_ttl_days,
    )
    if authenticated is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign-in link is invalid or expired",
        )
    set_session_cookie(
        response=response,
        token=authenticated.token,
        settings=settings,
    )
    db.commit()
    return AuthSessionRead(
        user=AccountUserRead.model_validate(authenticated.user),
        expires_at=authenticated.expires_at,
    )


def begin_workos_login(
    settings: AppSettings,
    workos: WorkOSClientDep,
) -> RedirectResponse:
    """Start hosted WorkOS AuthKit sign-in with a short-lived state cookie."""
    if workos is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Hosted sign-in is not configured",
        )
    state = token_urlsafe(32)
    redirect = RedirectResponse(
        url=workos.build_authorization_url(state=state),
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    )
    redirect.set_cookie(
        key=WORKOS_STATE_COOKIE,
        value=state,
        max_age=_WORKOS_STATE_TTL_SECONDS,
        path="/",
        secure=settings.auth.session_cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return redirect


# FastAPI injects these named query/callback fields as part of the HTTP contract.
# pylint: disable-next=too-many-arguments
def complete_workos_callback(
    *,
    code: str,
    state: str,
    request: Request,
    db: DbSession,
    settings: AppSettings,
    workos: WorkOSClientDep,
) -> RedirectResponse:
    """Finish hosted sign-in: validate state, exchange the code, set session."""
    if workos is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Hosted sign-in is not configured",
        )
    expected_state = request.cookies.get(WORKOS_STATE_COOKIE)
    try:
        valid_state = bool(
            state
            and expected_state
            and compare_digest(expected_state.encode("utf-8"), state.encode("utf-8"))
        )
    except UnicodeEncodeError:
        valid_state = False
    # Keep encoding errors, which hold the state, out of the response context.
    if not code or not valid_state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Sign-in state is invalid or expired",
        )
    request.state.consumed_workos_state_secure = settings.auth.session_cookie_secure
    try:
        profile = workos.exchange_code(code=code)
    except WorkOSAuthError as error:
        logger.warning("workos_code_exchange_failed: %s", error)
        if isinstance(error, WorkOSInvalidCodeError):
            raise rejected_sign_in_code() from error
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=SIGN_IN_FAILED,
        ) from error
    user = upsert_workos_account_user(db=db, profile=profile)
    authenticated = issue_user_session(
        db=db,
        user=user,
        session_ttl_days=settings.auth.session_ttl_days,
    )
    redirect = RedirectResponse(
        url=f"{settings.public_web_url.rstrip('/')}/account",
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    )
    set_session_cookie(
        response=redirect,
        token=authenticated.token,
        settings=settings,
    )
    expire_workos_state(response=redirect, secure=settings.auth.session_cookie_secure)
    db.commit()
    return redirect


def logout_account_session(
    request: Request,
    response: Response,
    db: DbSession,
    settings: AppSettings,
) -> AuthLogoutResponse:
    """Revoke the current account session and expire its browser cookie."""
    signed_out = revoke_user_session(
        db=db,
        session_token=request.cookies.get(settings.auth.session_cookie_name),
    )
    response.delete_cookie(key=settings.auth.session_cookie_name, path="/")
    db.commit()
    return AuthLogoutResponse(signed_out=signed_out)


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
    "/auth/login",
    begin_workos_login,
    methods=["GET"],
    response_model=None,
)

router.add_api_route(
    "/auth/callback",
    complete_workos_callback,
    name=WORKOS_CALLBACK_ROUTE_NAME,
    methods=["GET"],
    response_model=None,
)

router.add_api_route(
    "/auth/logout",
    logout_account_session,
    methods=["POST"],
    response_model=AuthLogoutResponse,
)

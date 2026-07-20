"""WorkOS AuthKit sign-in bridge.

Builds the hosted AuthKit authorization URL, exchanges callback codes via
the WorkOS User Management API, and upserts the local account identity.
Only the profile fields we persist (WorkOS user id, email, names) ever
leave this module — WorkOS access/refresh tokens and session metadata are
read from the exchange response and discarded, so browser sessions keep
referencing our own opaque session ids.
"""

from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from podex.config import Settings
from podex.models import AccountUser

WORKOS_AUTHORIZE_URL = "https://api.workos.com/user_management/authorize"
WORKOS_AUTHENTICATE_URL = "https://api.workos.com/user_management/authenticate"

_EXCHANGE_TIMEOUT_SECONDS = 10.0


class WorkOSAuthError(RuntimeError):
    """Raised when the WorkOS code exchange fails or yields no usable user."""


@dataclass(frozen=True, slots=True)
class WorkOSUserData:
    """Profile fields returned by a successful WorkOS code exchange."""

    workos_user_id: str
    email: str
    first_name: str | None
    last_name: str | None


class WorkOSAuthClient:
    """Thin client over the two public WorkOS User Management endpoints.

    Args:
        client_id: WorkOS environment client id.
        api_key: WorkOS API key used as the client secret.
        redirect_uri: Callback URL registered with WorkOS.
        transport: Optional httpx transport override for tests.
    """

    def __init__(
        self,
        *,
        client_id: str,
        api_key: str,
        redirect_uri: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client_id = client_id
        self._api_key = api_key
        self._redirect_uri = redirect_uri
        self._transport = transport

    def build_authorization_url(self, *, state: str) -> str:
        """Return the AuthKit hosted authorization URL for a login attempt.

        Args:
            state: Opaque CSRF token also stored in a short-lived cookie.

        Returns:
            The fully-parameterized authorization URL to redirect to.
        """
        query = urlencode(
            {
                "client_id": self._client_id,
                "redirect_uri": self._redirect_uri,
                "response_type": "code",
                "provider": "authkit",
                "prompt": "login",
                "state": state,
            },
        )
        return f"{WORKOS_AUTHORIZE_URL}?{query}"

    def exchange_code(self, *, code: str) -> WorkOSUserData:
        """Exchange a callback authorization code for the signed-in profile.

        Args:
            code: One-time authorization code from the AuthKit callback.

        Returns:
            The minimal profile fields we persist locally.

        Raises:
            WorkOSAuthError: If the exchange fails or omits id/email.
        """
        try:
            with httpx.Client(
                transport=self._transport,
                timeout=_EXCHANGE_TIMEOUT_SECONDS,
            ) as http:
                response = http.post(
                    WORKOS_AUTHENTICATE_URL,
                    data={
                        "client_id": self._client_id,
                        "client_secret": self._api_key,
                        "grant_type": "authorization_code",
                        "code": code,
                    },
                )
        except httpx.HTTPError as error:
            msg = "WorkOS authentication request failed"
            raise WorkOSAuthError(msg) from error
        if response.status_code != httpx.codes.OK:
            msg = f"WorkOS code exchange rejected with HTTP {response.status_code}"
            raise WorkOSAuthError(msg)
        payload = response.json()
        user = payload.get("user") if isinstance(payload, dict) else None
        if not isinstance(user, dict):
            msg = "WorkOS response did not include a user object"
            raise WorkOSAuthError(msg)
        workos_user_id = user.get("id")
        email = user.get("email")
        if not isinstance(workos_user_id, str) or not workos_user_id:
            msg = "WorkOS response did not include a user id"
            raise WorkOSAuthError(msg)
        if not isinstance(email, str) or not email:
            msg = "WorkOS response did not include an email address"
            raise WorkOSAuthError(msg)
        return WorkOSUserData(
            workos_user_id=workos_user_id,
            email=email.strip().casefold(),
            first_name=_optional_name(user.get("first_name")),
            last_name=_optional_name(user.get("last_name")),
        )


def build_workos_auth_client(*, settings: Settings) -> WorkOSAuthClient | None:
    """Build the WorkOS client, or ``None`` when the feature is not configured.

    Args:
        settings: Application settings supplying the WorkOS credentials.

    Returns:
        A configured client, or ``None`` when any credential is missing.
    """
    if not settings.auth.workos_enabled:
        return None
    return WorkOSAuthClient(
        client_id=settings.auth.workos_client_id,
        api_key=settings.auth.workos_api_key,
        redirect_uri=settings.auth.workos_redirect_uri,
    )


def upsert_workos_account_user(
    *,
    db: Session,
    profile: WorkOSUserData,
) -> AccountUser:
    """Create or update the local account for a WorkOS-authenticated profile.

    Matching prefers the stable ``workos_id``; an existing email-only account
    (e.g. created via the magic-link flow) is attached to the WorkOS identity
    on first hosted sign-in. Email is synced only when it would not collide
    with a different local account; names are synced whenever provided.

    Args:
        db: Active database session.
        profile: Profile fields from a successful code exchange.

    Returns:
        The persisted, up-to-date account user.
    """
    user = (
        db.query(AccountUser)
        .filter(AccountUser.workos_id == profile.workos_user_id)
        .first()
    )
    if user is None:
        user = db.query(AccountUser).filter(AccountUser.email == profile.email).first()
        if user is not None:
            user.workos_id = profile.workos_user_id
    if user is None:
        user = AccountUser(email=profile.email, workos_id=profile.workos_user_id)
        db.add(user)
    elif user.email != profile.email:
        taken = (
            db.query(AccountUser)
            .filter(AccountUser.email == profile.email, AccountUser.id != user.id)
            .first()
        )
        if taken is None:
            user.email = profile.email
    if profile.first_name is not None:
        user.first_name = profile.first_name
    if profile.last_name is not None:
        user.last_name = profile.last_name
    db.flush()
    return user


def _optional_name(value: object) -> str | None:
    """Normalize an optional profile name field to a non-empty string."""
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None

"""Tests for hosted WorkOS AuthKit sign-in."""

from dataclasses import fields
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from assertpy import assert_that
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from podex.api.deps import get_app_settings
from podex.api.v2 import auth as v2_auth_api
from podex.config import AuthSettings, Settings
from podex.models import AccountUser, UserSession
from podex.services.workos_auth import (
    WORKOS_AUTHENTICATE_URL,
    WorkOSAuthClient,
    WorkOSAuthError,
    WorkOSUserData,
    build_workos_auth_client,
    upsert_workos_account_user,
)

_PROFILE = WorkOSUserData(
    workos_user_id="user_01ABC",
    email="reader@example.com",
    first_name="Ada",
    last_name="Lovelace",
)


def _workos_settings(*, secure_cookies: bool = True) -> Settings:
    """Return settings with hosted sign-in fully configured.

    Args:
        secure_cookies: Whether cookies carry the ``Secure`` attribute. Flow
            tests disable it so the plain-HTTP test client echoes cookies back.

    Returns:
        Settings enabling the WorkOS feature.
    """
    return Settings(
        auth=AuthSettings(
            workos_client_id="client_123",
            workos_api_key="sk_test_456",
            workos_redirect_uri="https://app.example.com/api/v2/auth/callback",
            session_cookie_secure=secure_cookies,
        ),
    )


class StubWorkOSClient:
    """Deterministic stand-in for the WorkOS client in API tests."""

    def __init__(
        self,
        *,
        profile: WorkOSUserData = _PROFILE,
        fail: bool = False,
    ) -> None:
        self.profile = profile
        self.fail = fail
        self.codes: list[str] = []

    def build_authorization_url(self, *, state: str) -> str:
        """Return a recognizable authorization URL embedding the state."""
        return f"https://api.workos.com/user_management/authorize?state={state}"

    def exchange_code(self, *, code: str) -> WorkOSUserData:
        """Return the canned profile, or fail like a rejected exchange."""
        if self.fail:
            msg = "exchange rejected"
            raise WorkOSAuthError(msg)
        self.codes.append(code)
        return self.profile


def _override(client: TestClient, workos: object) -> None:
    """Install WorkOS-enabled settings and a client override on the app."""
    app = client.app
    if not isinstance(app, FastAPI):  # pragma: no cover - narrowed
        raise AssertionError
    app.dependency_overrides[get_app_settings] = lambda: _workos_settings(
        secure_cookies=False,
    )
    app.dependency_overrides[v2_auth_api.get_workos_auth_client] = lambda: workos


def _state_cookie(response: httpx.Response) -> str:
    """Read the OAuth state value from the login response cookie."""
    cookie = SimpleCookie()
    for header in response.headers.get_list("set-cookie"):
        cookie.load(header)
    return cookie[v2_auth_api.WORKOS_STATE_COOKIE].value


def test_workos_enabled_requires_all_three_settings() -> None:
    """The feature stays off unless every WorkOS credential is present."""
    assert_that(Settings().auth.workos_enabled).is_false()
    assert_that(
        Settings(auth=AuthSettings(workos_client_id="client_123")).auth.workos_enabled,
    ).is_false()
    assert_that(
        Settings(
            auth=AuthSettings(workos_client_id="client_123", workos_api_key="sk"),
        ).auth.workos_enabled,
    ).is_false()
    assert_that(_workos_settings().auth.workos_enabled).is_true()


def test_build_workos_auth_client_requires_configuration() -> None:
    """The builder returns ``None`` without credentials, a client with them."""
    assert_that(build_workos_auth_client(settings=Settings())).is_none()
    assert_that(
        build_workos_auth_client(settings=_workos_settings()),
    ).is_instance_of(WorkOSAuthClient)


def test_authorization_url_carries_authkit_parameters() -> None:
    """The hosted authorization URL includes every required parameter."""
    workos = WorkOSAuthClient(
        client_id="client_123",
        api_key="sk_test_456",
        redirect_uri="https://app.example.com/api/v2/auth/callback",
    )

    url = urlparse(workos.build_authorization_url(state="state-token"))
    query = parse_qs(url.query)

    assert_that(url.hostname).is_equal_to("api.workos.com")
    assert_that(url.path).is_equal_to("/user_management/authorize")
    assert_that(query["client_id"]).is_equal_to(["client_123"])
    assert_that(query["redirect_uri"]).is_equal_to(
        ["https://app.example.com/api/v2/auth/callback"],
    )
    assert_that(query["response_type"]).is_equal_to(["code"])
    assert_that(query["provider"]).is_equal_to(["authkit"])
    assert_that(query["prompt"]).is_equal_to(["login"])
    assert_that(query["state"]).is_equal_to(["state-token"])


def test_exchange_code_returns_profile_and_drops_tokens() -> None:
    """A successful exchange yields only the fields we persist."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "user": {
                    "id": "user_01ABC",
                    "email": " Reader@Example.COM ",
                    "first_name": "Ada",
                    "last_name": "",
                },
                "access_token": "at_secret",  # nosec B105 - canned test value
                "refresh_token": "rt_secret",  # nosec B105 - canned test value
            },
        )

    workos = WorkOSAuthClient(
        client_id="client_123",
        api_key="sk_test_456",
        redirect_uri="https://app.example.com/api/v2/auth/callback",
        transport=httpx.MockTransport(handler),
    )

    profile = workos.exchange_code(code="auth-code")

    assert_that(str(seen[0].url)).is_equal_to(WORKOS_AUTHENTICATE_URL)
    body = parse_qs(seen[0].content.decode("utf-8"))
    assert_that(body["client_id"]).is_equal_to(["client_123"])
    assert_that(body["client_secret"]).is_equal_to(["sk_test_456"])
    assert_that(body["grant_type"]).is_equal_to(["authorization_code"])
    assert_that(body["code"]).is_equal_to(["auth-code"])
    assert_that(profile.workos_user_id).is_equal_to("user_01ABC")
    assert_that(profile.email).is_equal_to("reader@example.com")
    assert_that(profile.first_name).is_equal_to("Ada")
    assert_that(profile.last_name).is_none()
    # The dataclass has no room for vendor tokens, so none can be persisted.
    assert_that([field.name for field in fields(WorkOSUserData)]).is_equal_to(
        ["workos_user_id", "email", "first_name", "last_name"],
    )


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(401, json={"error": "invalid_grant"}),
        httpx.Response(200, json={"authenticated": True}),
        httpx.Response(200, json={"user": {"email": "reader@example.com"}}),
        httpx.Response(200, json={"user": {"id": "user_01ABC"}}),
    ],
)
def test_exchange_code_rejects_bad_responses(response: httpx.Response) -> None:
    """Failed or incomplete exchanges raise instead of half-signing-in."""
    workos = WorkOSAuthClient(
        client_id="client_123",
        api_key="sk_test_456",
        redirect_uri="https://app.example.com/api/v2/auth/callback",
        transport=httpx.MockTransport(lambda _request: response),
    )

    with pytest.raises(WorkOSAuthError):
        workos.exchange_code(code="auth-code")


def test_exchange_code_wraps_transport_errors() -> None:
    """Network failures surface as a WorkOSAuthError."""

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    workos = WorkOSAuthClient(
        client_id="client_123",
        api_key="sk_test_456",
        redirect_uri="https://app.example.com/api/v2/auth/callback",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(WorkOSAuthError):
        workos.exchange_code(code="auth-code")


def test_upsert_creates_new_account(db_session: Session) -> None:
    """A first-time hosted sign-in mints a local account."""
    user = upsert_workos_account_user(db=db_session, profile=_PROFILE)
    db_session.commit()

    stored = db_session.execute(select(AccountUser)).scalar_one()
    assert_that(stored.id).is_equal_to(user.id)
    assert_that(stored.workos_id).is_equal_to("user_01ABC")
    assert_that(stored.email).is_equal_to("reader@example.com")
    assert_that(stored.first_name).is_equal_to("Ada")
    assert_that(stored.last_name).is_equal_to("Lovelace")


def test_upsert_matches_returning_user_by_workos_id(db_session: Session) -> None:
    """A returning hosted user is found by workos_id and synced."""
    first = upsert_workos_account_user(db=db_session, profile=_PROFILE)
    updated = upsert_workos_account_user(
        db=db_session,
        profile=WorkOSUserData(
            workos_user_id="user_01ABC",
            email="renamed@example.com",
            first_name="Augusta",
            last_name=None,
        ),
    )
    db_session.commit()

    assert_that(updated.id).is_equal_to(first.id)
    stored = db_session.execute(select(AccountUser)).scalar_one()
    assert_that(stored.email).is_equal_to("renamed@example.com")
    assert_that(stored.first_name).is_equal_to("Augusta")
    assert_that(stored.last_name).is_equal_to("Lovelace")


def test_upsert_attaches_workos_id_to_existing_email_account(
    db_session: Session,
) -> None:
    """A magic-link account is linked to its WorkOS identity by email."""
    db_session.add(AccountUser(email="reader@example.com"))
    db_session.commit()

    user = upsert_workos_account_user(db=db_session, profile=_PROFILE)
    db_session.commit()

    stored = db_session.execute(select(AccountUser)).scalar_one()
    assert_that(stored.id).is_equal_to(user.id)
    assert_that(stored.workos_id).is_equal_to("user_01ABC")


def test_upsert_keeps_email_when_it_would_collide(db_session: Session) -> None:
    """An email sync never steals another local account's address."""
    db_session.add(AccountUser(email="taken@example.com"))
    db_session.commit()
    upsert_workos_account_user(db=db_session, profile=_PROFILE)

    user = upsert_workos_account_user(
        db=db_session,
        profile=WorkOSUserData(
            workos_user_id="user_01ABC",
            email="taken@example.com",
            first_name=None,
            last_name=None,
        ),
    )
    db_session.commit()

    assert_that(user.email).is_equal_to("reader@example.com")


def test_status_reports_workos_disabled_by_default(client: TestClient) -> None:
    """The public status probe advertises hosted sign-in as off."""
    response = client.get("/api/v2/status")

    assert_that(response.status_code).is_equal_to(200)
    assert_that(response.json()["workos_enabled"]).is_false()


def test_status_reports_workos_enabled_when_configured(client: TestClient) -> None:
    """The public status probe flips once credentials are configured."""
    _override(client, StubWorkOSClient())

    response = client.get("/api/v2/status")

    assert_that(response.json()["workos_enabled"]).is_true()


def test_login_returns_404_when_workos_disabled(client: TestClient) -> None:
    """Without configuration the hosted login route does not exist."""
    response = client.get("/api/v2/auth/login", follow_redirects=False)

    assert_that(response.status_code).is_equal_to(404)


def test_login_redirects_with_state_cookie(client: TestClient) -> None:
    """Login sets a short-lived state cookie and redirects to AuthKit."""
    app = client.app
    if not isinstance(app, FastAPI):  # pragma: no cover - narrowed
        raise AssertionError
    app.dependency_overrides[get_app_settings] = _workos_settings
    app.dependency_overrides[v2_auth_api.get_workos_auth_client] = lambda: (
        build_workos_auth_client(settings=_workos_settings())
    )

    response = client.get("/api/v2/auth/login", follow_redirects=False)

    assert_that(response.status_code).is_equal_to(307)
    location = urlparse(response.headers["location"])
    query = parse_qs(location.query)
    assert_that(location.hostname).is_equal_to("api.workos.com")
    assert_that(query["client_id"]).is_equal_to(["client_123"])
    assert_that(query["provider"]).is_equal_to(["authkit"])
    assert_that(query["prompt"]).is_equal_to(["login"])
    assert_that(query["response_type"]).is_equal_to(["code"])
    state = _state_cookie(response)
    assert_that(query["state"]).is_equal_to([state])
    set_cookie = response.headers["set-cookie"]
    assert_that(set_cookie).contains("HttpOnly", "Max-Age=600", "Secure")


def test_callback_rejects_state_mismatch(client: TestClient) -> None:
    """A state value that does not match the cookie is rejected."""
    workos = StubWorkOSClient()
    _override(client, workos)
    client.get("/api/v2/auth/login", follow_redirects=False)

    response = client.get(
        "/api/v2/auth/callback",
        params={"code": "auth-code", "state": "forged-state"},
        follow_redirects=False,
    )

    assert_that(response.status_code).is_equal_to(400)
    assert_that(workos.codes).is_empty()


def test_callback_rejects_missing_state_cookie(client: TestClient) -> None:
    """An expired or absent state cookie fails closed."""
    workos = StubWorkOSClient()
    _override(client, workos)

    response = client.get(
        "/api/v2/auth/callback",
        params={"code": "auth-code", "state": "anything"},
        follow_redirects=False,
    )

    assert_that(response.status_code).is_equal_to(400)
    assert_that(workos.codes).is_empty()


def test_callback_returns_404_when_workos_disabled(client: TestClient) -> None:
    """Without configuration the callback route does not exist."""
    response = client.get(
        "/api/v2/auth/callback",
        params={"code": "auth-code", "state": "anything"},
        follow_redirects=False,
    )

    assert_that(response.status_code).is_equal_to(404)


def test_callback_signs_in_new_user(
    client: TestClient,
    db_session: Session,
) -> None:
    """A valid callback upserts the account and issues our session cookie."""
    workos = StubWorkOSClient()
    _override(client, workos)
    login = client.get("/api/v2/auth/login", follow_redirects=False)
    state = _state_cookie(login)

    response = client.get(
        "/api/v2/auth/callback",
        params={"code": "auth-code", "state": state},
        follow_redirects=False,
    )

    assert_that(response.status_code).is_equal_to(307)
    assert_that(response.headers["location"]).is_equal_to(
        "http://localhost:4321/account",
    )
    assert_that(workos.codes).is_equal_to(["auth-code"])
    user = db_session.execute(select(AccountUser)).scalar_one()
    assert_that(user.workos_id).is_equal_to("user_01ABC")
    assert_that(user.email).is_equal_to("reader@example.com")
    session = db_session.execute(select(UserSession)).scalar_one()
    assert_that(session.user_id).is_equal_to(user.id)
    cookie = SimpleCookie()
    for header in response.headers.get_list("set-cookie"):
        cookie.load(header)
    session_token = cookie["podex_session"].value
    assert_that(cookie["podex_session"]["httponly"]).is_not_equal_to("")
    # Only a digest of our own opaque token is stored — never the raw value
    # and never anything issued by WorkOS.
    assert_that(session.token_digest).is_not_equal_to(session_token)
    # The state cookie is cleared after use.
    assert_that(cookie[v2_auth_api.WORKOS_STATE_COOKIE].value).is_equal_to("")

    me = client.get(
        "/api/v2/me",
        headers={"Cookie": f"podex_session={session_token}"},
    )
    assert_that(me.status_code).is_equal_to(200)
    assert_that(me.json()["email"]).is_equal_to("reader@example.com")
    assert_that(me.json()["first_name"]).is_equal_to("Ada")


def test_callback_reuses_account_for_returning_user(
    client: TestClient,
    db_session: Session,
) -> None:
    """A second hosted sign-in reuses the account matched by workos_id."""
    workos = StubWorkOSClient()
    _override(client, workos)
    for _ in range(2):
        login = client.get("/api/v2/auth/login", follow_redirects=False)
        response = client.get(
            "/api/v2/auth/callback",
            params={"code": "auth-code", "state": _state_cookie(login)},
            follow_redirects=False,
        )
        assert_that(response.status_code).is_equal_to(307)

    users = db_session.execute(select(AccountUser)).scalars().all()
    assert_that(users).is_length(1)
    sessions = db_session.execute(select(UserSession)).scalars().all()
    assert_that(sessions).is_length(2)


def test_callback_maps_exchange_failure_to_bad_gateway(
    client: TestClient,
) -> None:
    """A rejected code exchange surfaces as an upstream failure."""
    _override(client, StubWorkOSClient(fail=True))
    login = client.get("/api/v2/auth/login", follow_redirects=False)

    response = client.get(
        "/api/v2/auth/callback",
        params={"code": "auth-code", "state": _state_cookie(login)},
        follow_redirects=False,
    )

    assert_that(response.status_code).is_equal_to(502)

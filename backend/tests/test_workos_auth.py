"""Tests for the WorkOS exchange client, settings and account upsert."""

from collections.abc import Callable
from dataclasses import fields
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from assertpy import assert_that
from sqlalchemy import select
from sqlalchemy.orm import Session

from podex.config import (
    AuthSettings,
    Settings,
)
from podex.models import AccountUser
from podex.services.workos_auth import (
    WORKOS_AUTHENTICATE_URL,
    WorkOSAuthClient,
    WorkOSAuthError,
    WorkOSUserData,
    build_workos_auth_client,
    upsert_workos_account_user,
)
from tests.workos_support import (
    CHAIN_API_KEY,
    CHAIN_CODE,
    FAILURE_MODES,
    PROFILE,
    texts_reachable_from,
    workos_settings,
)


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
    assert_that(workos_settings().auth.workos_enabled).is_true()


def test_build_workos_auth_client_requires_configuration() -> None:
    """The builder returns ``None`` without credentials, a client with them."""
    assert_that(build_workos_auth_client(settings=Settings())).is_none()
    assert_that(
        build_workos_auth_client(settings=workos_settings()),
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


@pytest.mark.parametrize(("handler", "expected_message"), FAILURE_MODES)
def test_exchange_code_failure_keeps_credentials_out_of_chain(
    handler: Callable[[httpx.Request], httpx.Response],
    expected_message: str,
) -> None:
    """A failed exchange raises an allowlisted message with no credential."""
    sent: list[httpx.Request] = []

    def recording_handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return handler(request)

    workos = WorkOSAuthClient(
        client_id="client_123",
        api_key=CHAIN_API_KEY,
        redirect_uri="https://app.example.com/api/v2/auth/callback",
        transport=httpx.MockTransport(recording_handler),
    )

    with pytest.raises(WorkOSAuthError) as caught:
        workos.exchange_code(code=CHAIN_CODE)

    # The request really did carry both credentials.
    sent_body = sent[0].content.decode("utf-8")
    assert_that(sent_body).contains(CHAIN_API_KEY, CHAIN_CODE)
    assert_that(str(caught.value)).is_equal_to(expected_message)
    assert_that(caught.value.__cause__).is_none()
    assert_that(caught.value.__context__).is_none()
    for text in texts_reachable_from(caught.value):
        for leaked in (CHAIN_API_KEY, CHAIN_CODE, "api.workos.com", "timed out"):
            assert_that(text).does_not_contain(leaked)


def test_upsert_creates_new_account(db_session: Session) -> None:
    """A first-time hosted sign-in mints a local account."""
    user = upsert_workos_account_user(db=db_session, profile=PROFILE)
    db_session.commit()

    stored = db_session.execute(select(AccountUser)).scalar_one()
    assert_that(stored.id).is_equal_to(user.id)
    assert_that(stored.workos_id).is_equal_to("user_01ABC")
    assert_that(stored.email).is_equal_to("reader@example.com")
    assert_that(stored.first_name).is_equal_to("Ada")
    assert_that(stored.last_name).is_equal_to("Lovelace")


def test_upsert_matches_returning_user_by_workos_id(db_session: Session) -> None:
    """A returning hosted user is found by workos_id and synced."""
    first = upsert_workos_account_user(db=db_session, profile=PROFILE)
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

    user = upsert_workos_account_user(db=db_session, profile=PROFILE)
    db_session.commit()

    stored = db_session.execute(select(AccountUser)).scalar_one()
    assert_that(stored.id).is_equal_to(user.id)
    assert_that(stored.workos_id).is_equal_to("user_01ABC")


def test_upsert_keeps_email_when_it_would_collide(db_session: Session) -> None:
    """An email sync never steals another local account's address."""
    db_session.add(AccountUser(email="taken@example.com"))
    db_session.commit()
    upsert_workos_account_user(db=db_session, profile=PROFILE)

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

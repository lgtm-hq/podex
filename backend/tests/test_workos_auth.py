"""Tests for hosted WorkOS AuthKit sign-in."""

import logging
from collections.abc import Callable
from dataclasses import fields
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlparse

import httpx
import httpx2
import pytest
from assertpy import assert_that
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from podex.api.deps import get_app_settings
from podex.api.v2 import auth as v2_auth_api
from podex.config import (
    AuthSettings,
    Settings,
)
from podex.models import AccountUser, UserSession
from podex.services.workos_auth import (
    WORKOS_AUTHENTICATE_URL,
    WorkOSAuthClient,
    WorkOSAuthError,
    WorkOSUserData,
    build_workos_auth_client,
    upsert_workos_account_user,
)
from tests.conftest import CapturingSentryTransport, create_reporting_app

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


def _state_cookie(response: httpx2.Response) -> str:
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


_CHAIN_API_KEY = "sk_test_789"
_CHAIN_CODE = "auth-code-chain-321"


def _texts_reachable_from(error: BaseException) -> list[str]:
    """Render everything an error reporter can reach from ``error``'s chain.

    Walks both ``__cause__`` and ``__context__`` and, for every exception
    found, its message, ``args`` and any attached HTTPX request (URL,
    headers and body).

    Args:
        error: The exception raised to the caller.

    Returns:
        Text renderings of every reachable object.
    """
    texts: list[str] = []
    pending: list[BaseException] = [error]
    visited: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in visited:
            continue
        visited.add(id(current))
        texts.extend((str(current), repr(current)))
        texts.extend(repr(argument) for argument in current.args)
        try:
            request = getattr(current, "request", None)
        except RuntimeError:  # httpx raises when no request is attached
            request = None
        if isinstance(request, httpx.Request):
            texts.extend(
                (
                    str(request.url),
                    repr(dict(request.headers)),
                    request.content.decode("utf-8"),
                ),
            )
        pending.extend(
            linked
            for linked in (current.__cause__, current.__context__)
            if linked is not None
        )
    return texts


_INVALID_CODE_MESSAGE = (
    "WorkOS rejected the authorization code (HTTP 400 invalid_grant)"
)


def _refuse(request: httpx.Request) -> httpx.Response:
    """Fail like an unreachable WorkOS endpoint."""
    raise httpx.ConnectError("connection refused by api.workos.com", request=request)


def _time_out(request: httpx.Request) -> httpx.Response:
    """Fail like a WorkOS endpoint that stops responding."""
    raise httpx.ReadTimeout("read timed out", request=request)


def _invalid_code(_request: httpx.Request) -> httpx.Response:
    """Reject the code as invalid or expired (OAuth ``invalid_grant``)."""
    return httpx.Response(
        400,
        json={
            "error": "invalid_grant",
            "error_description": f"The code '{_CHAIN_CODE}' has expired or is invalid.",
        },
    )


def _invalid_client(_request: httpx.Request) -> httpx.Response:
    """Reject our credentials (invalid API key), echoing the code."""
    return httpx.Response(
        401,
        json={"error": "invalid_client", "error_description": _CHAIN_CODE},
    )


def _bad_request(_request: httpx.Request) -> httpx.Response:
    """Reject the request for a reason other than the code itself."""
    return httpx.Response(400, json={"error": "invalid_request", "code": _CHAIN_CODE})


def _bad_request_not_json(_request: httpx.Request) -> httpx.Response:
    """Reject with HTTP 400 and a body that is not JSON."""
    return httpx.Response(400, content=f"<html>{_CHAIN_CODE}</html>".encode())


def _unavailable(_request: httpx.Request) -> httpx.Response:
    """Fail like a WorkOS server error."""
    return httpx.Response(503, content=f"upstream {_CHAIN_CODE}".encode())


def _malformed(_request: httpx.Request) -> httpx.Response:
    """Answer 200 with a body that is not JSON and echoes the code."""
    return httpx.Response(200, content=f"<html>{_CHAIN_CODE}</html>".encode())


_FAILURE_MODES = [
    pytest.param(
        _refuse,
        "WorkOS authentication request failed: ConnectError",
        id="connect-error",
    ),
    pytest.param(
        _time_out,
        "WorkOS authentication request failed: ReadTimeout",
        id="timeout",
    ),
    pytest.param(
        _invalid_code,
        _INVALID_CODE_MESSAGE,
        id="invalid-code",
    ),
    pytest.param(
        _invalid_client,
        "WorkOS code exchange rejected with HTTP 401",
        id="invalid-client",
    ),
    pytest.param(
        _bad_request,
        "WorkOS code exchange rejected with HTTP 400",
        id="other-400",
    ),
    pytest.param(
        _bad_request_not_json,
        "WorkOS code exchange rejected with HTTP 400",
        id="400-not-json",
    ),
    pytest.param(
        _unavailable,
        "WorkOS code exchange rejected with HTTP 503",
        id="http-503",
    ),
    pytest.param(_malformed, "WorkOS response was not valid JSON", id="malformed-json"),
]


def _failing_workos(handler: Callable[[httpx.Request], httpx.Response]) -> object:
    """Return a real WorkOS client whose transport answers with ``handler``."""
    return WorkOSAuthClient(
        client_id="client_123",
        api_key=_CHAIN_API_KEY,
        redirect_uri="https://app.example.com/api/v2/auth/callback",
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.parametrize(("handler", "expected_message"), _FAILURE_MODES)
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
        api_key=_CHAIN_API_KEY,
        redirect_uri="https://app.example.com/api/v2/auth/callback",
        transport=httpx.MockTransport(recording_handler),
    )

    with pytest.raises(WorkOSAuthError) as caught:
        workos.exchange_code(code=_CHAIN_CODE)

    # The request really did carry both credentials.
    sent_body = sent[0].content.decode("utf-8")
    assert_that(sent_body).contains(_CHAIN_API_KEY, _CHAIN_CODE)
    assert_that(str(caught.value)).is_equal_to(expected_message)
    assert_that(caught.value.__cause__).is_none()
    assert_that(caught.value.__context__).is_none()
    for text in _texts_reachable_from(caught.value):
        for leaked in (_CHAIN_API_KEY, _CHAIN_CODE, "api.workos.com", "timed out"):
            assert_that(text).does_not_contain(leaked)


@pytest.mark.parametrize(("handler", "expected_message"), _FAILURE_MODES)
def test_callback_logs_no_oauth_credentials(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
    handler: Callable[[httpx.Request], httpx.Response],
    expected_message: str,
) -> None:
    """A failed callback logs path, status and failure type, never secrets."""
    _override(client, _failing_workos(handler))
    login = client.get("/api/v2/auth/login", follow_redirects=False)
    state = _state_cookie(login)

    with caplog.at_level(logging.DEBUG):
        response = client.get(
            "/api/v2/auth/callback",
            params={"code": _CHAIN_CODE, "state": state},
            follow_redirects=False,
        )

    assert_that(response.status_code).is_equal_to(502)
    access = [
        record
        for record in caplog.records
        if record.name == "podex.access"
        and record.__dict__["path"] == "/api/v2/auth/callback"
    ]
    assert_that(access).is_length(1)
    assert_that(access[0].__dict__["status_code"]).is_equal_to(502)
    warnings = [
        record
        for record in caplog.records
        if record.getMessage() == f"workos_code_exchange_failed: {expected_message}"
    ]
    assert_that(warnings).is_length(1)
    assert_that(warnings[0].levelno).is_equal_to(logging.WARNING)
    formatter = logging.Formatter()
    # ``httpx2`` is the test client logging its own outgoing request line;
    # every other record was emitted while the server handled the request.
    server_records = [record for record in caplog.records if record.name != "httpx2"]
    assert_that(server_records).is_not_empty()
    for record in server_records:
        line = formatter.format(record)
        for leaked in (_CHAIN_CODE, state, _CHAIN_API_KEY, "sk_test_456"):
            assert_that(line).does_not_contain(leaked)


_TEST_DSN = "https://public@sentry.invalid/1"


def _reported_failed_callback(
    db_session: Session,
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[httpx2.Response, str]:
    """Run a failing sign-in through an app with error reporting enabled.

    Args:
        db_session: Database session the app should use.
        handler: Transport behaviour of the WorkOS token endpoint.

    Returns:
        The callback response and the state value it carried.
    """
    app = create_reporting_app(db_session)
    with TestClient(app) as reporting_client:
        _override(reporting_client, _failing_workos(handler))
        login = reporting_client.get("/api/v2/auth/login", follow_redirects=False)
        state = _state_cookie(login)
        response = reporting_client.get(
            "/api/v2/auth/callback",
            params={"code": _CHAIN_CODE, "state": state},
            follow_redirects=False,
        )
    return response, state


@pytest.mark.parametrize(("handler", "expected_message"), _FAILURE_MODES)
def test_failed_callback_reports_no_credentials(
    db_session: Session,
    sentry_transport: CapturingSentryTransport,
    handler: Callable[[httpx.Request], httpx.Response],
    expected_message: str,
) -> None:
    """Failures are reported usefully without credentials, except bad codes.

    A rejected authorization code is caller-triggerable (anyone can replay
    or forge one), so it is only logged; every other failure is reported
    exactly once.
    """
    response, state = _reported_failed_callback(db_session, handler)

    assert_that(response.status_code).is_equal_to(502)
    wire = sentry_transport.serialized()
    for leaked in (_CHAIN_API_KEY, "sk_test_456", _CHAIN_CODE, state):
        assert_that(wire).does_not_contain(leaked)
    events = sentry_transport.events()
    if expected_message == _INVALID_CODE_MESSAGE:
        assert_that(events).is_empty()
        return
    assert_that(events).is_length(1)
    event = events[0]
    exceptions = {value["type"]: value for value in event["exception"]["values"]}
    assert_that(exceptions).contains_key("HTTPException", "WorkOSAuthError")
    assert_that(exceptions["WorkOSAuthError"]["value"]).is_equal_to(expected_message)
    for value in exceptions.values():
        for frame in value.get("stacktrace", {}).get("frames", []):
            assert_that(frame).does_not_contain_key("vars")
    assert_that(event["transaction"]).is_equal_to("/api/v2/auth/callback")
    assert_that(event["tags"]["request_id"]).is_equal_to(
        response.headers["X-Request-ID"],
    )
    assert_that(event["request"]["url"]).is_equal_to(
        "http://testserver/api/v2/auth/callback",
    )
    assert_that(event["request"]).does_not_contain_key("query_string")


def test_replayed_invalid_codes_send_no_reports(
    db_session: Session,
    sentry_transport: CapturingSentryTransport,
) -> None:
    """Repeated bad-code callbacks never spend reporting quota."""
    for _ in range(5):
        response, _state = _reported_failed_callback(db_session, _invalid_code)
        assert_that(response.status_code).is_equal_to(502)

    assert_that(sentry_transport.events()).is_empty()


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


@pytest.mark.parametrize(
    ("state", "cookie"),
    [("é", "valid-state"), ("valid-state", "é"), ("%ED%A0%80", "valid-state")],
    ids=["query", "cookie", "invalid-utf8-query"],
)
def test_callback_rejects_non_ascii_state(
    client: TestClient,
    state: str,
    cookie: str,
) -> None:
    """Non-ASCII query or cookie state returns the ordinary mismatch response."""
    workos = StubWorkOSClient()
    _override(client, workos)
    with TestClient(client.app, raise_server_exceptions=False) as callback_client:
        headers = [
            (b"Cookie", f"{v2_auth_api.WORKOS_STATE_COOKIE}={cookie}".encode()),
            (b"X-Request-ID", b"non-ascii-state-regression"),
        ]
        mismatch = callback_client.get(
            "/api/v2/auth/callback?code=auth-code&state=wrong-state",
            headers=headers,
        )
        response = callback_client.get(
            f"/api/v2/auth/callback?code=auth-code&state={state}",
            headers=headers,
        )

    assert_that(response.status_code).is_equal_to(400)
    assert_that(response.json()).is_equal_to(mismatch.json())
    assert_that(workos.codes).is_empty()


@pytest.mark.parametrize(
    ("state", "cookie"),
    [
        ("\udcff", "valid-state"),
        ("\ud800", "valid-state"),
        ("\udc00", "valid-state"),
        # The cookie is read as Latin-1 and re-encoded as UTF-8: b"\xc3\x83\xc2\xbf".
        ("\udcc3\udc83\udcc2\udcbf", "ÿ"),
    ],
    ids=["escaped-byte", "high-surrogate", "low-surrogate", "byte-collision"],
)
def test_callback_rejects_unencodable_state_without_exception_context(
    db_session: Session,
    state: str,
    cookie: str,
) -> None:
    """Direct callers cannot leak an unencodable state through exception context."""
    workos = StubWorkOSClient()
    # HTTP query decoding replaces invalid UTF-8; exercise lone surrogates directly.
    request = Request(
        {
            "type": "http",
            "headers": [
                (b"cookie", f"{v2_auth_api.WORKOS_STATE_COOKIE}={cookie}".encode()),
            ],
        },
    )
    with pytest.raises(HTTPException) as caught:
        v2_auth_api.complete_workos_callback(
            code="auth-code",
            state=state,
            request=request,
            db=db_session,
            settings=_workos_settings(),
            workos=workos,
        )

    assert_that(caught.value.status_code).is_equal_to(400)
    assert_that(caught.value.detail).is_equal_to("Sign-in state is invalid or expired")
    assert_that(caught.value.__context__).is_none()
    assert_that(caught.value.__cause__).is_none()
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

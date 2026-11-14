"""Shared helpers for the hosted WorkOS sign-in tests."""

from collections.abc import Callable
from http.cookies import SimpleCookie

import httpx
import httpx2
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from podex.api.deps import get_app_settings
from podex.api.v2 import auth as v2_auth_api
from podex.api.v2.auth_rejection import RejectedSignInCodeError
from podex.config import (
    AuthSettings,
    Settings,
)
from podex.services.workos_auth import (
    WorkOSAuthClient,
    WorkOSAuthError,
    WorkOSUserData,
)
from tests.conftest import create_reporting_app

PROFILE = WorkOSUserData(
    workos_user_id="user_01ABC",
    email="reader@example.com",
    first_name="Ada",
    last_name="Lovelace",
)


def workos_settings(*, secure_cookies: bool = True) -> Settings:
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
        profile: WorkOSUserData = PROFILE,
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


def override_workos(
    client: TestClient,
    workos: object,
    *,
    secure_cookies: bool = False,
) -> None:
    """Install WorkOS-enabled settings and a client override on the app."""
    app = client.app
    if not isinstance(app, FastAPI):  # pragma: no cover - narrowed
        raise AssertionError
    app.dependency_overrides[get_app_settings] = lambda: workos_settings(
        secure_cookies=secure_cookies,
    )
    app.dependency_overrides[v2_auth_api.get_workos_auth_client] = lambda: workos


def state_cookie(response: httpx2.Response) -> str:
    """Read the OAuth state value from the login response cookie."""
    cookie = SimpleCookie()
    for header in response.headers.get_list("set-cookie"):
        cookie.load(header)
    return cookie[v2_auth_api.WORKOS_STATE_COOKIE].value


CHAIN_API_KEY = "sk_test_789"


CHAIN_CODE = "auth-code-chain-321"


def texts_reachable_from(error: BaseException) -> list[str]:
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


INVALID_CODE_MESSAGE = "WorkOS rejected the authorization code (HTTP 400 invalid_grant)"


def refuse(request: httpx.Request) -> httpx.Response:
    """Fail like an unreachable WorkOS endpoint."""
    raise httpx.ConnectError("connection refused by api.workos.com", request=request)


def time_out(request: httpx.Request) -> httpx.Response:
    """Fail like a WorkOS endpoint that stops responding."""
    raise httpx.ReadTimeout("read timed out", request=request)


def invalid_code(_request: httpx.Request) -> httpx.Response:
    """Reject the code as invalid or expired (OAuth ``invalid_grant``)."""
    return httpx.Response(
        400,
        json={
            "error": "invalid_grant",
            "error_description": f"The code '{CHAIN_CODE}' has expired or is invalid.",
        },
    )


def invalid_client(_request: httpx.Request) -> httpx.Response:
    """Reject our credentials (invalid API key), echoing the code."""
    return httpx.Response(
        401,
        json={"error": "invalid_client", "error_description": CHAIN_CODE},
    )


def bad_request(_request: httpx.Request) -> httpx.Response:
    """Reject the request for a reason other than the code itself."""
    return httpx.Response(400, json={"error": "invalid_request", "code": CHAIN_CODE})


def bad_request_not_json(_request: httpx.Request) -> httpx.Response:
    """Reject with HTTP 400 and a body that is not JSON."""
    return httpx.Response(400, content=f"<html>{CHAIN_CODE}</html>".encode())


def unavailable(_request: httpx.Request) -> httpx.Response:
    """Fail like a WorkOS server error."""
    return httpx.Response(503, content=f"upstream {CHAIN_CODE}".encode())


def malformed(_request: httpx.Request) -> httpx.Response:
    """Answer 200 with a body that is not JSON and echoes the code."""
    return httpx.Response(200, content=f"<html>{CHAIN_CODE}</html>".encode())


FAILURE_MODES = [
    pytest.param(
        refuse,
        "WorkOS authentication request failed: ConnectError",
        id="connect-error",
    ),
    pytest.param(
        time_out,
        "WorkOS authentication request failed: ReadTimeout",
        id="timeout",
    ),
    pytest.param(
        invalid_code,
        INVALID_CODE_MESSAGE,
        id="invalid-code",
    ),
    pytest.param(
        invalid_client,
        "WorkOS code exchange rejected with HTTP 401",
        id="invalid-client",
    ),
    pytest.param(
        bad_request,
        "WorkOS code exchange rejected with HTTP 400",
        id="other-400",
    ),
    pytest.param(
        bad_request_not_json,
        "WorkOS code exchange rejected with HTTP 400",
        id="400-not-json",
    ),
    pytest.param(
        unavailable,
        "WorkOS code exchange rejected with HTTP 503",
        id="http-503",
    ),
    pytest.param(malformed, "WorkOS response was not valid JSON", id="malformed-json"),
]


def failing_workos(handler: Callable[[httpx.Request], httpx.Response]) -> object:
    """Return a real WorkOS client whose transport answers with ``handler``."""
    return WorkOSAuthClient(
        client_id="client_123",
        api_key=CHAIN_API_KEY,
        redirect_uri="https://app.example.com/api/v2/auth/callback",
        transport=httpx.MockTransport(handler),
    )


def reported_failed_callback(
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
    with TestClient(app, raise_server_exceptions=False) as reporting_client:
        override_workos(reporting_client, failing_workos(handler))
        login = reporting_client.get("/api/v2/auth/login", follow_redirects=False)
        state = state_cookie(login)
        response = reporting_client.get(
            "/api/v2/auth/callback",
            params={"code": CHAIN_CODE, "state": state},
            follow_redirects=False,
        )
    return response, state


def invalid_code_rejection() -> WorkOSAuthError:
    """Return the real error the exchange client raises for ``invalid_grant``.

    Returns:
        WorkOSAuthError: The ``WorkOSInvalidCodeError`` from a real exchange.
    """
    try:
        WorkOSAuthClient(
            client_id="client_123",
            api_key=CHAIN_API_KEY,
            redirect_uri="https://app.example.com/api/v2/auth/callback",
            transport=httpx.MockTransport(invalid_code),
        ).exchange_code(code=CHAIN_CODE)
    except WorkOSAuthError as error:
        return error
    raise AssertionError("expected the exchange to be rejected")


def handled_rejection(db: Session) -> Exception:
    """Return the real exception the sign-in callback raises for a rejected code.

    Args:
        db: Database session passed to the callback (not used on this path).

    Returns:
        Exception: The callback's ``RejectedSignInCodeError``.
    """
    request = Request(
        {
            "type": "http",
            "headers": [(b"cookie", f"{v2_auth_api.WORKOS_STATE_COOKIE}=s1".encode())],
        },
    )
    try:
        v2_auth_api.complete_workos_callback(
            code=CHAIN_CODE,
            state="s1",
            request=request,
            db=db,
            settings=workos_settings(),
            workos=WorkOSAuthClient(
                client_id="client_123",
                api_key=CHAIN_API_KEY,
                redirect_uri="https://app.example.com/api/v2/auth/callback",
                transport=httpx.MockTransport(invalid_code),
            ),
        )
    except RejectedSignInCodeError as error:
        rejected: Exception = error
        return rejected
    raise AssertionError("expected the callback to reject the code")

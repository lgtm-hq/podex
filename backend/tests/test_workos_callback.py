"""Tests for the hosted WorkOS sign-in routes and callback flow."""

import logging
from collections.abc import Callable
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
from podex.models import AccountUser, UserSession
from podex.services.limiter import RateLimitDecision, SlidingWindowRateLimiter
from podex.services.workos_auth import (
    build_workos_auth_client,
)
from tests.workos_support import (
    CHAIN_API_KEY,
    CHAIN_CODE,
    FAILURE_MODES,
    StubWorkOSClient,
    failing_workos,
    override_workos,
    state_cookie,
    workos_settings,
)


def _assert_state_expired(
    login: httpx2.Response,
    callback: httpx2.Response,
) -> None:
    """Require expiration of the same state cookie with all original attributes."""
    original = SimpleCookie(login.headers["set-cookie"])[
        v2_auth_api.WORKOS_STATE_COOKIE
    ]
    expired = SimpleCookie()
    for header in callback.headers.get_list("set-cookie"):
        expired.load(header)
    assert_that(expired).contains_key(v2_auth_api.WORKOS_STATE_COOKIE)
    state = expired[v2_auth_api.WORKOS_STATE_COOKIE]
    assert_that(state.value).is_empty()
    assert_that(state["max-age"]).is_equal_to("0")
    for attribute in ("path", "domain", "samesite", "secure", "httponly"):
        assert_that(state[attribute]).is_equal_to(original[attribute])


@pytest.mark.parametrize(
    ("failure", "expected_status"),
    [
        ("mismatch", 400),
        ("disabled", 404),
        ("missing-code", 422),
        ("missing-state", 422),
        ("empty-code", 400),
        ("empty-state", 400),
        ("method", 405),
        ("rate-limit", 429),
    ],
)
def test_unconsumed_callback_preserves_state_cookie(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    expected_status: int,
) -> None:
    """Rejected callbacks cannot consume another sign-in attempt's state."""
    workos = StubWorkOSClient()
    override_workos(client, workos)
    with TestClient(client.app, raise_server_exceptions=False) as callback_client:
        login = callback_client.get("/api/v2/auth/login", follow_redirects=False)
        params = {"code": "auth-code", "state": state_cookie(login)}
        if failure == "mismatch":
            params["state"] = "wrong-state"
        elif failure == "disabled":
            override_workos(client, None)
        elif failure.startswith("missing-"):
            del params[failure.removeprefix("missing-")]
        elif failure.startswith("empty-"):
            params[failure.removeprefix("empty-")] = ""
        elif failure == "rate-limit":
            monkeypatch.setattr(
                SlidingWindowRateLimiter,
                "check",
                lambda _self, _key: RateLimitDecision.rejected(limit=1, retry_after=60),
            )
        response = callback_client.request(
            "POST" if failure == "method" else "GET",
            "/api/v2/auth/callback",
            params=params,
            follow_redirects=False,
        )

        assert_that(response.status_code).is_equal_to(expected_status)
        assert_that(response.headers.get_list("set-cookie")).is_empty()
        assert_that(
            callback_client.cookies.get(v2_auth_api.WORKOS_STATE_COOKIE)
        ).is_equal_to(
            state_cookie(login),
        )
        assert_that(workos.codes).is_empty()


def test_stale_tab_callback_preserves_newer_sign_in(client: TestClient) -> None:
    """A stale tab's callback cannot delete the state belonging to a newer tab."""
    workos = StubWorkOSClient()
    override_workos(client, workos)
    first_login = client.get("/api/v2/auth/login", follow_redirects=False)
    second_login = client.get("/api/v2/auth/login", follow_redirects=False)
    assert_that(state_cookie(first_login)).is_not_equal_to(state_cookie(second_login))

    stale = client.get(
        "/api/v2/auth/callback",
        params={"code": "first-tab-code", "state": state_cookie(first_login)},
        follow_redirects=False,
    )

    assert_that(stale.status_code).is_equal_to(400)
    assert_that(stale.headers.get_list("set-cookie")).is_empty()
    assert_that(client.cookies.get(v2_auth_api.WORKOS_STATE_COOKIE)).is_equal_to(
        state_cookie(second_login),
    )
    valid = client.get(
        "/api/v2/auth/callback",
        params={"code": "second-tab-code", "state": state_cookie(second_login)},
        follow_redirects=False,
    )
    assert_that(valid.status_code).is_equal_to(307)
    assert_that(workos.codes).is_equal_to(["second-tab-code"])


@pytest.mark.parametrize("secure_cookies", [False, True])
@pytest.mark.parametrize("expected_status", [409, 500])
def test_consumed_callback_error_expires_state_cookie(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    secure_cookies: bool,
    expected_status: int,
) -> None:
    """Account and unexpected errors after state validation expire that cookie."""
    override_workos(client, StubWorkOSClient(), secure_cookies=secure_cookies)

    def fail_upsert(**_kwargs: object) -> None:
        """Simulate an account error after the callback consumes its state."""
        if expected_status == 409:
            raise HTTPException(status_code=409, detail="Account conflict")
        raise RuntimeError("persistence failed")

    monkeypatch.setattr(v2_auth_api, "upsert_workos_account_user", fail_upsert)
    with TestClient(
        client.app,
        base_url="https://testserver",
        raise_server_exceptions=False,
    ) as callback_client:
        login = callback_client.get("/api/v2/auth/login", follow_redirects=False)
        response = callback_client.get(
            "/api/v2/auth/callback",
            params={"code": "auth-code", "state": state_cookie(login)},
            follow_redirects=False,
        )

        assert_that(response.status_code).is_equal_to(expected_status)
        _assert_state_expired(login, response)
        assert_that(callback_client.cookies).does_not_contain(
            v2_auth_api.WORKOS_STATE_COOKIE,
        )


@pytest.mark.parametrize("secure_cookies", [False, True])
def test_successful_callback_expires_matching_state_cookie(
    client: TestClient,
    secure_cookies: bool,
) -> None:
    """Successful callbacks expire state with the attributes used at login."""
    override_workos(client, StubWorkOSClient(), secure_cookies=secure_cookies)
    with TestClient(client.app, base_url="https://testserver") as callback_client:
        login = callback_client.get("/api/v2/auth/login", follow_redirects=False)
        response = callback_client.get(
            "/api/v2/auth/callback",
            params={"code": "auth-code", "state": state_cookie(login)},
            follow_redirects=False,
        )

        assert_that(response.status_code).is_equal_to(307)
        _assert_state_expired(login, response)


@pytest.mark.parametrize(("handler", "expected_message"), FAILURE_MODES)
def test_callback_logs_no_oauth_credentials(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
    handler: Callable[[httpx.Request], httpx.Response],
    expected_message: str,
) -> None:
    """A failed callback logs path, status and failure type, never secrets."""
    override_workos(client, failing_workos(handler))
    login = client.get("/api/v2/auth/login", follow_redirects=False)
    state = state_cookie(login)

    with caplog.at_level(logging.DEBUG):
        response = client.get(
            "/api/v2/auth/callback",
            params={"code": CHAIN_CODE, "state": state},
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
        for leaked in (CHAIN_CODE, state, CHAIN_API_KEY, "sk_test_456"):
            assert_that(line).does_not_contain(leaked)


def test_status_reports_workos_disabled_by_default(client: TestClient) -> None:
    """The public status probe advertises hosted sign-in as off."""
    response = client.get("/api/v2/status")

    assert_that(response.status_code).is_equal_to(200)
    assert_that(response.json()["workos_enabled"]).is_false()


def test_status_reports_workos_enabled_when_configured(client: TestClient) -> None:
    """The public status probe flips once credentials are configured."""
    override_workos(client, StubWorkOSClient())

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
    app.dependency_overrides[get_app_settings] = workos_settings
    app.dependency_overrides[v2_auth_api.get_workos_auth_client] = lambda: (
        build_workos_auth_client(settings=workos_settings())
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
    state = state_cookie(response)
    assert_that(query["state"]).is_equal_to([state])
    set_cookie = response.headers["set-cookie"]
    assert_that(set_cookie).contains("HttpOnly", "Max-Age=600", "Secure")


def test_callback_rejects_state_mismatch(client: TestClient) -> None:
    """A state value that does not match the cookie is rejected."""
    workos = StubWorkOSClient()
    override_workos(client, workos)
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
    override_workos(client, workos)
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
            settings=workos_settings(),
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
    override_workos(client, workos)

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
    override_workos(client, workos)
    login = client.get("/api/v2/auth/login", follow_redirects=False)
    state = state_cookie(login)

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
    override_workos(client, workos)
    for _ in range(2):
        login = client.get("/api/v2/auth/login", follow_redirects=False)
        response = client.get(
            "/api/v2/auth/callback",
            params={"code": "auth-code", "state": state_cookie(login)},
            follow_redirects=False,
        )
        assert_that(response.status_code).is_equal_to(307)

    users = db_session.execute(select(AccountUser)).scalars().all()
    assert_that(users).is_length(1)
    sessions = db_session.execute(select(UserSession)).scalars().all()
    assert_that(sessions).is_length(2)


@pytest.mark.parametrize("secure_cookies", [False, True])
def test_callback_maps_exchange_failure_to_bad_gateway(
    client: TestClient,
    secure_cookies: bool,
) -> None:
    """A rejected code exchange surfaces as an upstream failure."""
    override_workos(client, StubWorkOSClient(fail=True), secure_cookies=secure_cookies)
    with TestClient(client.app, base_url="https://testserver") as callback_client:
        login = callback_client.get("/api/v2/auth/login", follow_redirects=False)
        response = callback_client.get(
            "/api/v2/auth/callback",
            params={"code": "auth-code", "state": state_cookie(login)},
            follow_redirects=False,
        )

        assert_that(response.status_code).is_equal_to(502)
        # Default policy: a transient exchange failure requires a fresh sign-in too.
        _assert_state_expired(login, response)
        assert_that(callback_client.cookies).does_not_contain(
            v2_auth_api.WORKOS_STATE_COOKIE
        )

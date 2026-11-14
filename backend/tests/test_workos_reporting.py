"""Tests for how hosted sign-in failures reach the error reporter."""

import ast
from collections.abc import Callable
from pathlib import Path

import httpx
import httpx2
import pytest
from assertpy import assert_that
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from podex.api.v2 import auth as v2_auth_api
from podex.api.v2.auth_rejection import RejectedSignInCodeError
from podex.services.workos_auth import WorkOSInvalidCodeError
from tests.conftest import SentryEnvelopeRecorder
from tests.workos_support import (
    CHAIN_API_KEY,
    CHAIN_CODE,
    FAILURE_MODES,
    INVALID_CODE_MESSAGE,
    failing_workos,
    invalid_client,
    invalid_code,
    override_workos,
    reported_failed_callback,
    state_cookie,
)


@pytest.mark.parametrize(("handler", "expected_message"), FAILURE_MODES)
def test_failed_callback_reports_no_credentials(
    db_session: Session,
    sentry_transport: SentryEnvelopeRecorder,
    handler: Callable[[httpx.Request], httpx.Response],
    expected_message: str,
) -> None:
    """Failures are reported usefully without credentials, except bad codes.

    A rejected authorization code is caller-triggerable (anyone can replay
    or forge one), so it is only logged; every other failure is reported
    exactly once.
    """
    response, state = reported_failed_callback(db_session, handler)

    assert_that(response.status_code).is_equal_to(502)
    wire = sentry_transport.serialized()
    for leaked in (CHAIN_API_KEY, "sk_test_456", CHAIN_CODE, state):
        assert_that(wire).does_not_contain(leaked)
    events = sentry_transport.events()
    if expected_message == INVALID_CODE_MESSAGE:
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


_HANDLING_FAILURES = [
    pytest.param(OSError("log volume unavailable"), id="logger-oserror"),
    pytest.param(
        OperationalError("SELECT 1", {}, Exception("database unavailable")),
        id="database-error",
    ),
    pytest.param(TypeError("handler bug"), id="handler-bug"),
]


@pytest.mark.parametrize("failure", _HANDLING_FAILURES)
@pytest.mark.parametrize(
    "handler",
    [
        pytest.param(invalid_code, id="after-invalid-code"),
        pytest.param(invalid_client, id="after-invalid-client"),
    ],
)
def test_failure_while_handling_a_rejection_is_reported(
    db_session: Session,
    sentry_transport: SentryEnvelopeRecorder,
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
    failure: Exception,
) -> None:
    """A server error raised while handling any rejection is never hidden.

    The rejection is still in the new error's context, but only the
    rejection itself (or its handled 502) is exempt from reporting, so a
    caller cannot hide a real server error by first sending a bad code.
    """

    def failing_warning(*_args: object, **_kwargs: object) -> None:
        """Fail like a broken log handler, database call or handler bug."""
        raise failure

    monkeypatch.setattr(v2_auth_api.logger, "warning", failing_warning)

    response, state = reported_failed_callback(db_session, handler)

    assert_that(response.status_code).is_equal_to(500)
    events = sentry_transport.events()
    assert_that(events).is_length(1)
    reported_types = [value["type"] for value in events[0]["exception"]["values"]]
    assert_that(reported_types).contains(type(failure).__name__)
    wire = sentry_transport.serialized()
    for leaked in (CHAIN_API_KEY, "sk_test_456", CHAIN_CODE, state):
        assert_that(wire).does_not_contain(leaked)


def test_replayed_invalid_codes_send_no_reports(
    db_session: Session,
    sentry_transport: SentryEnvelopeRecorder,
) -> None:
    """Repeated bad-code callbacks never spend reporting quota."""
    for _ in range(5):
        response, _state = reported_failed_callback(db_session, invalid_code)
        assert_that(response.status_code).is_equal_to(502)

    assert_that(sentry_transport.events()).is_empty()


def test_foreign_502_raised_from_a_rejection_is_reported(
    db_session: Session,
    sentry_transport: SentryEnvelopeRecorder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the callback's own handled 502 is dropped, not a look-alike."""

    def foreign_502(_message: str, error: BaseException) -> None:
        """Raise a different 502 from the rejection while handling it."""
        raise HTTPException(status_code=502, detail="other failure") from error

    monkeypatch.setattr(v2_auth_api.logger, "warning", foreign_502)

    response, state = reported_failed_callback(db_session, invalid_code)

    assert_that(response.status_code).is_equal_to(502)
    events = sentry_transport.events()
    assert_that(events).is_length(1)
    reported_types = [value["type"] for value in events[0]["exception"]["values"]]
    assert_that(reported_types).contains("HTTPException", "WorkOSInvalidCodeError")
    wire = sentry_transport.serialized()
    for leaked in (CHAIN_API_KEY, "sk_test_456", CHAIN_CODE, state):
        assert_that(wire).does_not_contain(leaked)


def test_dedicated_rejection_types_cannot_be_constructed_elsewhere() -> None:
    """Both dropped types need a module-private token and refuse subclasses."""
    pytest.raises(TypeError, WorkOSInvalidCodeError, "forged", token=object())
    pytest.raises(TypeError, RejectedSignInCodeError, token=object())
    with pytest.raises(TypeError):
        type("ForgedRejection", (WorkOSInvalidCodeError,), {})
    with pytest.raises(TypeError):
        type("ForgedHandled", (RejectedSignInCodeError,), {})


@pytest.mark.parametrize(
    ("name", "only_module"),
    [
        ("WorkOSInvalidCodeError", "services/workos_auth.py"),
        ("RejectedSignInCodeError", "api/v2/auth_rejection.py"),
        ("rejected_sign_in_code", "api/v2/auth.py"),
    ],
)
def test_dedicated_rejection_types_are_raised_in_one_place(
    name: str,
    only_module: str,
) -> None:
    """Source scan: each dropped type, and the one factory, has one call site."""
    package = Path(__file__).resolve().parents[1] / "src" / "podex"
    calling = sorted(
        str(path.relative_to(package))
        for path in package.rglob("*.py")
        if name in _called_names(path)
    )

    assert_that(calling).is_equal_to([only_module])


def _called_names(path: Path) -> set[str]:
    """Return the names of every function or class called in ``path``."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call):
            target = node.func
            if isinstance(target, ast.Name):
                names.add(target.id)
            elif isinstance(target, ast.Attribute):
                names.add(target.attr)
    return names


def _callback_response(
    client: TestClient,
    handler: Callable[[httpx.Request], httpx.Response],
    request_id: str,
) -> httpx2.Response:
    """Run one failing sign-in on ``client`` with a fixed request id."""
    override_workos(client, failing_workos(handler))
    login = client.get("/api/v2/auth/login", follow_redirects=False)
    response: httpx2.Response = client.get(
        "/api/v2/auth/callback",
        params={"code": CHAIN_CODE, "state": state_cookie(login)},
        headers={"X-Request-ID": request_id},
        follow_redirects=False,
    )
    return response


def _comparable_headers(
    headers: list[tuple[str, str]],
) -> list[tuple[str, tuple[str, ...]]]:
    """Return ``headers`` in a form two requests can be compared by.

    Two things legitimately differ between otherwise identical responses and
    are left out: the shared rate-limit budget's counters, and the ``expires``
    timestamp of a cleared cookie, which is the wall-clock second of the
    request. A cleared cookie must still carry ``Max-Age=0``, so dropping the
    timestamp cannot hide a cookie that is not expired.

    Args:
        headers: The response's header pairs in order.

    Returns:
        The header pairs with the request-dependent parts removed.
    """
    budget = {"x-ratelimit-remaining", "x-ratelimit-reset"}
    comparable: list[tuple[str, tuple[str, ...]]] = []
    for name, value in headers:
        if name in budget:
            continue
        if name != "set-cookie":
            comparable.append((name, (value,)))
            continue
        attributes = tuple(part.strip() for part in value.split(";"))
        timestamps = [part for part in attributes if part.startswith("expires=")]
        if timestamps:
            assert_that(attributes).contains("Max-Age=0")
        comparable.append(
            (name, tuple(part for part in attributes if part not in timestamps)),
        )
    return comparable


def test_rejected_code_response_matches_the_generic_502(client: TestClient) -> None:
    """The bad-code 502 matches the generic failed-exchange 502."""
    rejected = _callback_response(client, invalid_code, "same-request-id")
    generic = _callback_response(client, invalid_client, "same-request-id")

    assert_that(rejected.status_code).is_equal_to(502)
    assert_that(generic.status_code).is_equal_to(502)
    assert_that(rejected.content).is_equal_to(generic.content)
    assert_that(_comparable_headers(rejected.headers.multi_items())).is_equal_to(
        _comparable_headers(generic.headers.multi_items()),
    )


_CLEARED_AT_38 = (
    'podex_oauth_state=""; expires=Sat, 14 Nov 2026 14:40:38 GMT; HttpOnly; '
    "Max-Age=0; Path=/; SameSite=lax"
)
_CLEARED_AT_39 = _CLEARED_AT_38.replace("14:40:38", "14:40:39")


def test_header_comparison_ignores_the_second_a_cookie_was_cleared() -> None:
    """Two responses a second apart compare equal; any other change does not."""
    earlier = [("content-type", "application/json"), ("set-cookie", _CLEARED_AT_38)]
    later = [("content-type", "application/json"), ("set-cookie", _CLEARED_AT_39)]

    assert_that(_comparable_headers(earlier)).is_equal_to(_comparable_headers(later))

    for changed in (
        [("content-type", "text/plain"), ("set-cookie", _CLEARED_AT_39)],
        [
            ("content-type", "application/json"),
            ("set-cookie", _CLEARED_AT_39.replace("Path=/", "Path=/api")),
        ],
        [("content-type", "application/json")],
        [*later, ("x-extra", "1")],
    ):
        assert_that(_comparable_headers(changed)).is_not_equal_to(
            _comparable_headers(earlier),
        )


def test_header_comparison_rejects_a_dated_cookie_that_is_not_expired() -> None:
    """A cookie with an ``expires`` date must also carry ``Max-Age=0``."""
    live = _CLEARED_AT_38.replace("Max-Age=0", "Max-Age=600")

    with pytest.raises(AssertionError):
        _comparable_headers([("set-cookie", live)])

"""Error reports from credential-bearing requests carry no credentials.

Each test runs the real application with the error reporter initialised
through ``init_sentry`` and an in-memory transport, triggers an unhandled
exception, and inspects the complete serialised envelopes.
"""

import hmac
import json
import time
from collections.abc import Iterator

import pytest
from assertpy import assert_that
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from podex.api.deps import get_app_settings
from podex.config import BillingSettings, Settings
from tests.conftest import (
    CapturingSentryTransport,
    create_reporting_app,
    enable_ops,
)

_OPS_KEY = "ops-key-report-7310"
_PADDLE_SIGNING_KEY = "whsec_test_signing"
_SESSION_COOKIE = "podex_session=session-cookie-5512"
_USER_AGENT = "podex-report-test/1.0"
_REQUEST_ID = "req-report-42"
_PEER = "testclient:50000"


def _paddle_signature(body: bytes) -> str:
    """Return a valid Paddle ``ts``/``h1`` signature header for ``body``."""
    timestamp = str(int(time.time()))
    mac = hmac.new(
        _PADDLE_SIGNING_KEY.encode(), timestamp.encode() + b":" + body, "sha256"
    )
    return f"ts={timestamp};h1={mac.hexdigest()}"


def _boom(**_kwargs: object) -> object:
    """Stand in for a service call that fails unexpectedly."""
    raise RuntimeError("service failed")


@pytest.fixture(name="reporting_client")
def reporting_client_fixture(
    db_session: Session,
    sentry_transport: CapturingSentryTransport,
) -> Iterator[TestClient]:
    """Return a client for an app that reports errors to memory.

    Yields:
        TestClient: Client whose unhandled errors return 500 responses.
    """
    del sentry_transport  # requested so init_sentry uses the in-memory transport
    app = create_reporting_app(db_session)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def _only_event(transport: CapturingSentryTransport) -> dict[str, object]:
    """Return the single captured error event."""
    events = transport.events()
    assert_that(events).is_length(1)
    event: dict[str, object] = events[0]
    return event


def _assert_reported_headers(event: dict[str, object], *extra: str) -> None:
    """Assert the report keeps exactly the allowlisted request headers."""
    request = event["request"]
    if not isinstance(request, dict):
        raise AssertionError("expected request data in the report")
    headers = request["headers"]
    assert_that(headers).contains_entry(
        {"user-agent": _USER_AGENT},
        {"x-request-id": _REQUEST_ID},
    )
    assert_that(set(headers)).is_subset_of(
        {"user-agent", "x-request-id", "content-type", "content-length", "host"},
    )
    for name in extra:
        assert_that(headers).contains_key(name)
    assert_that(request).does_not_contain_key("cookies", "data", "env")


def test_ops_failure_report_omits_ops_key_cookie_and_peer(
    reporting_client: TestClient,
    sentry_transport: CapturingSentryTransport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unhandled error on an ops route reports no credential header."""
    enable_ops(reporting_client, api_key=_OPS_KEY)
    monkeypatch.setattr("podex.api.v2.ops.get_operational_metrics", _boom)

    response = reporting_client.get(
        "/api/v2/ops/metrics",
        headers={
            "X-Ops-Key": _OPS_KEY,
            "Authorization": "Bearer bearer-token-6630",
            "Cookie": _SESSION_COOKIE,
            "User-Agent": _USER_AGENT,
            "X-Request-ID": _REQUEST_ID,
        },
    )

    assert_that(response.status_code).is_equal_to(500)
    event = _only_event(sentry_transport)
    _assert_reported_headers(event)
    wire = sentry_transport.serialized()
    for leaked in (_OPS_KEY, "bearer-token-6630", "session-cookie-5512", _PEER):
        assert_that(wire).does_not_contain(leaked)
    assert_that(wire).does_not_contain("client_addr")


def test_webhook_failure_report_omits_signature_and_body(
    reporting_client: TestClient,
    sentry_transport: CapturingSentryTransport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unhandled error on a verified webhook reports no signature or body."""
    app = reporting_client.app
    if not isinstance(app, FastAPI):
        raise AssertionError("expected a FastAPI test app")
    app.dependency_overrides[get_app_settings] = lambda: Settings(
        billing=BillingSettings(paddle_webhook_secret=_PADDLE_SIGNING_KEY),
    )
    monkeypatch.setattr("podex.api.v2.billing.apply_paddle_subscription_event", _boom)
    body = json.dumps(
        {
            "event_id": "evt_report_1",
            "event_type": "subscription.activated",
            "data": {"customer_id": "ctm_body_marker_9431", "email": "payer@x.test"},
        },
    ).encode("utf-8")
    signature = _paddle_signature(body)

    response = reporting_client.post(
        "/api/v2/billing/webhooks/paddle",
        content=body,
        headers={
            "Paddle-Signature": signature,
            "Content-Type": "application/json",
            "User-Agent": _USER_AGENT,
            "X-Request-ID": _REQUEST_ID,
        },
    )

    assert_that(response.status_code).is_equal_to(500)
    event = _only_event(sentry_transport)
    _assert_reported_headers(event, "content-type")
    wire = sentry_transport.serialized()
    digest = signature.split("h1=", 1)[1]
    for leaked in (signature, digest, "ctm_body_marker_9431", "payer@x.test"):
        assert_that(wire).does_not_contain(leaked)

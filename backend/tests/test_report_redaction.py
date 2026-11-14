"""Report redaction rewrites free text and data, never event structure.

Each test builds the application with ``create_app`` (which initialises the
reporter as in production) and an in-memory transport, fails a request in a
real route handler, and inspects the complete serialised envelope.
"""

import json
import logging
import re
from collections.abc import Iterator
from datetime import datetime
from secrets import token_hex
from typing import Any
from urllib.parse import quote

import pytest
import sentry_sdk
from assertpy import assert_that
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.orm import Session

from podex import __version__
from podex.config import (
    AuthSettings,
    DatabaseSettings,
    ObservabilitySettings,
    RateLimitSettings,
    Settings,
)
from podex.database import get_db
from podex.main import create_app
from tests.conftest import SentryEnvelopeRecorder

_logger = logging.getLogger("podex.tests.report_redaction")
_HEX_ID = re.compile(r"[0-9a-f]{32}")
_MARKER = "[redacted]"
_ONE_CHARACTER = "e"
_QUERY_PASSWORD = f"redis-query-{token_hex(6)}"
_SSL_PASSWORD = f"pg-ssl-{token_hex(6)}"
_ENCODED_KEY = f"k+v/{token_hex(6)}"
_APOSTROPHE_PASSWORD = f"pa'ss{token_hex(6)}"
_SURROGATE_PASSWORD = f"pw\udcff{token_hex(6)}"


def _mixed_case_escapes(value: str) -> str:
    """Percent-encode ``value``, lowering the hex digits of every other escape."""
    escapes = iter(range(1_000))
    return re.sub(
        r"%[0-9A-F]{2}",
        lambda match: match.group(0).lower() if next(escapes) % 2 else match.group(0),
        quote(value, safe=""),
    )


async def _leaky_route() -> None:
    """Fail after recording free text and data in every redacted place."""
    _logger.info("loading the queue before the failure")
    sentry_sdk.set_extra("note", "extra value here")
    sentry_sdk.set_context("custom", {"detail": "context value here"})
    sentry_sdk.add_breadcrumb(
        category="outbound",
        message="sent a request",
        data={
            "request": {"headers": {"Authorization": "Bearer tok", "User-Agent": "a"}}
        },
    )
    raise RuntimeError("the queue failed here")


async def _credential_route() -> None:
    """Fail with every configured credential form in the message."""
    raise RuntimeError(
        f"redis password={_QUERY_PASSWORD}; sslpassword={_SSL_PASSWORD}; "
        f"key={_mixed_case_escapes(_ENCODED_KEY)}; "
        f"feed https://reader:{_APOSTROPHE_PASSWORD}@feeds.invalid/rss",
    )


def _client(settings: Settings, db_session: Session) -> Iterator[TestClient]:
    """Yield a client for an app built from ``settings`` with failing routes."""
    app: FastAPI = create_app(settings)

    def override_get_db() -> Iterator[Session]:
        """Yield the test database session in place of the real one."""
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.add_api_route("/leaky", _leaky_route, methods=["GET"])
    app.add_api_route("/credentials", _credential_route, methods=["GET"])
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


_REPORTING = ObservabilitySettings(sentry_dsn="https://public@sentry.invalid/1")


def _only_event(transport: SentryEnvelopeRecorder) -> dict[str, Any]:
    """Return the single reported event, after checking the wire is JSON."""
    for line in transport.serialized().splitlines():
        json.loads(line)
    events = transport.events()
    assert_that(events).is_length(1)
    event: dict[str, Any] = events[0]
    return event


def _unmarked(text: str) -> str:
    """Return ``text`` without redaction markers."""
    return text.replace(_MARKER, "")


@pytest.fixture(name="short_secret_client")
def short_secret_client_fixture(
    db_session: Session,
    sentry_transport: SentryEnvelopeRecorder,
) -> Iterator[TestClient]:
    """Return a client whose settings hold a one-character secret.

    Yields:
        TestClient: Client for the reporting app.
    """
    del sentry_transport  # requested so init_sentry uses the in-memory transport
    settings = Settings(
        rate_limit=RateLimitSettings(enabled=False),
        observability=_REPORTING,
        auth=AuthSettings(workos_api_key=SecretStr(_ONE_CHARACTER)),
    )
    yield from _client(settings, db_session)


def test_one_character_secret_leaves_a_valid_event(
    short_secret_client: TestClient,
    sentry_transport: SentryEnvelopeRecorder,
) -> None:
    """Structure stays valid while every free-text and data field is redacted."""
    response = short_secret_client.get(
        "/leaky",
        headers={"User-Agent": "agent-here"},
    )

    assert_that(response.status_code).is_equal_to(500)
    event = _only_event(sentry_transport)
    assert_that(event["level"]).is_equal_to("error")
    assert_that(event["platform"]).is_equal_to("python")
    assert_that(event["release"]).is_equal_to(f"podex@{__version__}")
    assert_that(event["environment"]).is_equal_to("production")
    assert_that(_HEX_ID.fullmatch(str(event["event_id"]))).is_not_none()
    datetime.fromisoformat(str(event["timestamp"]))
    contexts = event["contexts"]
    breadcrumbs = event["breadcrumbs"]
    assert_that(event["sdk"]["name"]).starts_with("sentry.python")
    assert_that(_HEX_ID.fullmatch(contexts["trace"]["trace_id"])).is_not_none()
    error = event["exception"]["values"][-1]
    assert_that(error["type"]).is_equal_to("RuntimeError")
    redacted = [
        error["value"],
        event["extra"]["note"],
        contexts["custom"]["detail"],
        event["request"]["headers"]["user-agent"],
        *(crumb["message"] for crumb in breadcrumbs["values"] if crumb.get("message")),
    ]
    for text in redacted:
        assert_that(text).contains(_MARKER)
        assert_that(_unmarked(text)).does_not_contain(_ONE_CHARACTER)
    outbound = [c for c in breadcrumbs["values"] if c.get("category") == "outbound"]
    assert_that(outbound).is_length(1)
    assert_that(outbound[0]["data"]["request"]["headers"]).is_equal_to(
        {"User-Agent": "a"}
    )
    assert_that(outbound[0]["type"]).is_equal_to("default")


@pytest.fixture(name="credential_client")
def credential_client_fixture(
    db_session: Session,
    sentry_transport: SentryEnvelopeRecorder,
) -> Iterator[TestClient]:
    """Return a client whose settings hold every credential form.

    Yields:
        TestClient: Client for the reporting app.
    """
    del sentry_transport  # requested so init_sentry uses the in-memory transport
    settings = Settings(
        observability=_REPORTING,
        database=DatabaseSettings(
            url=SecretStr(
                f"postgresql+psycopg://podex@db.invalid/podex?sslpassword={_SSL_PASSWORD}",
            ),
        ),
        rate_limit=RateLimitSettings(
            enabled=False,
            redis_url=SecretStr(
                f"redis://redis.invalid:6379/0?password={_QUERY_PASSWORD}",
            ),
        ),
        auth=AuthSettings(
            workos_api_key=SecretStr(_ENCODED_KEY),
            smtp_password=SecretStr(_SURROGATE_PASSWORD),
        ),
    )
    yield from _client(settings, db_session)


def test_credential_forms_are_redacted_in_the_report(
    credential_client: TestClient,
    sentry_transport: SentryEnvelopeRecorder,
) -> None:
    """Query options, mixed-case escapes and apostrophe userinfo never leak.

    A configured secret without a UTF-8 encoding does not stop the reporter.
    """
    response = credential_client.get("/credentials")

    assert_that(response.status_code).is_equal_to(500)
    event = _only_event(sentry_transport)
    wire = sentry_transport.serialized()
    for leaked in (
        _QUERY_PASSWORD,
        _SSL_PASSWORD,
        _mixed_case_escapes(_ENCODED_KEY),
        _APOSTROPHE_PASSWORD,
    ):
        assert_that(wire).does_not_contain(leaked)
    assert_that(event["exception"]["values"][-1]["value"]).contains(
        "https://[redacted]@feeds.invalid/rss",
    )

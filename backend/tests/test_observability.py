"""Tests for Sentry initialization and PII scrubbing."""

from collections.abc import Callable
from unittest.mock import patch

import httpx
import pytest
import sentry_sdk
from assertpy import assert_that
from sentry_sdk.scrubber import DEFAULT_DENYLIST, EventScrubber
from sentry_sdk.types import Event
from starlette.exceptions import HTTPException

from podex import __version__
from podex.config import ObservabilitySettings, RateLimitSettings, Settings
from podex.logging_config import configure_logging
from podex.main import create_app
from podex.observability import before_send, init_sentry, scrub_event
from podex.scheduler_runner import main as scheduler_main
from tests.conftest import CapturingSentryTransport

_TEST_DSN = "https://public@sentry.invalid/1"


def test_init_sentry_noops_without_dsn() -> None:
    """The SDK stays fully disabled when no DSN is configured."""
    settings = Settings(observability=ObservabilitySettings(sentry_dsn=""))
    with patch("podex.observability.sentry_sdk.init") as mock_init:
        result = init_sentry(settings)
    assert_that(result).is_false()
    mock_init.assert_not_called()


def test_init_sentry_initializes_with_dsn() -> None:
    """A configured DSN initializes the SDK with environment and release."""
    settings = Settings(
        observability=ObservabilitySettings(
            sentry_dsn="https://key@sentry.example/1",
            sentry_environment="staging",
        ),
    )
    with patch("podex.observability.sentry_sdk.init") as mock_init:
        result = init_sentry(settings)
    assert_that(result).is_true()
    mock_init.assert_called_once()
    options = dict(mock_init.call_args.kwargs)
    scrubber = options.pop("event_scrubber")
    assert_that(options).is_equal_to(
        {
            "dsn": "https://key@sentry.example/1",
            "environment": "staging",
            "release": f"podex@{__version__}",
            "before_send": before_send,
            "send_default_pii": False,
            "include_local_variables": False,
            "max_request_body_size": "never",
        },
    )
    assert_that(scrubber).is_instance_of(EventScrubber)
    assert_that(scrubber.recursive).is_true()
    # The scrubber matches keys case-insensitively and stores them lowercased.
    assert_that(scrubber.denylist).contains(
        *(entry.lower() for entry in DEFAULT_DENYLIST),
    )
    assert_that(scrubber.denylist).contains(
        "http.query", "http.fragment", "code", "state", "client_secret"
    )


def test_init_sentry_defaults_keep_sdk_disabled() -> None:
    """Default settings carry an empty DSN so Sentry never activates."""
    settings = Settings()
    assert_that(settings.observability.sentry_dsn).is_empty()
    assert_that(settings.observability.sentry_environment).is_equal_to("production")


def test_scrub_event_redacts_emails_in_message() -> None:
    """Email addresses in the event message are redacted."""
    event: Event = {
        "message": "login failed for alice.smith+test@example.co.uk today",
    }
    scrubbed = scrub_event(event, {})
    assert_that(scrubbed["message"]).is_equal_to(
        "login failed for [redacted-email] today",
    )


def test_scrub_event_redacts_emails_in_extra() -> None:
    """String extra values are scrubbed; non-strings pass through unchanged."""
    event: Event = {
        "extra": {
            "recipient": "bob@example.com",
            "attempts": 3,
            "note": "cc dana@example.org and eve@example.net",
        },
    }
    scrubbed = scrub_event(event, {})
    assert_that(scrubbed["extra"]).is_equal_to(
        {
            "recipient": "[redacted-email]",
            "attempts": 3,
            "note": "cc [redacted-email] and [redacted-email]",
        },
    )


def test_scrub_event_drops_user_email() -> None:
    """The user email is dropped and remaining user strings are scrubbed."""
    event: Event = {
        "user": {
            "email": "carol@example.com",
            "id": "u-1",
            "username": "carol@example.com",
        },
    }
    scrubbed = scrub_event(event, {})
    assert_that(scrubbed["user"]).is_equal_to(
        {"id": "u-1", "username": "[redacted-email]"},
    )


def test_scrub_event_leaves_clean_events_untouched() -> None:
    """Events without messages, extras, or users pass through unchanged."""
    event: Event = {"level": "error"}
    scrubbed = scrub_event(event, {})
    assert_that(scrubbed).is_equal_to({"level": "error"})


def test_create_app_initializes_sentry() -> None:
    """The API app factory initializes Sentry with the resolved settings."""
    settings = Settings(rate_limit=RateLimitSettings(enabled=False))
    with patch("podex.main.init_sentry") as mock_init:
        create_app(settings)
    mock_init.assert_called_once_with(settings)


def test_scheduler_main_initializes_sentry() -> None:
    """The scheduler process initializes Sentry independently."""
    with (
        patch("podex.scheduler_runner.init_sentry") as mock_init,
        patch("podex.scheduler_runner.SchedulerRunner") as mock_runner,
    ):
        scheduler_main()
    mock_init.assert_called_once()
    mock_runner.return_value.run_forever.assert_called_once_with()


def test_scrub_event_drops_query_strings_on_every_route() -> None:
    """Request query strings never leave the process, whatever the route."""
    event: Event = {
        "request": {
            "url": "http://testserver/api/v2/podcasts?token=query-secret#frag",
            "query_string": "token=query-secret&code=query-code",
            "method": "GET",
        },
    }
    scrubbed = scrub_event(event, {})
    assert_that(scrubbed["request"]).is_equal_to(
        {"url": "http://testserver/api/v2/podcasts", "method": "GET"},
    )


def test_outbound_request_breadcrumbs_keep_provider_keys_out(
    sentry_transport: CapturingSentryTransport,
) -> None:
    """Provider API keys sent in a query never reach a reported event."""
    provider_key = "omdb-query-key-4821"
    init_sentry(Settings(observability=ObservabilitySettings(sentry_dsn=_TEST_DSN)))
    configure_logging()
    with httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(503)),
    ) as http:
        http.get(
            "https://www.omdbapi.com/",
            params={"apikey": provider_key, "i": "tt0111161"},
        )
    try:
        raise RuntimeError("enrichment provider failed")
    except RuntimeError as error:
        sentry_sdk.capture_exception(error)

    events = sentry_transport.events()
    assert_that(events).is_length(1)
    crumbs = events[0]["breadcrumbs"]["values"]
    http_crumbs = [crumb for crumb in crumbs if crumb.get("type") == "http"]
    assert_that(http_crumbs).is_length(1)
    assert_that(http_crumbs[0]["data"]["url"]).is_equal_to("https://www.omdbapi.com/")
    assert_that(sentry_transport.serialized()).does_not_contain(provider_key)


class _ExpectedRejection(Exception):
    """A caller-triggerable failure used to exercise the drop rule."""

    reportable = False


def _raised_from(cause: BaseException) -> BaseException:
    """Return a RuntimeError raised from ``cause`` with its chain attached."""
    try:
        raise RuntimeError("wrapped") from cause
    except RuntimeError as error:
        return error


def _raise_while_handling(
    rejection: BaseException,
    error: BaseException,
    *,
    explicit_cause: bool,
    hide_context: bool = False,
) -> BaseException:
    """Attach ``rejection`` to ``error`` as Python's ``raise`` statement would.

    Raising ``error`` inside ``except rejection`` always sets ``__context__``;
    ``raise error from rejection`` also sets ``__cause__``, and ``raise
    error from None`` sets ``__suppress_context__`` while keeping the context.

    Args:
        rejection: The exception being handled.
        error: The exception raised while handling it.
        explicit_cause: Whether ``error`` is raised ``from rejection``.
        hide_context: Whether ``error`` is raised ``from None``.

    Returns:
        BaseException: ``error`` with its cause and context attached.
    """
    error.__context__ = rejection
    if explicit_cause:
        error.__cause__ = rejection
    error.__suppress_context__ = explicit_cause or hide_context
    return error


def _hint(error: BaseException) -> dict[str, object]:
    """Build the SDK hint for an exception event."""
    return {"exc_info": (type(error), error, error.__traceback__)}


@pytest.mark.parametrize(
    ("build", "dropped"),
    [
        pytest.param(lambda: _ExpectedRejection("bad code"), True, id="rejection"),
        pytest.param(
            lambda: _raise_while_handling(
                _ExpectedRejection("bad code"),
                HTTPException(status_code=502),
                explicit_cause=True,
            ),
            True,
            id="handled-502-from-rejection",
        ),
        pytest.param(
            lambda: _raise_while_handling(
                _ExpectedRejection("bad code"),
                OSError("log failed"),
                explicit_cause=False,
            ),
            False,
            id="error-while-handling",
        ),
        pytest.param(
            lambda: _raise_while_handling(
                _ExpectedRejection("bad code"),
                OSError("log failed"),
                explicit_cause=False,
                hide_context=True,
            ),
            False,
            id="error-from-none-while-handling",
        ),
        pytest.param(
            lambda: _raised_from(_ExpectedRejection("bad code")),
            False,
            id="other-error-from-rejection",
        ),
        pytest.param(
            lambda: _raise_while_handling(
                _ExpectedRejection("bad code"),
                HTTPException(status_code=500),
                explicit_cause=True,
            ),
            False,
            id="handled-500-from-rejection",
        ),
        pytest.param(
            lambda: _raise_while_handling(
                _ExpectedRejection("bad code"),
                HTTPException(status_code=502),
                explicit_cause=False,
            ),
            False,
            id="502-with-rejection-only-as-context",
        ),
        pytest.param(
            lambda: _raise_while_handling(
                ValueError("provider down"),
                HTTPException(status_code=502),
                explicit_cause=True,
            ),
            False,
            id="handled-502-from-other-error",
        ),
    ],
)
def test_before_send_drops_only_the_rejection_and_its_handled_502(
    build: Callable[[], BaseException],
    dropped: bool,
) -> None:
    """Only the rejection or the 502 raised from it is dropped."""
    error = build()

    sent = before_send({"level": "error"}, _hint(error))

    assert_that(sent is None).is_equal_to(dropped)


def test_before_send_keeps_and_minimises_other_events() -> None:
    """Other errors are sent, with request headers reduced to the allowlist."""
    wrapped = _raised_from(ValueError("provider down"))
    hint = _hint(wrapped)
    event: Event = {
        "request": {
            "url": "http://testserver/api/v2/ops/metrics",
            "headers": {
                "User-Agent": "agent/1",
                "X-Ops-Key": "ops-secret",
                "Paddle-Signature": "ts=1;h1=abc",
                "X-Request-ID": "req-1",
            },
            "data": {"body": "payload"},
            "cookies": {"podex_session": "cookie"},
        },
    }

    sent = before_send(event, hint)

    if sent is None:
        raise AssertionError("expected the event to be sent")
    assert_that(sent["request"]).is_equal_to(
        {
            "url": "http://testserver/api/v2/ops/metrics",
            "headers": {"User-Agent": "agent/1"},
        },
    )

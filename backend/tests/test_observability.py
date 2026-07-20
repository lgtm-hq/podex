"""Tests for Sentry initialization and PII scrubbing."""

from unittest.mock import patch

from assertpy import assert_that
from sentry_sdk.types import Event

from podex import __version__
from podex.config import Settings
from podex.main import create_app
from podex.observability import init_sentry, scrub_event
from podex.scheduler_runner import main as scheduler_main


def test_init_sentry_noops_without_dsn() -> None:
    """The SDK stays fully disabled when no DSN is configured."""
    settings = Settings(sentry_dsn="")
    with patch("podex.observability.sentry_sdk.init") as mock_init:
        result = init_sentry(settings)
    assert_that(result).is_false()
    mock_init.assert_not_called()


def test_init_sentry_initializes_with_dsn() -> None:
    """A configured DSN initializes the SDK with environment and release."""
    settings = Settings(
        sentry_dsn="https://key@sentry.example/1",
        sentry_environment="staging",
    )
    with patch("podex.observability.sentry_sdk.init") as mock_init:
        result = init_sentry(settings)
    assert_that(result).is_true()
    mock_init.assert_called_once_with(
        dsn="https://key@sentry.example/1",
        environment="staging",
        release=f"podex@{__version__}",
        before_send=scrub_event,
        send_default_pii=False,
    )


def test_init_sentry_defaults_keep_sdk_disabled() -> None:
    """Default settings carry an empty DSN so Sentry never activates."""
    settings = Settings()
    assert_that(settings.sentry_dsn).is_empty()
    assert_that(settings.sentry_environment).is_equal_to("production")


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
    settings = Settings(rate_limit_enabled=False)
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

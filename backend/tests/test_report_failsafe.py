"""Report redaction covers every data field, matches errors exactly, fails safe.

Each test initialises the reporter with ``init_sentry`` (with a configured
secret) and an in-memory transport, reports through the SDK, and inspects
the complete serialised envelope.
"""

import logging
from secrets import token_hex
from typing import cast

import pytest
import sentry_sdk
from assertpy import assert_that
from pydantic import SecretStr
from sentry_sdk.types import Event

from podex import observability
from podex.config import AuthSettings, ObservabilitySettings, Settings
from podex.logging_config import configure_logging
from podex.observability import before_send, init_sentry
from tests.conftest import SentryEnvelopeRecorder
from tests.database_error_support import (
    FOREIGN_KEY_DESCRIPTOR,
    SQLSTATE_DESCRIPTOR,
    foreign_key_violation,
    unique_violation,
)

_KEY = f"report-key-{token_hex(8)}"
_ROW_VALUE = f"row-report-{token_hex(8)}"


@pytest.fixture(name="transport")
def transport_fixture(
    sentry_transport: SentryEnvelopeRecorder,
) -> SentryEnvelopeRecorder:
    """Start the reporter with a configured secret and logging as the app does.

    Returns:
        SentryEnvelopeRecorder: The in-memory envelopes.
    """
    init_sentry(
        Settings(
            auth=AuthSettings(workos_api_key=SecretStr(_KEY)),
            observability=ObservabilitySettings(
                sentry_dsn="https://public@sentry.invalid/1",
            ),
        ),
    )
    configure_logging()
    return sentry_transport


def _report() -> None:
    """Report an error so recorded breadcrumbs and scope data are sent."""
    try:
        raise RuntimeError("closing report")
    except RuntimeError as error:
        sentry_sdk.capture_exception(error)


def test_breadcrumb_tuples_and_sets_are_redacted(
    transport: SentryEnvelopeRecorder,
) -> None:
    """Sequences in breadcrumb data are traversed when the breadcrumb is stored."""
    sentry_sdk.add_breadcrumb(
        category="custom",
        message="stored values",
        data={"detail": (_KEY,), "nested": {"options": frozenset({_KEY})}},
    )
    _report()

    assert_that(transport.serialized()).does_not_contain(_KEY)
    assert_that(transport.serialized()).contains("[redacted]")


def test_database_errors_of_one_class_keep_their_own_descriptor(
    transport: SentryEnvelopeRecorder,
) -> None:
    """Two errors of the same wrapper class are matched one to one."""
    group = ExceptionGroup(
        "batch failed",
        [unique_violation(_ROW_VALUE), foreign_key_violation(_ROW_VALUE)],
    )

    logging.getLogger("podex.tests.report_failsafe").error("batch", exc_info=group)

    values = [
        value["value"]
        for event in transport.events()
        for value in event["exception"]["values"]
        if value["type"] == "IntegrityError"
    ]
    assert_that(values).is_length(2)
    assert_that(sorted(values)).is_equal_to(
        sorted(
            [
                f"{SQLSTATE_DESCRIPTOR} [driver message withheld]",
                f"{FOREIGN_KEY_DESCRIPTOR} [driver message withheld]",
            ],
        ),
    )
    assert_that(transport.serialized()).does_not_contain(_ROW_VALUE)


def test_tags_fingerprint_and_logger_are_redacted(
    transport: SentryEnvelopeRecorder,
) -> None:
    """Free text in tags, the fingerprint and the logger name is rewritten."""
    with sentry_sdk.new_scope() as scope:
        scope.set_tag("note", f"tag {_KEY}")
        scope.fingerprint = ["{{ default }}", _KEY]
        logging.getLogger(f"podex.{_KEY}").error("failed")

    assert_that(transport.events()).is_not_empty()
    assert_that(transport.serialized()).does_not_contain(_KEY)


def test_culprit_and_server_name_are_redacted(
    transport: SentryEnvelopeRecorder,
) -> None:
    """Text fields the SDK may set are redacted; enums stay as they are."""
    del transport  # requested so the reporter holds the configured secret
    event = cast(
        Event,
        {
            "level": "error",
            "culprit": f"in {_KEY}",
            "server_name": f"{_KEY}.internal",
        },
    )

    sent = before_send(event, {})

    if sent is None:
        raise AssertionError("expected the event to be sent")
    assert_that(sent["level"]).is_equal_to("error")
    assert_that(str(sent)).does_not_contain(_KEY)


def test_failing_event_minimisation_sends_a_placeholder(
    transport: SentryEnvelopeRecorder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If minimising fails, a placeholder event is sent, never the original."""

    def failing_scrub(_event: Event, _hint: object) -> Event:
        """Fail inside the reporter hook."""
        raise RuntimeError("scrub broke")

    monkeypatch.setattr(observability, "scrub_event", failing_scrub)
    try:
        raise RuntimeError(f"failed with {_KEY}")
    except RuntimeError as error:
        sentry_sdk.capture_exception(error)

    events = transport.events()
    assert_that(events).is_length(1)
    assert_that(events[0]["level"]).is_equal_to("error")
    assert_that(events[0]["message"]).contains("<unprintable: sanitiser failed on")
    assert_that(events[0]["message"]).contains("RuntimeError")
    assert_that(transport.serialized()).does_not_contain(_KEY)


def test_failing_breadcrumb_minimisation_keeps_a_placeholder(
    transport: SentryEnvelopeRecorder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If minimising a breadcrumb fails, a placeholder replaces it."""

    def failing_allowlist(_data: object) -> None:
        """Fail inside the breadcrumb hook."""
        raise RuntimeError("allowlist broke")

    monkeypatch.setattr(
        observability,
        "_allowlist_headers_recursively",
        failing_allowlist,
    )
    sentry_sdk.add_breadcrumb(category="custom", message=_KEY, data={"k": _KEY})
    _report()

    assert_that(transport.serialized()).does_not_contain(_KEY)
    assert_that(transport.serialized()).contains("<unprintable: sanitiser failed on")

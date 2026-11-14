"""Database error text stays out of every log and report form.

A driver's message can carry row values (PostgreSQL's ``DETAIL:  Key
(value)=(...) already exists.``). These tests fail requests through real
route handlers and log through real loggers, with the reporter initialised
as the application does, and search the captured logs and the complete
serialised envelopes for the row value.
"""

import logging
import sys
import threading
from collections.abc import Callable, Iterator
from secrets import token_hex

import pytest
from assertpy import assert_that
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from podex.logging_config import configure_logging
from tests.conftest import SentryEnvelopeRecorder, create_reporting_app
from tests.database_error_support import SQLSTATE_DESCRIPTOR, unique_violation

_ROW_VALUE = f"row-boundary-{token_hex(8)}"
_PLAIN_DIAGNOSTIC = "batch item 2 had no title"
_logger = logging.getLogger("podex.tests.database_error_boundaries")


def _failing_batch() -> None:
    """Fail as a task group would: a database error beside another error."""
    raise ExceptionGroup(
        "batch failed",
        [unique_violation(_ROW_VALUE), ValueError(_PLAIN_DIAGNOSTIC)],
    )


async def _database_route() -> None:
    """Fail with the database error, as a repository call would."""
    raise unique_violation(_ROW_VALUE)


async def _group_route() -> None:
    """Log and re-raise an exception group holding the database error."""
    try:
        _failing_batch()
    except ExceptionGroup:
        _logger.exception("batch failed")
        raise


@pytest.fixture(name="reporting_client")
def reporting_client_fixture(
    db_session: Session,
    sentry_transport: SentryEnvelopeRecorder,
) -> Iterator[TestClient]:
    """Return a client for a reporting app with two failing routes.

    Yields:
        TestClient: Client whose unhandled errors return 500 responses.
    """
    del sentry_transport  # requested so init_sentry uses the in-memory transport
    app = create_reporting_app(db_session)
    app.add_api_route("/database-failure", _database_route, methods=["GET"])
    app.add_api_route("/group-failure", _group_route, methods=["GET"])
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def _output(transport: SentryEnvelopeRecorder, caplog: pytest.LogCaptureFixture) -> str:
    """Return every serialised envelope followed by the captured logs."""
    assert_that(transport.events()).is_not_empty()
    logs: str = caplog.text
    return transport.serialized() + logs


def test_route_database_error_is_withheld(
    reporting_client: TestClient,
    sentry_transport: SentryEnvelopeRecorder,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unhandled database error in a handler reports its descriptor only."""
    response = reporting_client.get("/database-failure")

    assert_that(response.status_code).is_equal_to(500)
    output = _output(sentry_transport, caplog)
    assert_that(output).does_not_contain(_ROW_VALUE)
    assert_that(output).contains(SQLSTATE_DESCRIPTOR)


def test_route_exception_group_withholds_member_database_errors(
    reporting_client: TestClient,
    sentry_transport: SentryEnvelopeRecorder,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A database error inside an exception group leaks neither way.

    The group's structure and its other members' diagnostics are kept.
    """
    caplog.set_level(logging.DEBUG)

    response = reporting_client.get("/group-failure")

    assert_that(response.status_code).is_equal_to(500)
    output = _output(sentry_transport, caplog)
    assert_that(output).does_not_contain(_ROW_VALUE)
    for expected in (SQLSTATE_DESCRIPTOR, _PLAIN_DIAGNOSTIC, "batch failed"):
        assert_that(output).contains(expected)
    assert_that(caplog.text).contains("Exception Group Traceback")
    types = [
        value["type"]
        for event in sentry_transport.events()
        for value in event["exception"]["values"]
    ]
    for kind in ("ExceptionGroup", "IntegrityError", "ValueError"):
        assert_that(types).contains(kind)


_LogCall = Callable[[logging.Logger], None]


@pytest.mark.parametrize(
    "log_call",
    [
        pytest.param(
            lambda log: log.error(
                "failed: %(error)s",
                {"error": unique_violation(_ROW_VALUE)},
            ),
            id="mapping-argument",
        ),
        pytest.param(
            lambda log: log.error(unique_violation(_ROW_VALUE)),
            id="exception-as-message",
        ),
        pytest.param(
            lambda log: log.error("failed: %s", [unique_violation(_ROW_VALUE)]),
            id="nested-in-list",
        ),
        pytest.param(
            lambda log: log.error(
                "failed: %s",
                {"batch": (unique_violation(_ROW_VALUE),)},
            ),
            id="nested-in-mapping",
        ),
        pytest.param(
            lambda log: log.error("failed", exc_info=_group_error()),
            id="exception-group-traceback",
        ),
    ],
)
def test_logged_forms_withhold_database_text(
    reporter: SentryEnvelopeRecorder,
    caplog: pytest.LogCaptureFixture,
    log_call: _LogCall,
) -> None:
    """Every way a database error reaches a log record is described only."""
    log_call(_logger)

    output = _output(reporter, caplog)
    assert_that(output).does_not_contain(_ROW_VALUE)
    assert_that(output).contains(SQLSTATE_DESCRIPTOR)


def _group_error() -> ExceptionGroup:
    """Return a raised exception group holding the database error."""
    try:
        _failing_batch()
    except ExceptionGroup as group:
        return group
    raise AssertionError("expected an exception group")


def test_cached_traceback_text_is_replaced(
    reporter: SentryEnvelopeRecorder,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Traceback text cached by an earlier record factory is overwritten."""
    original = logging.getLogRecordFactory()

    def caching_factory(*args: object, **kwargs: object) -> logging.LogRecord:
        """Cache the raw traceback at creation, as some integrations do."""
        record = original(*args, **kwargs)
        if record.exc_info and record.exc_info[1] is not None:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        return record

    logging.setLogRecordFactory(caching_factory)
    try:
        configure_logging()
        _logger.error("failed", exc_info=unique_violation(_ROW_VALUE))
    finally:
        logging.setLogRecordFactory(original)

    output = _output(reporter, caplog)
    assert_that(output).does_not_contain(_ROW_VALUE)
    assert_that(caplog.text).contains(SQLSTATE_DESCRIPTOR)


def test_uncaught_errors_are_printed_without_database_text(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Uncaught process and thread errors print the descriptor only."""
    configure_logging()

    error = unique_violation(_ROW_VALUE)
    sys.excepthook(type(error), error, error.__traceback__)
    worker = threading.Thread(target=_failing_batch, name="batch-worker")
    worker.start()
    worker.join()

    printed = capsys.readouterr().err
    assert_that(printed).does_not_contain(_ROW_VALUE)
    for expected in (SQLSTATE_DESCRIPTOR, "Exception in thread batch-worker"):
        assert_that(printed).contains(expected)

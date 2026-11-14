"""Database error text never reaches logs or error reports.

A driver's message can carry row values: PostgreSQL renders a unique
violation as ``DETAIL:  Key (column)=(value) already exists.``, which
``hide_parameters`` does not cover. These tests build such an error without
a database server and log it the ways the application and its server do.
"""

import logging
import sqlite3
import traceback
from secrets import token_hex

import pytest
from assertpy import assert_that
from sqlalchemy.exc import IntegrityError

from podex.database_errors import (
    describe_database_error,
    format_sanitised_exception,
)
from podex.logging_config import configure_logging
from tests.conftest import SentryEnvelopeRecorder
from tests.database_error_support import unique_violation

_ROW_VALUE = f"row-secret-{token_hex(8)}"


def _unique_violation() -> IntegrityError:
    """Return a SQLAlchemy unique violation whose DETAIL reveals the row value."""
    return unique_violation(_ROW_VALUE)


@pytest.fixture(name="logged")
def logged_fixture(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    """Configure logging as the application does, capturing everything.

    Returns:
        pytest.LogCaptureFixture: The capture, at DEBUG.
    """
    configure_logging()
    caplog.set_level(logging.DEBUG)
    return caplog


def test_driver_error_is_described_by_class_and_sqlstate() -> None:
    """The descriptor names the classes and SQLSTATE, never the message."""
    error = _unique_violation()

    assert_that(describe_database_error(error)).is_equal_to(
        "IntegrityError (UniqueViolation, SQLSTATE 23505)",
    )
    assert_that(str(error)).contains(_ROW_VALUE)


def test_sqlite_error_is_described_by_error_name() -> None:
    """SQLite errors are described by their error name."""
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE credentials (value TEXT UNIQUE)")
    connection.execute("INSERT INTO credentials VALUES (?)", (_ROW_VALUE,))
    with pytest.raises(sqlite3.IntegrityError) as caught:
        connection.execute("INSERT INTO credentials VALUES (?)", (_ROW_VALUE,))
    connection.close()

    assert_that(describe_database_error(caught.value)).is_equal_to(
        "IntegrityError (SQLITE_CONSTRAINT_UNIQUE)",
    )


@pytest.mark.parametrize(
    "logger_name",
    ["podex.scheduler_runner", "uvicorn.error", "podex.api.v2.auth"],
)
def test_logged_traceback_withholds_driver_detail(
    logged: pytest.LogCaptureFixture,
    logger_name: str,
) -> None:
    """Every logger writes the traceback without the driver's message."""
    error = _unique_violation()

    logging.getLogger(logger_name).error("tick failed", exc_info=error)

    for withheld in (_ROW_VALUE, "DETAIL"):
        assert_that(logged.text).does_not_contain(withheld)
    for expected in (
        "Traceback (most recent call last)",
        "psycopg.errors.UniqueViolation: UniqueViolation (SQLSTATE 23505)",
        "The above exception was the direct cause of the following exception",
        "sqlalchemy.exc.IntegrityError: IntegrityError (UniqueViolation, "
        "SQLSTATE 23505) [driver message withheld]",
    ):
        assert_that(logged.text).contains(expected)


def test_logged_exception_inside_handler_withholds_driver_detail(
    logged: pytest.LogCaptureFixture,
) -> None:
    """``logger.exception`` at a boundary writes no driver text."""
    try:
        raise RuntimeError("tick failed") from _unique_violation()
    except RuntimeError:
        logging.getLogger("podex.scheduler_runner").exception("scheduler_tick_failed")

    assert_that(logged.text).does_not_contain(_ROW_VALUE)
    assert_that(logged.text).contains("RuntimeError: tick failed")


def test_database_error_log_argument_is_described(
    logged: pytest.LogCaptureFixture,
) -> None:
    """A database error passed as a log argument is replaced by its descriptor."""
    logging.getLogger("podex.api.v2.auth").warning(
        "digest_delivery_failed: %s",
        _unique_violation(),
    )

    assert_that(logged.text).does_not_contain(_ROW_VALUE)
    assert_that(logged.text).contains(
        "digest_delivery_failed: IntegrityError (UniqueViolation, SQLSTATE 23505)",
    )


def _fail(message: str) -> None:
    """Raise a plain error, chaining any error being handled as its context."""
    raise RuntimeError(message)


@pytest.mark.parametrize("explicit_cause", [False, True])
def test_other_tracebacks_are_formatted_as_usual(explicit_cause: bool) -> None:
    """Chains without database errors format exactly as :mod:`traceback` does."""
    chained: BaseException | None = None
    try:
        try:
            raise ValueError("inner")
        except ValueError as inner:
            if explicit_cause:
                raise RuntimeError("outer") from inner
            _fail("outer")
    except RuntimeError as error:
        chained = error
    if chained is None:
        raise AssertionError("expected a chained error")

    assert_that(format_sanitised_exception(chained)).is_equal_to(
        "".join(traceback.format_exception(chained)).rstrip("\n"),
    )


def test_reported_database_error_withholds_driver_detail(
    reporter: SentryEnvelopeRecorder,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The report carries the descriptor, not the driver's message."""
    logging.getLogger("podex.scheduler_runner").error(
        "scheduler_tick_failed",
        exc_info=_unique_violation(),
    )

    events = reporter.events()
    assert_that(events).is_length(1)
    values = [value["value"] for value in events[0]["exception"]["values"]]
    assert_that(values).contains(
        "IntegrityError (UniqueViolation, SQLSTATE 23505) [driver message withheld]",
    )
    assert_that(reporter.serialized() + caplog.text).does_not_contain(
        _ROW_VALUE,
    )

"""The log boundary never raises, never prints the original, always leaves a trace.

Each test logs through a real logger (or calls a real exception hook) with
the boundary installed as the application installs it, and inspects what
was written. A failure inside the sanitiser must not reach the caller, must
not fall back to the original text, and must leave a placeholder naming the
types involved.
"""

import logging
import sys
from collections.abc import Iterator, Mapping
from secrets import token_hex

import pytest
from assertpy import assert_that
from sqlalchemy.exc import IntegrityError

from podex import logging_config
from podex.logging_config import configure_logging
from tests.database_error_support import SQLSTATE_DESCRIPTOR, unique_violation

_ROW_VALUE = f"row-failsafe-{token_hex(8)}"
_PLACEHOLDER = "<unprintable: sanitiser failed on"
_LIMIT = "<unprintable: sanitiser limit reached on"
_logger = logging.getLogger("podex.tests.sanitiser_failsafe")


class _UnprintableError(Exception):
    """An exception whose text cannot be produced."""

    def __str__(self) -> str:
        """Fail, as a broken ``__str__`` does."""
        raise RuntimeError("no text for this error")


class _UnprintableArgument:
    """A value whose text and representation cannot be produced."""

    def __str__(self) -> str:
        """Fail, as a broken ``__str__`` does."""
        raise RuntimeError("no text for this value")

    def __repr__(self) -> str:
        """Fail, as a broken ``__repr__`` does."""
        raise RuntimeError("no representation for this value")


class _UnreadableMapping(Mapping[str, object]):
    """A mapping that cannot be iterated."""

    def __getitem__(self, key: str) -> object:
        """Fail every lookup."""
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        """Fail iteration."""
        raise RuntimeError("cannot iterate")

    def __len__(self) -> int:
        """Claim one item, so logging treats it as a mapping argument."""
        return 1


class _HashableOnce:
    """A key that hashes once, on insertion, and fails afterwards."""

    def __init__(self) -> None:
        self.hashed = False

    def __hash__(self) -> int:
        """Hash once, then fail."""
        if self.hashed:
            raise TypeError("rehashed")
        self.hashed = True
        return 1

    def __repr__(self) -> str:
        """Name the key."""
        return "<key>"


@pytest.fixture(name="logged")
def logged_fixture(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    """Install the boundary as the application does and capture all logs.

    Returns:
        pytest.LogCaptureFixture: The capture, at DEBUG.
    """
    configure_logging()
    caplog.set_level(logging.DEBUG)
    return caplog


def _unprintable_chain(build: str) -> BaseException:
    """Return a raised error whose text fails, caused by a database error."""
    try:
        try:
            raise unique_violation(_ROW_VALUE)
        except IntegrityError as cause:
            if build == "broken-str":
                raise _UnprintableError() from cause
            raise ValueError(_UnprintableArgument()) from cause
    except (_UnprintableError, ValueError) as error:
        return error
    raise AssertionError("expected an error")


@pytest.mark.parametrize("build", ["broken-str", "broken-argument"])
def test_unprintable_exception_in_chain_is_logged_safely(
    logged: pytest.LogCaptureFixture,
    build: str,
) -> None:
    """``logger.exception`` neither raises nor prints driver text."""
    try:
        raise _unprintable_chain(build)
    except (_UnprintableError, ValueError):
        _logger.exception("tick failed")

    assert_that(logged.text).does_not_contain(_ROW_VALUE)
    assert_that(logged.text).contains(SQLSTATE_DESCRIPTOR)
    assert_that(logged.text).contains(_PLACEHOLDER)


def test_unprintable_exception_in_chain_reaches_excepthook_safely(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The process hook prints the sanitised tree, never the original."""
    monkeypatch.setattr(sys, "excepthook", sys.__excepthook__)
    configure_logging()
    error = _unprintable_chain("broken-str")

    sys.excepthook(type(error), error, error.__traceback__)

    printed = capsys.readouterr().err
    assert_that(printed).does_not_contain(_ROW_VALUE)
    assert_that(printed).does_not_contain("Original exception was")
    assert_that(printed).contains(SQLSTATE_DESCRIPTOR)


def test_very_long_chain_is_truncated_with_a_placeholder(
    logged: pytest.LogCaptureFixture,
) -> None:
    """A chain of 1,100 exceptions is logged without recursion or driver text."""
    error: BaseException = unique_violation(_ROW_VALUE)
    for link in range(1_100):
        newer = RuntimeError(f"link {link}")
        newer.__cause__ = error
        error = newer

    _logger.error("deep failure", exc_info=error)

    assert_that(logged.text).does_not_contain(_ROW_VALUE)
    assert_that(logged.text).contains(_LIMIT)


def test_unreadable_mapping_argument_becomes_a_placeholder(
    logged: pytest.LogCaptureFixture,
) -> None:
    """A mapping that cannot be inspected is replaced, and logging succeeds."""
    _logger.error("state: %s", _UnreadableMapping())

    assert_that(logged.text).contains(_PLACEHOLDER)


def test_mapping_without_database_errors_is_not_rebuilt(
    logged: pytest.LogCaptureFixture,
) -> None:
    """Nothing to sanitise means no copy: keys are not hashed again."""
    key = _HashableOnce()
    state = {key: "value-kept"}

    _logger.error("state: %s", state)

    assert_that(logged.text).contains("state: {<key>: 'value-kept'}")


def test_exception_group_argument_withholds_driver_text(
    logged: pytest.LogCaptureFixture,
) -> None:
    """A group holding a database error is sanitised in ``%r`` and ``%s``."""
    group = ExceptionGroup("batch", [unique_violation(_ROW_VALUE)])

    _logger.error("group=%r and %s", group, group)

    assert_that(logged.text).does_not_contain(_ROW_VALUE)
    assert_that(logged.text).contains(SQLSTATE_DESCRIPTOR)


def test_argument_past_the_depth_limit_becomes_a_placeholder(
    logged: pytest.LogCaptureFixture,
) -> None:
    """An uninspected subtree is replaced instead of printed."""
    nested: object = [unique_violation(_ROW_VALUE)]
    for _level in range(6):
        nested = [nested]

    _logger.error("nested: %s", nested)

    assert_that(logged.text).does_not_contain(_ROW_VALUE)
    assert_that(logged.text).contains(_LIMIT)


def test_argument_past_the_item_limit_becomes_a_placeholder(
    logged: pytest.LogCaptureFixture,
) -> None:
    """A very large argument is replaced instead of searched to its end."""
    large: list[object] = [*range(10_000), unique_violation(_ROW_VALUE)]

    _logger.error("batch: %s", large)

    assert_that(logged.text).does_not_contain(_ROW_VALUE)
    assert_that(logged.text).contains(f"{_LIMIT} list>")


def test_failing_sanitiser_leaves_a_placeholder_and_class_names(
    logged: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If sanitising fails, the record is replaced, never left original."""

    def failing_sanitiser(_record: logging.LogRecord) -> None:
        """Fail inside the boundary."""
        raise RuntimeError("sanitiser broke")

    monkeypatch.setattr(logging_config, "sanitise_database_errors", failing_sanitiser)
    error = unique_violation(_ROW_VALUE)

    _logger.error("failed: %s", error, exc_info=error)

    assert_that(logged.text).does_not_contain(_ROW_VALUE)
    assert_that(logged.text).contains(f"{_PLACEHOLDER} str> [IntegrityError")
    assert_that(logged.text).contains("UniqueViolation")


def test_failing_previous_hook_does_not_escape(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A previous hook that fails leaves a placeholder; nothing escapes."""

    def failing_hook(*_args: object) -> None:
        """Fail, as a broken reporter hook would."""
        raise RuntimeError("previous hook broke")

    monkeypatch.setattr(sys, "excepthook", failing_hook)
    configure_logging()
    error = unique_violation(_ROW_VALUE)

    sys.excepthook(type(error), error, error.__traceback__)

    printed = capsys.readouterr().err
    assert_that(printed).does_not_contain(_ROW_VALUE)
    assert_that(printed).contains(_PLACEHOLDER)
    assert_that(printed).contains("IntegrityError")

"""Shared helpers for the third-party SDK log boundary tests."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from secrets import token_hex
from typing import Any

import httpx
import pytest

from podex.logging_config import (
    configure_logging,
    install_log_boundaries,
    redact_secret_values,
)
from tests.timing_support import assert_fast

CREDENTIAL_QUERY_KEYS = (
    "token",
    "access_token",
    "refresh_token",
    "id_token",
    "auth",
    "secret",
    "client_secret",
    "signature",
    "sig",
    "password",
    "key",
    "api_key",
    "apikey",
    "code",
    "X-Amz-Signature",
    "X-Amz-Credential",
    "X-Amz-Security-Token",
)
"""Query keys whose values the boundary masks in logged URLs."""


def synthetic_secret(purpose: str) -> str:
    """Return a unique synthetic secret naming where it was planted.

    Args:
        purpose: Where the secret is placed, such as ``response-header``.

    Returns:
        str: For example ``sdk-response-header-0f1e2d3c4b5a6978``.
    """
    return f"sdk-{purpose}-{token_hex(8)}"


def logged(caplog: pytest.LogCaptureFixture) -> str:
    """Return everything the captured records could print.

    The formatted text (with tracebacks), and each record's message,
    arguments, traceback text and attached exception.

    Args:
        caplog: The capture fixture.

    Returns:
        str: The text to search for secrets.
    """
    parts = [caplog.text]
    for record in caplog.records:
        parts.extend(
            (
                repr(record.msg),
                repr(record.args),
                str(record.exc_text),
                repr(record.exc_info),
            ),
        )
    return "\n".join(parts)


def log_sdk_request(secret: str) -> None:
    """Log an HTTP client request line whose URL carries ``secret``.

    The line is logged at WARNING so that it is written under any
    entrypoint's log level.

    Args:
        secret: Placed in the URL userinfo and a ``token`` query field.
    """
    url = f"https://reader:{secret}@api.example.test/v1/items?token={secret}"
    logging.getLogger("httpx").warning(
        'HTTP Request: %s %s "%s %d %s"',
        "GET",
        httpx.URL(url),
        "HTTP/1.1",
        200,
        "OK",
    )


RECORD_LIMIT_SECONDS = 0.05
"""Longest a bounded record may take to log, on its fastest run."""


def assert_quick_record(
    logger: logging.Logger,
    level: int,
    message: str,
    *arguments: object,
) -> None:
    """Assert that logging one record costs about as much as an ordinary one.

    The record is logged several times (see :func:`assert_fast`); its fastest
    run must stay under :data:`RECORD_LIMIT_SECONDS` and within a fixed
    multiple of an ordinary record logged by the same logger at ``level``.

    Args:
        logger: The library logger the record is sent to.
        level: The record's level.
        message: The record's message or format string.
        *arguments: The record's arguments.
    """
    assert_fast(
        lambda: logger.log(level, message, *arguments),
        baseline=lambda: logger.log(level, "ordinary record %s", "value"),
        limit=RECORD_LIMIT_SECONDS,
    )


SDK_REQUEST_LINE = (
    "HTTP Request: GET https://api.example.test/v1/items"
    '?token=[redacted] "HTTP/1.1 200 OK"'
)
"""What :func:`log_sdk_request` writes once the boundary is installed."""


@contextmanager
def debug_logging(
    caplog: pytest.LogCaptureFixture,
    configured: tuple[str, ...] = (),
) -> Iterator[None]:
    """Configure logging as the application does, at DEBUG, with secrets.

    Args:
        caplog: The capture fixture, set to DEBUG.
        configured: Secret values registered as configured settings values.

    Yields:
        None: While the test runs; the configured values are cleared after.
    """
    configure_logging()
    redact_secret_values(configured)
    caplog.set_level(logging.DEBUG)
    try:
        yield
    finally:
        redact_secret_values(())


@contextmanager
def cached_record_text(exc_text: str, stack_info: str) -> Iterator[None]:
    """Create records that already carry traceback and stack text.

    The boundary is installed around a record factory that sets both, as an
    inner factory could, and the previous factory is restored afterwards.

    Args:
        exc_text: Cached traceback text set on every record.
        stack_info: Stack text set on every record.

    Yields:
        None: While records are created this way.
    """
    previous = logging.getLogRecordFactory()

    def caching_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = logging.LogRecord(*args, **kwargs)
        record.exc_text = exc_text
        record.stack_info = stack_info
        return record

    logging.setLogRecordFactory(caching_factory)
    try:
        install_log_boundaries()
        yield
    finally:
        logging.setLogRecordFactory(previous)


class MemoryTransport:
    """The part of an asyncio transport the server uses; keeps what it writes."""

    def __init__(self) -> None:
        self.written = bytearray()
        self.closed = False

    def get_extra_info(self, name: str, default: Any = None) -> Any:
        """Return fixed local and peer addresses.

        Args:
            name: The requested transport detail.
            default: Returned for any other detail.

        Returns:
            Any: The address, or ``default``.
        """
        addresses = {"sockname": ("127.0.0.1", 8000), "peername": ("127.0.0.1", 5000)}
        return addresses.get(name, default)

    def write(self, data: bytes | bytearray | memoryview) -> None:
        """Keep the written bytes.

        Args:
            data: Bytes the server writes.
        """
        self.written += data

    def close(self) -> None:
        """Mark the transport closed."""
        self.closed = True

    def is_closing(self) -> bool:
        """Return whether the transport was closed.

        Returns:
            bool: True after :meth:`close`.
        """
        return self.closed

    def pause_reading(self) -> None:
        """Accept a request to stop reading; nothing is read from a socket."""

    def resume_reading(self) -> None:
        """Accept a request to resume reading."""

"""Shared helpers for the third-party SDK log boundary tests."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from secrets import token_hex

import httpx
import pytest

from podex.logging_config import configure_logging, redact_secret_values

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


SDK_REQUEST_LINE = (
    "HTTP Request: GET https://[redacted]@api.example.test/v1/items"
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

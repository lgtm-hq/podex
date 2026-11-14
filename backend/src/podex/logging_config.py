"""Structured logging configuration.

Besides the format, :func:`configure_logging` installs the credential
boundaries every log record passes:

- HTTPX request lines lose URL userinfo, fragments and provider key fields.
- Database errors are written by class and SQLSTATE only, wherever they are
  logged (application code, the scheduler, the ASGI server): a driver's text
  can carry row values (``DETAIL:  Key (column)=(value) already exists.``).
- The Spotify SDK's debug lines lose authorization headers and token fields.
"""

import logging
import re
from collections.abc import Callable
from functools import partial
from typing import Any

import httpx

from podex.database_errors import (
    chain_has_database_error,
    describe_database_error,
    format_sanitised_exception,
    is_database_error,
)

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_CREDENTIAL_QUERY_PARAMS = ("api_key", "apikey", "key")
_SPOTIFY_LOGGERS = (
    "spotipy.client",
    "spotipy.oauth2",
    "spotipy.util",
    "spotipy.cache_handler",
)
# Authorization header values ("Basic <base64>", "Bearer <token>") and token
# fields, as the Spotify SDK renders them in its request debug lines.
_AUTHORIZATION_RE = re.compile(
    r"""(["']?authorization["']?\s*[:=]\s*["']?)[^"'\n,}]+""",
    re.IGNORECASE,
)
_TOKEN_FIELD_RE = re.compile(
    r"""(["']?\b(?:client_secret|refresh_token|access_token|code_verifier|code)"""
    r"""["']?\s*[:=]\s*["']?)[^"'\s,}&]+""",
    re.IGNORECASE,
)
_REDACTED = "[redacted]"

RecordFactory = Callable[..., logging.LogRecord]
"""``logging``'s record factory."""


def _safe_httpx_argument(value: object) -> object:
    """Remove credentials from a logged URL without changing requests.

    Drops the userinfo (``user:password@``), the fragment and the provider
    credential query fields; the scheme, host, path and other query fields
    stay for diagnostics.
    """
    if isinstance(value, httpx.URL):
        value = value.copy_with(username=None, password=None, fragment=None)
        for name in _CREDENTIAL_QUERY_PARAMS:
            value = value.copy_remove_param(name)
    return value


def _redact_httpx_credentials(record: logging.LogRecord) -> bool:
    """Keep HTTPX request diagnostics while removing URL credentials."""
    if isinstance(record.args, tuple):
        record.args = tuple(_safe_httpx_argument(value) for value in record.args)
    return True


def mask_authorization(text: str) -> str:
    """Replace authorization header values and token fields in ``text``.

    Args:
        text: A rendered log message.

    Returns:
        str: The message with credentials replaced by ``[redacted]``.
    """
    text = _AUTHORIZATION_RE.sub(rf"\g<1>{_REDACTED}", text)
    return _TOKEN_FIELD_RE.sub(rf"\g<1>{_REDACTED}", text)


def _mask_spotify_authorization(record: logging.LogRecord) -> bool:
    """Mask credentials in the Spotify SDK's request debug lines.

    The SDK renders request headers (``Authorization: Basic`` with the client
    secret, or ``Bearer`` with the access token) into its messages.
    """
    message = record.getMessage()
    masked = mask_authorization(message)
    if masked != message:
        record.msg = masked
        record.args = ()
    return True


def sanitise_database_errors(record: logging.LogRecord) -> None:
    """Withhold database error text from one log record.

    A logged traceback whose chain holds a database error is pre-formatted
    without that error's message (formatters use the cached ``exc_text``);
    ``exc_info`` is kept, so error reporting still sees the exception. Log
    arguments that are database errors are replaced by their descriptor.

    Args:
        record: The record, edited in place.
    """
    exc_info = record.exc_info
    if (
        record.exc_text is None
        and isinstance(exc_info, tuple)
        and isinstance(exc_info[1], BaseException)
        and chain_has_database_error(exc_info[1])
    ):
        record.exc_text = format_sanitised_exception(exc_info[1])
    if isinstance(record.args, tuple) and any(map(is_database_error, record.args)):
        record.args = tuple(
            describe_database_error(value) if is_database_error(value) else value
            for value in record.args
        )


def _sanitising_record(
    inner: RecordFactory, *args: Any, **kwargs: Any
) -> logging.LogRecord:
    """Create a record with the inner factory, then withhold database text."""
    record: logging.LogRecord = inner(*args, **kwargs)
    sanitise_database_errors(record)
    return record


def _install_database_error_boundary() -> None:
    """Sanitise every record at creation, whichever logger emits it, once."""
    current = logging.getLogRecordFactory()
    if isinstance(current, partial) and current.func is _sanitising_record:
        return
    logging.setLogRecordFactory(partial(_sanitising_record, current))


def configure_logging(*, level: str = "INFO") -> None:
    """Configure root logging once for the application."""
    logging.basicConfig(level=level, format=_LOG_FORMAT)
    logging.getLogger("httpx").addFilter(_redact_httpx_credentials)
    for name in _SPOTIFY_LOGGERS:
        logging.getLogger(name).addFilter(_mask_spotify_authorization)
    _install_database_error_boundary()


def get_logger(name: str) -> logging.Logger:
    """Return a named application logger."""
    return logging.getLogger(name)

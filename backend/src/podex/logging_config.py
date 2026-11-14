"""Structured logging configuration.

:func:`install_log_boundaries` installs the credential boundaries every log
record and uncaught exception passes; :func:`configure_logging` also sets the
API's log format. Every process entrypoint installs the boundaries: the API
(``create_app``), the scheduler runner and the migration environment.

- HTTPX request lines lose URL userinfo, fragments and provider key fields.
- Database errors are written by class and SQLSTATE only, wherever they are
  logged or printed (application code, the scheduler, the ASGI server,
  Alembic, uncaught exceptions): a driver's text can carry row values
  (``DETAIL:  Key (column)=(value) already exists.``).
- The Spotify SDK's debug lines lose authorization headers and token fields.

Text formatted into a string before it is logged is outside these
boundaries.
"""

import logging
import re
import sys
import threading
from collections.abc import Callable, Mapping
from functools import partial
from types import TracebackType
from typing import Any, cast

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
_MAX_ARGUMENT_DEPTH = 4

RecordFactory = Callable[..., logging.LogRecord]
"""``logging``'s record factory."""


def _safe_httpx_argument(value: object) -> object:
    """Remove credentials from a logged URL without changing requests.

    Drops the userinfo (``user:password@``), the fragment and the provider
    credential query fields; the scheme, host, path and other query fields
    stay for diagnostics.
    """
    if not isinstance(value, httpx.URL):
        return value
    url: httpx.URL = value.copy_with(username=None, password=None, fragment=None)
    for name in _CREDENTIAL_QUERY_PARAMS:
        url = url.copy_remove_param(name)
    return url


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


def _without_database_errors(value: object, depth: int = 0) -> object:
    """Return ``value`` with database errors replaced by their descriptor.

    Tuples, lists, sets and mapping values are searched to a bounded depth,
    because formatting a container renders its items with ``repr``. A
    container is copied only when something in it changed.
    """
    if is_database_error(value) and isinstance(value, BaseException):
        return describe_database_error(value)
    if depth >= _MAX_ARGUMENT_DEPTH:
        return value
    if isinstance(value, Mapping):
        items = {
            key: _without_database_errors(item, depth + 1)
            for key, item in value.items()
        }
        changed = any(items[key] is not item for key, item in value.items())
        return items if changed else value
    if isinstance(value, tuple | list | set | frozenset):
        members = [_without_database_errors(item, depth + 1) for item in value]
        if all(new is old for new, old in zip(members, value, strict=True)):
            return value
        return tuple(members) if isinstance(value, tuple) else members
    return value


def sanitise_database_errors(record: logging.LogRecord) -> None:
    """Withhold database error text from one log record.

    A logged traceback whose printed tree (chain and exception groups) holds
    a database error is formatted without that error's message, replacing
    any text cached earlier; formatters use the cached ``exc_text``, and
    ``exc_info`` is kept, so error reporting still sees the exception. A
    database error used as the message, as a log argument, as a mapping
    argument's value or nested in a container argument is replaced by its
    descriptor.

    Args:
        record: The record, edited in place.
    """
    exc_info = record.exc_info
    if (
        isinstance(exc_info, tuple)
        and isinstance(exc_info[1], BaseException)
        and chain_has_database_error(exc_info[1])
    ):
        record.exc_text = format_sanitised_exception(exc_info[1])
    record.msg = _without_database_errors(record.msg)
    if record.args:
        record.args = cast(
            "tuple[object, ...] | Mapping[str, object]",
            _without_database_errors(record.args),
        )


def _sanitising_record(
    inner: RecordFactory, *args: Any, **kwargs: Any
) -> logging.LogRecord:
    """Create a record with the inner factory, then withhold database text."""
    record: logging.LogRecord = inner(*args, **kwargs)
    sanitise_database_errors(record)
    return record


def _sanitising_excepthook(
    previous: Callable[..., object],
    kind: type[BaseException],
    error: BaseException,
    trace: TracebackType | None,
) -> None:
    """Print an uncaught exception without database error text."""
    if not chain_has_database_error(error):
        previous(kind, error, trace)
        return
    if sys.stderr is not None:
        sys.stderr.write(format_sanitised_exception(error) + "\n")


def _sanitising_thread_excepthook(
    previous: Callable[[threading.ExceptHookArgs], object],
    args: threading.ExceptHookArgs,
) -> None:
    """Print an uncaught thread exception without database error text."""
    error = args.exc_value
    if error is None or not chain_has_database_error(error):
        previous(args)
        return
    if sys.stderr is not None:
        name = args.thread.name if args.thread is not None else "unknown"
        sys.stderr.write(
            f"Exception in thread {name}:\n{format_sanitised_exception(error)}\n",
        )


def _wraps(installed: object, wrapper: Callable[..., object]) -> bool:
    """Return whether ``installed`` is ``wrapper`` bound to a previous hook."""
    return (
        isinstance(installed, partial) and getattr(installed, "func", None) is wrapper
    )


def _install_database_error_boundary() -> None:
    """Sanitise every record and uncaught exception, whatever emits it, once."""
    factory = logging.getLogRecordFactory()
    if not _wraps(factory, _sanitising_record):
        logging.setLogRecordFactory(partial(_sanitising_record, factory))
    if not _wraps(sys.excepthook, _sanitising_excepthook):
        sys.excepthook = partial(_sanitising_excepthook, sys.excepthook)
    if not _wraps(threading.excepthook, _sanitising_thread_excepthook):
        threading.excepthook = partial(
            _sanitising_thread_excepthook,
            threading.excepthook,
        )


def install_log_boundaries() -> None:
    """Install the credential boundaries without changing log output.

    Every process entrypoint calls this (the API through
    :func:`configure_logging`, the scheduler and the migration environment
    directly), so the boundaries hold whichever process logs or fails.
    """
    logging.getLogger("httpx").addFilter(_redact_httpx_credentials)
    for name in _SPOTIFY_LOGGERS:
        logging.getLogger(name).addFilter(_mask_spotify_authorization)
    _install_database_error_boundary()


def configure_logging(*, level: str = "INFO") -> None:
    """Configure root logging once for the application."""
    logging.basicConfig(level=level, format=_LOG_FORMAT)
    install_log_boundaries()


def get_logger(name: str) -> logging.Logger:
    """Return a named application logger."""
    return logging.getLogger(name)

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

import contextlib
import logging
import re
import sys
import threading
from collections.abc import Callable, Collection, Mapping, Sequence, Set
from functools import partial
from types import TracebackType
from typing import Any

import httpx

from podex.database_errors import (
    chain_has_database_error,
    exception_class_names,
    format_sanitised_exception,
    placeholder,
    stand_in,
    withheld_exception,
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
_MAX_ARGUMENT_ITEMS = 1_000
_SCALAR_TYPES = frozenset({str, int, float, bool, bytes, type(None)})
_FAILED = object()

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


def _limit(value: object) -> str:
    """Return the placeholder for a value past an inspection limit."""
    text: str = placeholder(value, reason="sanitiser limit reached")
    return text


def _clean_collection(
    value: Collection[object], depth: int, budget: list[int]
) -> object:
    """Clean a sequence or set's items; rebuild only if one changed."""
    items: list[object] = []
    changed = False
    for item in value:
        budget[0] -= 1
        if budget[0] < 0:
            return _limit(value)
        cleaned = _clean(item, depth + 1, budget)
        changed = changed or cleaned is not item
        items.append(cleaned)
    if not changed:
        return value
    return tuple(items) if isinstance(value, tuple) else items


def _clean_mapping(
    value: Mapping[object, object], depth: int, budget: list[int]
) -> object:
    """Clean a mapping's keys and values; rebuild only if one changed."""
    pairs: list[tuple[object, object]] = []
    changed = False
    for key, item in value.items():
        budget[0] -= 1
        if budget[0] < 0:
            return _limit(value)
        cleaned_key = _clean(key, depth + 1, budget)
        cleaned_item = _clean(item, depth + 1, budget)
        changed = changed or cleaned_key is not key or cleaned_item is not item
        pairs.append((cleaned_key, cleaned_item))
    return dict(pairs) if changed else value


def _clean(value: object, depth: int, budget: list[int]) -> object:
    """Return ``value`` with database errors withheld, or ``value`` itself.

    Formatting renders containers' items with ``repr``, so sequences, sets
    and mappings are searched; a container is rebuilt only when an item
    changed. An exception whose printed tree holds a database error
    (including an exception group) becomes a sanitised stand-in. Past
    :data:`_MAX_ARGUMENT_DEPTH` levels or :data:`_MAX_ARGUMENT_ITEMS` items, a
    container becomes a placeholder; if inspecting it fails, too.
    """
    if type(value) in _SCALAR_TYPES:
        return value
    if isinstance(value, BaseException):
        return withheld_exception(value) if chain_has_database_error(value) else value
    is_mapping = isinstance(value, Mapping)
    is_collection = isinstance(value, Sequence | Set) and not isinstance(
        value, str | bytes | bytearray
    )
    if not (is_mapping or is_collection):
        return value
    if depth >= _MAX_ARGUMENT_DEPTH:
        return _limit(value)
    cleaned: object = _FAILED
    with contextlib.suppress(Exception):
        if isinstance(value, Mapping):
            cleaned = _clean_mapping(value, depth, budget)
        elif isinstance(value, Sequence | Set):
            cleaned = _clean_collection(value, depth, budget)
    return placeholder(value) if cleaned is _FAILED else cleaned


def sanitise_database_errors(record: logging.LogRecord) -> None:
    """Withhold database error text from one log record.

    A logged traceback whose printed tree (chain and exception groups) holds
    a database error is formatted without that error's message, replacing
    any text cached earlier; formatters use the cached ``exc_text``, and
    ``exc_info`` is kept, so error reporting still sees the exception. A
    database error (or an exception group holding one) used as the message,
    as a log argument, as a mapping argument's key or value or nested in a
    container argument is replaced by a sanitised stand-in.

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
    budget = [_MAX_ARGUMENT_ITEMS]
    record.msg = _clean(record.msg, 0, budget)
    arguments = record.args
    if isinstance(arguments, tuple):
        cleaned: list[object] = []
        for argument in arguments:
            budget[0] -= 1
            cleaned.append(
                _clean(argument, 1, budget) if budget[0] >= 0 else _limit(argument)
            )
        if any(new is not old for new, old in zip(cleaned, arguments, strict=True)):
            record.args = tuple(cleaned)
    elif isinstance(arguments, Mapping):
        mapping = _clean(arguments, 0, budget)
        if isinstance(mapping, Mapping):
            record.args = mapping
        else:
            record.msg, record.args = mapping, ()


def _replace_with_placeholder(record: logging.LogRecord) -> None:
    """Replace a record's content after sanitising it failed.

    The message, arguments and traceback text become placeholders naming the
    message type and the exception classes; nothing original is printed.
    """
    error = record.exc_info[1] if isinstance(record.exc_info, tuple) else None
    classes = exception_class_names(error) if isinstance(error, BaseException) else []
    record.msg = placeholder(record.msg, classes=classes)
    record.args = ()
    if isinstance(error, BaseException):
        record.exc_text = placeholder(error, classes=classes)


def _sanitising_record(
    inner: RecordFactory, *args: Any, **kwargs: Any
) -> logging.LogRecord:
    """Create a record with the inner factory, then withhold database text.

    Never raises from sanitising: on any failure the record's content is
    replaced by placeholders instead.
    """
    record: logging.LogRecord = inner(*args, **kwargs)
    sanitised = False
    with contextlib.suppress(Exception):
        sanitise_database_errors(record)
        sanitised = True
    if not sanitised:
        _replace_with_placeholder(record)
    return record


def _write_stderr(text: str) -> None:
    """Write a line to standard error, if there is one; never raises."""
    with contextlib.suppress(Exception):
        if sys.stderr is not None:
            sys.stderr.write(text + "\n")


def _shown(error: BaseException) -> BaseException:
    """Return the exception to pass on: ``error`` or its sanitised stand-in."""
    shown: BaseException | None = None
    with contextlib.suppress(Exception):
        shown = withheld_exception(error) if chain_has_database_error(error) else error
    if shown is None:
        shown = stand_in(
            error, placeholder(error, classes=exception_class_names(error))
        )
    return shown


def _sanitising_excepthook(
    previous: Callable[..., object],
    kind: type[BaseException],
    error: BaseException,
    trace: TracebackType | None,
) -> None:
    """Pass an uncaught exception on without database error text.

    The previous hook (the interpreter's printer, or an error reporter's
    hook) receives a sanitised stand-in tree when the exception holds a
    database error, so it still prints and reports. Never raises: if the
    previous hook fails, a placeholder naming the classes is printed.
    """
    shown = _shown(error) if isinstance(error, BaseException) else error
    delivered = False
    with contextlib.suppress(Exception):
        if shown is error:
            previous(kind, error, trace)
        else:
            previous(type(shown), shown, shown.__traceback__)
        delivered = True
    if not delivered:
        _write_stderr(placeholder(error, classes=exception_class_names(shown)))


def _sanitising_thread_excepthook(
    previous: Callable[[threading.ExceptHookArgs], object],
    args: threading.ExceptHookArgs,
) -> None:
    """Pass an uncaught thread exception on without database error text."""
    error = args.exc_value
    shown = _shown(error) if isinstance(error, BaseException) else error
    delivered = False
    with contextlib.suppress(Exception):
        if shown is not error and isinstance(shown, BaseException):
            args = threading.ExceptHookArgs(
                [type(shown), shown, shown.__traceback__, args.thread],
            )
        previous(args)
        delivered = True
    if not delivered:
        _write_stderr(placeholder(shown))


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
